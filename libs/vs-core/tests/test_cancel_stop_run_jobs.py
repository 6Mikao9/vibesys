"""A cancel stop cancels the jobs the run itself owns; a drain stop waits for them.

Attempt closure drains an attempt's jobs, but a measurement the run submits in its own
scope (the input baseline) has no attempt to close. Without a run-level cancel, a run
stopped with ``StopMode.CANCEL`` while that job is queued stays CLOSING until the job
ends on its own, which a held cluster job never does.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hypothesis import given, settings
from hypothesis import strategies as st

import vs_core.api as core
from vs_sim.api.testing import examples

from .test_measurements import committed, observation, plan, requested

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

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
    jobs: Mapping[core.ResourceId, core.SubmitMeasurement],
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
        if isinstance(request, core.CancelOwnedJob) and request.resource_id in jobs:
            ended = observation(
                jobs[request.resource_id],
                resource_id=request.resource_id,
                sequence=len(sent) + 10,
                status=core.ObservationStatus.CANCELLED,
                terminal=True,
                released=True,
                children_complete=True,
                observed_at=now_at,
            )
            result = core.step(
                result.state, core.JobObserved(resource_id=request.resource_id, observation=ended)
            )
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
    state = _settle(result.state, result.requests, 2.0, sent, {_JOB: job})
    if not accepted_before_stop:
        state, requests = _accept(state, job)
        state = _settle(state, requests, 2.5, sent, {_JOB: job})
    for tick in ticks:
        result = core.step(state, core.ClockAdvanced(now_at=tick))
        state = _settle(result.state, result.requests, tick, sent, {_JOB: job})
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


def _reload(state: core.CoreState) -> core.CoreState:
    """A host restart: the whole state through the envelope codec."""
    envelope = core.RunEnvelope[core.StrategyState](
        schema_version=core.ENVELOPE_SCHEMA_VERSION,
        fence=core.HostFence(host_id=core.HostId(root="host"), epoch=1),
        strategy_id=state.run.declaration.strategy_id,
        state_schema=state.run.declaration.state_schema,
        core=state,
        strategy=core.StrategyState(schema_version=1),
        event_cursor=core.EventCursor(sequence=0),
    )
    codec = core.OperationRegistry()
    return codec.decode_envelope(
        core.RunEnvelope[core.StrategyState], codec.encode_envelope(envelope)
    ).core


def test_a_late_observation_does_not_ask_again_for_the_inspection_a_stop_asked_for() -> None:
    """A stop that finds the job unconfirmed inspects it; the next observation of it at the
    same sequence is no second, differently attributed, inspection.

    The inspection's id names the job's step. The stop stamped it with its own decision and
    the observation then asked for the same id under the measurement's decision, which the
    request ledger rejects as an identity conflict and halts the shell.
    """
    asked = requested()
    job = asked.requests[0]
    assert isinstance(job, core.SubmitMeasurement)
    assert job.request_id is not None
    state = core.step(asked.state, core.DispatchAuthorized(request_id=job.request_id)).state
    unconfirmed = observation(
        job, status=core.ObservationStatus.UNKNOWN, diagnostic="executor holds no record"
    )
    state = committed(state, unconfirmed)
    state = core.step(state, core.MeasurementSubmissionObserved(observation=unconfirmed)).state
    state = core.step(state, core.JobObserved(resource_id=_JOB, observation=unconfirmed)).state
    stopped = core.step(state, _stop(core.StopMode.CANCEL, 2.0))
    assert [type(r) for r in stopped.requests] == [core.InspectOwnedJob]
    again = core.step(stopped.state, core.JobObserved(resource_id=_JOB, observation=unconfirmed))
    assert again.requests == ()


_QUEUE_WAIT_LIMIT = 8.0

_STATUSES = (
    ("unknown", core.ObservationStatus.UNKNOWN, False),
    ("unknown-accepted", core.ObservationStatus.UNKNOWN, True),
    ("pending", core.ObservationStatus.PENDING, True),
)

_OPS = st.one_of(
    st.tuples(st.just("tick"), st.sampled_from((0.0, 0.0, 1.0, 3.0))),
    st.tuples(st.just("observe"), st.integers(0, 2), st.sampled_from(_STATUSES)),
    st.tuples(st.just("stop"), st.sampled_from((core.StopMode.CANCEL, core.StopMode.DRAIN))),
    st.tuples(st.just("replay"), st.integers(0, 2)),
    st.tuples(st.just("restart")),
)


class _Run:
    """The run's own measurement jobs, driven by hand: the events the shell would feed core.

    Core refuses a second measurement of a plan the scope already measures, so each job
    measures a plan of its own (a distinct evaluator).
    """

    def __init__(self, count: int) -> None:
        self.state = core.initial_state()
        self.jobs: dict[core.ResourceId, core.SubmitMeasurement] = {}
        for index in range(count):
            asked = requested(
                self.state,
                identity=f"measure-{index}",
                measurement=plan(evaluator_digest=f"evaluator-{index}"),
            )
            job = asked.requests[0]
            assert isinstance(job, core.SubmitMeasurement)
            assert job.request_id is not None
            self.state = core.step(
                asked.state, core.DispatchAuthorized(request_id=job.request_id)
            ).state
            self.jobs[core.ResourceId(root=f"job-{index}")] = job
        self.resources = list(self.jobs)
        self.sent: list[core.Request] = []
        self.now = 1.0
        self.mode: core.StopMode | None = None
        self.sequences = dict.fromkeys(self.jobs, 0)
        self.submitted: set[core.ResourceId] = set()
        self.last: dict[core.ResourceId, tuple[core.Observation, bool]] = {}

    def _step(self, event: core.CoreEvent) -> None:
        result = core.step(self.state, event)
        self.state = result.state
        self.sent.extend(result.requests)

    def tick(self, seconds: float) -> None:
        """Let time pass, but not past the queue allowance (10 s): an unconfirmed job that
        outlives it is cancelled by core itself, which is not the stop under test."""
        if self.now + seconds > _QUEUE_WAIT_LIMIT:
            return
        self.now += seconds
        self._step(core.ClockAdvanced(now_at=self.now))

    def observe(
        self,
        index: int,
        status: core.ObservationStatus,
        *,
        accepted: bool,
        **updates: object,
    ) -> None:
        resource = self.resources[index % len(self.resources)]
        self.sequences[resource] += 1
        seen = observation(
            self.jobs[resource],
            sequence=self.sequences[resource],
            resource_id=resource,
            status=status,
            accepted=accepted,
            observed_at=self.now,
            **updates,
        )
        self.deliver(resource, seen, accepted=accepted)

    def deliver(self, resource: core.ResourceId, seen: core.Observation, *, accepted: bool) -> None:
        self.last[resource] = (seen, accepted)
        self.state = committed(self.state, seen)
        if accepted and resource not in self.submitted:
            self.submitted.add(resource)
            self._step(core.MeasurementSubmissionObserved(observation=seen))
        self._step(core.JobObserved(resource_id=resource, observation=seen))

    def replay(self, index: int) -> None:
        """The last observation again: a poll answered twice, or replayed after a restart."""
        resource = self.resources[index % len(self.resources)]
        if resource in self.last:
            seen, accepted = self.last[resource]
            self.deliver(resource, seen, accepted=accepted)

    def stop(self, mode: core.StopMode) -> None:
        if self.mode is None:
            self.mode = mode
            self._step(_stop(mode, self.now))

    def apply(self, op: tuple[object, ...]) -> None:
        match op:
            case ("tick", float(seconds)):
                self.tick(seconds)
            case ("observe", int(index), (_, core.ObservationStatus() as status, bool(accepted))):
                self.observe(index, status, accepted=accepted)
            case ("stop", core.StopMode() as mode):
                self.stop(mode)
            case ("replay", int(index)):
                self.replay(index)
            case ("restart",):
                self.state = _reload(self.state)

    def confirm_and_settle(self) -> None:
        """The executor confirms every job at last, and answers whatever core asked."""
        for index in range(len(self.resources)):
            self.observe(index, core.ObservationStatus.PENDING, accepted=True)
        self.state = _settle(self.state, self._unanswered(), self.now, [], self.jobs)
        self.settle_over((5.0, 10.0, 70.0))

    def end_jobs(self) -> None:
        """Every job finishes by itself."""
        for resource, job in self.jobs.items():
            self.sequences[resource] += 1
            ended = observation(
                job,
                sequence=self.sequences[resource],
                resource_id=resource,
                status=core.ObservationStatus.SUCCEEDED,
                terminal=True,
                released=True,
                children_complete=True,
                observed_at=self.now,
            )
            self._step(core.RequestObserved(observation=ended))
            self._step(core.JobObserved(resource_id=resource, observation=ended))
        self.state = _settle(self.state, self._unanswered(), self.now, [], self.jobs)
        self.settle_over((5.0, 10.0))

    def settle_over(self, laters: tuple[float, ...]) -> None:
        """Let the clock run on, answering what core asks at each tick."""
        for later in laters:
            self.now += later
            result = core.step(self.state, core.ClockAdvanced(now_at=self.now))
            self.state = _settle(result.state, result.requests, self.now, self.sent, self.jobs)

    def _unanswered(self) -> list[core.Request]:
        """Each request core sent so far, once: the ones the shell has yet to answer."""
        once = {r.request_id: r for r in self.sent if r.request_id is not None}
        return [
            r
            for r in once.values()
            if any(
                row.request_id == r.request_id and row.phase == core.RequestPhase.PREPARED
                for row in self.state.records.records
            )
        ]

    def cancels(self, resource: core.ResourceId) -> int:
        return sum(
            isinstance(r, core.CancelOwnedJob) and r.resource_id == resource for r in self.sent
        )


@settings(max_examples=examples(pr=25, nightly=500), deadline=None, database=None)
@given(count=st.integers(1, 3), ops=st.lists(_OPS, max_size=16))
def test_polls_stops_and_observations_at_shared_instants_never_conflict(
    count: int, ops: list[tuple[object, ...]]
) -> None:
    """Whatever order polls, stops, observations, replays and restarts come in, at the same
    instant or not, on one to three run-owned jobs, core never issues one request id with two
    payloads and cancels each job at most once. A cancel stop closes the run with every job
    ended; a drain stop cancels nothing, keeps the run CLOSING while a job lives, and closes
    it once every job finishes.
    """
    run = _Run(count)
    for op in ops:
        if run.state.run.status == core.RunStatus.TERMINAL:
            break
        run.apply(op)
    run.confirm_and_settle()
    assert all(run.cancels(resource) <= 1 for resource in run.resources)
    match run.mode:
        case core.StopMode.CANCEL:
            assert run.state.run.status == core.RunStatus.TERMINAL
            assert all(run.cancels(resource) == 1 for resource in run.resources)
        case core.StopMode.DRAIN:
            assert all(run.cancels(resource) == 0 for resource in run.resources)
            assert run.state.run.status == core.RunStatus.CLOSING
            run.end_jobs()
            assert run.state.run.status == core.RunStatus.TERMINAL
