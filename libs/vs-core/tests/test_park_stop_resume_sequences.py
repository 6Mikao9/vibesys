"""Durable submitted work survives mixed operator controls and evaluation completion."""

from collections.abc import Sequence
from typing import Literal

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

import vs_core.api as core

from .test_measurements import observation, plan, requested

Action = Literal["park", "stop", "terminate", "resume", "evaluate"]


class Run:
    """Pure request/observation interpreter; a park cancellation releases only physical work."""

    def __init__(self, count: int) -> None:
        self.state = core.step(core.initial_state(), core.EpochResumed(epoch=1, now_at=0)).state
        self.serial = 0
        for index in range(count):
            result = requested(
                self.state,
                identity=f"attempt-{index}",
                measurement=plan(candidate=core.RevisionRef.of_git_commit(f"{index:040x}")),
            )
            self.state = result.state
            submit = next(r for r in result.requests if isinstance(r, core.SubmitMeasurement))
            assert submit.request_id is not None
            self.feed(core.DispatchAuthorized(request_id=submit.request_id))
            observed = observation(
                submit,
                resource_id=core.ResourceId(root=f"job-{index}"),
                children_complete=True,
                observed_at=0.0,
                event_id=core.EventId(root=f"submitted-{index}"),
            )
            self.feed(core.RequestObserved(observation=observed))
            self.feed(core.MeasurementSubmissionObserved(observation=observed))
            assert observed.resource_id is not None
            self.feed(core.JobObserved(resource_id=observed.resource_id, observation=observed))
        self.plans = {job.submission_id: job.plan for job in self.state.evaluation.jobs}
        assert len(self.plans) == count

    def feed(self, event: core.CoreEvent) -> None:
        self.state = core.step(self.state, event).state

    def settle(self) -> None:
        for _ in range(256):
            row = next(
                (
                    row
                    for row in self.state.records.records
                    if row.phase is core.RequestPhase.PREPARED
                    and isinstance(
                        row.request,
                        core.CancelOwnedJob | core.CollectEvidence | core.InspectRequest,
                    )
                ),
                None,
            )
            if row is None:
                return
            self.feed(core.DispatchAuthorized(request_id=row.request_id))
            self.serial += 1
            own = core.Observation(
                event_id=core.EventId(root=f"settled-{self.serial}"),
                request_id=row.request_id,
                scope=row.request.scope,
                admission_id=row.request.admission_id,
                sequence=(0 if row.sequence is None else row.sequence) + 1,
                observed_at=self.state.run.now_at,
                status=core.ObservationStatus.SUCCEEDED,
                accepted=True,
                terminal=True,
                released=True,
                children_complete=True,
                resource_id=row.request.resource_id
                if isinstance(row.request, core.CancelOwnedJob)
                else None,
            )
            target = None
            if isinstance(row.request, core.InspectRequest):
                source = self.state.records.record(row.request.target)
                assert source is not None
                assert source.observation is not None
                target = core.TargetObservation(observation=source.observation)
            self.feed(core.RequestObserved(observation=own, target=target))
        message = "cleanup or recovery did not converge"
        raise AssertionError(message)

    def act(self, action: Action) -> None:
        self.serial += 1
        if action == "resume":
            self.feed(
                core.EpochResumed(
                    epoch=self.state.records.recovery.epoch + 1, now_at=self.state.run.now_at
                )
            )
        elif action == "evaluate":
            self.evaluate()
        elif action == "park":
            if self.state.run.park is None:
                self.control("park")
        else:
            # Termination during park enters the cancelling-stop transition
            # delivered by the signal shell; this property exercises that drain.
            assert self.state.run.park is not None
            self.control(
                "stop", core.StopMode.CANCEL if action == "terminate" else core.StopMode.DRAIN
            )
        self.settle()
        self.state = core.CoreState.model_validate_json(self.state.model_dump_json())
        assert {job.submission_id: job.plan for job in self.state.evaluation.jobs} == self.plans
        assert self.state.run.result is None

    def control(self, action: Literal["park", "stop"], mode: core.StopMode | None = None) -> None:
        self.feed(
            core.RunControlEvent(
                control=core.ControlInput(
                    control_id=core.ControlId(root=f"{action}-{self.serial}"),
                    action=action,
                    mode=mode,
                ),
                now_at=self.state.run.now_at,
            )
        )

    def evaluate(self) -> None:
        if self.state.run.status is core.RunStatus.PARKED:
            return
        job = next((job for job in self.state.evaluation.jobs if not job.terminal), None)
        if job is None:
            return
        assert job.observation is not None
        assert job.resource_id is not None
        self.feed(
            core.JobObserved(
                resource_id=job.resource_id,
                observation=job.observation.model_copy(
                    update={
                        "event_id": core.EventId(root=f"evaluated-{self.serial}"),
                        "sequence": job.observation.sequence + 1,
                        "status": core.ObservationStatus.SUCCEEDED,
                        "terminal": True,
                        "released": True,
                    }
                ),
            )
        )


def lifecycle_sequences() -> st.SearchStrategy[list[Action]]:
    """Each host parks, handles controls/results, exits, then explicitly resumes."""
    controls: tuple[Action, ...] = ("park", "stop", "terminate", "evaluate")

    def joined(epochs: Sequence[Sequence[Action]]) -> list[Action]:
        sequence: list[Action] = []
        for epoch in epochs:
            sequence.append("park")
            sequence.extend(epoch)
            sequence.extend(("terminate", "resume"))
        return sequence

    return st.lists(st.lists(st.sampled_from(controls), max_size=8), min_size=1, max_size=4).map(
        joined
    )


@pytest.mark.regression
@example(actions=["park", "stop", "terminate", "resume", "evaluate"], count=17)
@example(actions=["park", "evaluate", "resume", "park", "stop", "resume"], count=2)
@given(actions=lifecycle_sequences(), count=st.integers(min_value=1, max_value=5))
def test_no_submitted_attempt_is_lost_and_every_control_sequence_can_resume(
    actions: list[Action],
    count: int,
) -> None:
    run = Run(count)
    for action in actions:
        run.act(action)
    run.act("park")
    run.act("terminate")
    assert run.state.run.status is core.RunStatus.PARKED
    run.act("resume")
    assert run.state.records.recovery.phase is core.RecoveryPhase.READY
    for _ in range(count):
        run.act("evaluate")
    run.act("park")
    assert run.state.run.status is core.RunStatus.PARKED
    assert all(job.terminal for job in run.state.evaluation.jobs)
