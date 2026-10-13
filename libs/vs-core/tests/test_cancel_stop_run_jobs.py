"""A cancel stop cancels the jobs the run itself owns; a drain stop waits for them.

Attempt closure drains an attempt's jobs, but a measurement the run submits in its own
scope (the input baseline) has no attempt to close. Without a run-level cancel, a run
stopped with ``StopMode.CANCEL`` while that job is queued stays CLOSING until the job
ends on its own, which a held cluster job never does.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hypothesis import given
from hypothesis import strategies as st

import vs_core.api as core

from .test_measurements import committed, observation, requested

if TYPE_CHECKING:
    from collections.abc import Iterable

_JOB = core.ResourceId(root="job")


def _stop(mode: core.StopMode, now_at: float) -> core.RunControlEvent:
    return core.RunControlEvent(
        control=core.ControlInput(control_id=core.ControlId(root="stop"), action="stop", mode=mode),
        result=core.RunResultProposal(outcome="cancelled", reason="operator stop"),
        now_at=now_at,
    )


def _answer(request: core.Request, now_at: float) -> core.Observation:
    """A well-behaved executor: a cancel ends the job, anything else simply succeeds."""
    cancelled = isinstance(request, core.CancelOwnedJob)
    request_id = request.request_id or core.RequestId(root="unnamed")
    return core.Observation(
        event_id=core.EventId(root=f"answer:{request_id.root}"),
        request_id=request_id,
        scope=request.scope,
        sequence=0,
        observed_at=now_at,
        status=core.ObservationStatus.CANCELLED if cancelled else core.ObservationStatus.SUCCEEDED,
        resource_id=request.resource_id if cancelled else None,
        accepted=True,
        terminal=True,
        released=True,
        children_complete=True,
        admission_id=request.admission_id,
    )


def _settle(
    state: core.CoreState,
    requests: Iterable[core.Request],
    now_at: float,
    sent: list[core.Request],
    job: core.SubmitMeasurement,
) -> core.CoreState:
    """Authorize and answer every request core sends, until it sends no more.

    A cancel's answer also reports the job itself ended, as the runtime's does.
    """
    pending = list(requests)
    for _ in range(50):
        if not pending:
            break
        request = pending.pop(0)
        sent.append(request)
        if request.request_id is None:
            continue
        state = core.step(state, core.DispatchAuthorized(request_id=request.request_id)).state
        result = core.step(state, core.RequestObserved(observation=_answer(request, now_at)))
        pending.extend(result.requests)
        if isinstance(request, core.CancelOwnedJob):
            ended = observation(
                job,
                sequence=len(sent) + 10,
                status=core.ObservationStatus.CANCELLED,
                terminal=True,
                released=True,
                children_complete=True,
                observed_at=now_at,
            )
            result = core.step(result.state, core.JobObserved(resource_id=_JOB, observation=ended))
            pending.extend(result.requests)
        state = result.state
    return state


def _accept(
    state: core.CoreState, job: core.SubmitMeasurement
) -> tuple[core.CoreState, tuple[core.Request, ...]]:
    """The executor accepts the submission as job ``job``, still queued."""
    accepted = observation(job, children_complete=True)
    state = committed(state, accepted)
    submission = core.step(state, core.MeasurementSubmissionObserved(observation=accepted))
    observed = core.step(submission.state, core.JobObserved(resource_id=_JOB, observation=accepted))
    return observed.state, (*submission.requests, *observed.requests)


def _stopped(
    mode: core.StopMode, *, accepted_before_stop: bool, ticks: tuple[float, ...]
) -> tuple[core.CoreState, list[core.Request]]:
    """Stop a run whose own measurement job is queued, then drive it as the shell would.

    The job is accepted before the stop, or only after it: a submission already sent when
    the stop commits is answered later, and its job must still be cancelled.
    """
    asked = requested()
    job = asked.requests[0]
    assert isinstance(job, core.SubmitMeasurement)
    assert job.request_id is not None
    state = core.step(asked.state, core.DispatchAuthorized(request_id=job.request_id)).state
    sent: list[core.Request] = []
    if accepted_before_stop:
        state, _ = _accept(state, job)
    result = core.step(state, _stop(mode, 2.0))
    state = _settle(result.state, result.requests, 2.0, sent, job)
    if not accepted_before_stop:
        state, requests = _accept(state, job)
        state = _settle(state, requests, 2.5, sent, job)
    for tick in ticks:
        result = core.step(state, core.ClockAdvanced(now_at=tick))
        state = _settle(result.state, result.requests, tick, sent, job)
    return state, sent


def _cancels(sent: Iterable[core.Request]) -> int:
    return sum(isinstance(r, core.CancelOwnedJob) and r.resource_id == _JOB for r in sent)


def test_a_cancel_stop_cancels_the_runs_queued_measurement_and_closes() -> None:
    state, sent = _stopped(core.StopMode.CANCEL, accepted_before_stop=True, ticks=(4.0,))
    assert _cancels(sent) == 1
    assert state.run.status == core.RunStatus.TERMINAL


@given(
    accepted_before_stop=st.booleans(),
    ticks=st.lists(st.floats(min_value=3.0, max_value=50.0), max_size=3).map(
        lambda xs: tuple(sorted(xs))
    ),
)
def test_a_cancel_stop_cancels_the_runs_own_jobs_once_and_closes(
    *, accepted_before_stop: bool, ticks: tuple[float, ...]
) -> None:
    state, sent = _stopped(
        core.StopMode.CANCEL, accepted_before_stop=accepted_before_stop, ticks=(*ticks, 60.0)
    )
    assert _cancels(sent) == 1
    assert state.run.status == core.RunStatus.TERMINAL


@given(accepted_before_stop=st.booleans())
def test_a_drain_stop_lets_the_runs_own_job_finish(*, accepted_before_stop: bool) -> None:
    state, sent = _stopped(
        core.StopMode.DRAIN, accepted_before_stop=accepted_before_stop, ticks=(4.0, 20.0)
    )
    assert _cancels(sent) == 0
    assert state.run.status == core.RunStatus.CLOSING
