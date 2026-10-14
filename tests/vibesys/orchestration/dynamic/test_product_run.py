"""The dynamic search wired to the production shell: a few whole runs simulated by vs-dst.

The flow stories pin each decision and `tests/system` faults every request kind with the
skeleton search; these runs show only what the wiring adds. A clean run ends live, executes no
request twice and repeats none; every turn it asks is given the prompt a render operation made
for it and is bounded by the run deadline; and a measurement or a turn that nothing answers, or
a candidate whose job is still running at a stop, still ends the run terminal.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from enum import StrEnum
from typing import TYPE_CHECKING, Any, TypedDict

from tests.support.dynamic_world import DynamicWorld
from tests.vibesys.orchestration.dynamic._replies import (
    implement,
    implemented,
    plan_reply,
    reviewed,
)

from vibesys.orchestration.dynamic.search.api import OPERATIONS, RenderRoleArtifacts
from vs_core.api import (
    AttemptPhase,
    ControlId,
    ControlInput,
    DispatchTurn,
    ExecuteRegisteredOperation,
    MeasurementFailure,
    ObserveOwnedJob,
    OwnedJob,
    ResourceId,
    ResumeSessionTurn,
    RunControlEvent,
    StopMode,
    SubmitMeasurement,
)
from vs_core.testing.drive import Failed, Retryable, Running, Succeeded, Unknown
from vs_core.testing.liveness import Journal, spin_violations
from vs_dst.api import SimulatedRun, simulate

if TYPE_CHECKING:
    from collections.abc import Sequence

    from vs_core.api import CoreState, Request
    from vs_core.testing.drive import Answer
    from vs_dst.api import Enact
    from vs_runtime.api.core import CoreRuntime, ExecutionResult
    from vs_sim.api.testing import VirtualClock

WORKSTREAMS = 2


class Script(TypedDict, total=False):
    """What the agents of a run reply and how wide its search is (see `DynamicWorld`)."""

    planner: Sequence[str]
    implementer: Sequence[str]
    judge: Sequence[str]
    max_in_flight: int


class Hosted(DynamicWorld):
    """The dynamic world at a concurrency cap and under a run deadline of the test's choosing."""

    def __init__(
        self, script: Script | None = None, *, cap: int = 1, deadline_at: float | None = None
    ) -> None:
        """Script a run as `DynamicWorld` does; ``cap`` and ``deadline_at`` set the host."""
        super().__init__(**(script or {}))
        harness = self.setup.harness
        if deadline_at is not None:
            harness = replace(harness, deadline_at=deadline_at)
        self.setup = replace(self.setup, harness=harness, max_concurrent=cap)


def two_workstreams() -> Script:
    """The script of a run whose planner starts two workstreams that are implemented and approved."""
    return {
        "planner": [plan_reply(*(implement(f"h{n}") for n in range(WORKSTREAMS)))],
        "implementer": [implemented() for _ in range(WORKSTREAMS)],
        "judge": [reviewed() for _ in range(WORKSTREAMS)],
        "max_in_flight": WORKSTREAMS,
    }


def assert_ends_live(run: SimulatedRun) -> None:
    assert run.halted is None, run.halted
    assert run.finished
    assert not run.liveness_violations


# -- measurements and turns that end every way ------------------------------------------------------


class Measurement(StrEnum):
    SUCCEEDED = "succeeded"
    WORKLOAD_FAILURE = "workload_failure"
    INFRASTRUCTURE_FAILURE = "infrastructure_failure"
    LOST = "lost"


class Turn(StrEnum):
    REPLIES = "replies"
    UNPARSABLE = "unparsable"
    RETRYABLE_FAILURE = "retryable_failure"
    LOST = "lost"


def _nth[T](outcomes: list[T], position: int, default: T) -> T:
    """The outcome of the ``position``-th occurrence; the last repeats, none means ``default``."""
    return default if not outcomes else outcomes[min(position, len(outcomes) - 1)]


class Scenario(Hosted):
    """A world that answers each measurement submission and agent turn with its generated outcome."""

    def __init__(self, measurements: list[Measurement], turns: list[Turn]) -> None:
        """Script the n-th submission and the n-th turn; the last outcome of a list repeats."""
        super().__init__()
        self._measurements = measurements
        self._turns = turns
        self._submitted = 0
        self._asked = 0
        self._no_evidence: set[str] = set()

    def __call__(self, request: Request, core: CoreState) -> Answer:
        """The generated outcome for a submission or turn, else the base world's answer."""
        answer: Answer | None = None
        if isinstance(request, SubmitMeasurement):
            answer = self._measurement(request)
        elif isinstance(request, ObserveOwnedJob) and self._ends_without_evidence(request, core):
            answer = Failed(accepted=True)
        elif isinstance(request, DispatchTurn | ResumeSessionTurn):
            answer = self._turn(request)
        return super().__call__(request, core) if answer is None else answer

    def _measurement(self, request: SubmitMeasurement) -> Answer | None:
        outcome = _nth(self._measurements, self._submitted, Measurement.SUCCEEDED)
        self._submitted += 1
        if outcome is Measurement.INFRASTRUCTURE_FAILURE:
            self._no_evidence.add(request.plan.model_dump_json())
        if outcome is Measurement.WORKLOAD_FAILURE:
            return Failed(MeasurementFailure.WORKLOAD)
        return Unknown() if outcome is Measurement.LOST else None

    def _turn(self, request: DispatchTurn | ResumeSessionTurn) -> Answer | None:
        outcome = _nth(self._turns, self._asked, Turn.REPLIES)
        self._asked += 1
        if outcome is Turn.UNPARSABLE:
            lease = ResourceId(root=f"lease:{request.turn.session.session_id.root}")
            return Succeeded(output_json="not json", resource_id=lease)
        if outcome is Turn.RETRYABLE_FAILURE:
            return Retryable()
        return Unknown() if outcome is Turn.LOST else None

    def _ends_without_evidence(self, request: ObserveOwnedJob, core: CoreState) -> bool:
        job = next(
            item
            for item in core.evaluation.jobs
            if isinstance(item, OwnedJob) and item.resource_id == request.resource_id
        )
        return job.plan.model_dump_json() in self._no_evidence


_ANSWERED_MEASUREMENTS = [
    Measurement.SUCCEEDED,
    Measurement.WORKLOAD_FAILURE,
    Measurement.INFRASTRUCTURE_FAILURE,
]


class Counting(Hosted):
    """A world that counts how often each request is executed."""

    def __init__(self, script: Script, *, deadline_at: float) -> None:
        """Script a run as `Hosted` does, under ``deadline_at``."""
        super().__init__(script, deadline_at=deadline_at)
        self.executed: Counter[str] = Counter()

    def __call__(self, request: Request, core: CoreState) -> Answer:
        """The base world's answer, once counted."""
        assert request.request_id is not None
        self.executed[request.request_id.root] += 1
        return super().__call__(request, core)


def test_a_clean_run_ends_live_asks_each_turn_a_rendered_prompt_within_the_deadline() -> None:
    """One run, every wiring fact: live end, no spin, no double execution, prompts, deadline."""
    deadline_at = 1800.0
    world = Counting(two_workstreams(), deadline_at=deadline_at)
    run = simulate(world)

    assert_ends_live(run)
    assert max(world.executed.values()) == 1
    assert spin_violations(Journal.from_ledger(run.core)) == []
    result = run.core.run.result
    assert result is not None
    assert result.outcome == "success", result.reason
    assert result.selection is not None
    assert result.selection.kind == "retained_candidate"
    registry = OPERATIONS.registry()
    records = [record for record in run.core.records.records]
    renders = [
        record.request
        for record in records
        if isinstance(record.request, ExecuteRegisteredOperation)
        and isinstance(registry.decode(record.request.operation), RenderRoleArtifacts)
    ]
    dispatched = [
        record for record in records if isinstance(record.request, DispatchTurn | ResumeSessionTurn)
    ]
    rendered = {f"prompt:{request.operation_id.root}" for request in renders}
    assert dispatched
    assert len(renders) == len(dispatched)
    for record in dispatched:
        turn = record.request.turn  # ty: ignore[unresolved-attribute]
        assert turn.prompts
        assert {item.artifact_id.root for item in turn.prompts} <= rendered
        assert record.until.at <= deadline_at
    roles = {record.request.turn.session.role_id.root for record in dispatched}  # ty: ignore[unresolved-attribute]
    assert roles >= {"dynamic-orchestrator", "dynamic-implementer"}


def test_a_candidate_measurement_nothing_answers_ends_the_run_live() -> None:
    """The second submission (the first candidate's) is never answered; the input's was."""
    assert_ends_live(simulate(Scenario([Measurement.SUCCEEDED, Measurement.LOST], [])))


def test_a_turn_the_executor_never_finishes_ends_the_run_live() -> None:
    assert_ends_live(simulate(Scenario([], [Turn.LOST])))


# -- a candidate's job that keeps running ----------------------------------------------------------


class SlowCandidateJobs(Hosted):
    """Candidate jobs keep running for ``running_polls`` polls, then end as the world says.

    The input's own measurement is never slow. With ``stop_while_running`` the operator's stop
    arrives once a candidate's job has been seen running, so its attempt settles over the job.
    """

    def __init__(
        self, *, running_polls: int, ends_failed: bool, stop_while_running: bool, passed: bool
    ) -> None:
        """Script one hypothesis whose candidate's job runs ``running_polls`` polls."""
        super().__init__(
            {
                "planner": [plan_reply(implement("h1"))],
                "implementer": [implemented() for _ in range(6)],
                "judge": [reviewed(passed=passed) for _ in range(6)],
            }
        )
        self._running_polls = running_polls
        self._ends_failed = ends_failed
        self._stop_while_running = stop_while_running
        self._polled: Counter[str] = Counter()
        self._stop_sent = False

    def attach(self, shell: CoreRuntime[Any], clock: VirtualClock) -> Enact | None:
        """Hear the shell: the operator's stop is submitted to it, as a control is."""
        del clock

        async def enact(request: Request, result: ExecutionResult) -> ExecutionResult:
            if (
                self._stop_while_running
                and not self._stop_sent
                and isinstance(request, ObserveOwnedJob)
                and self._polled[request.resource_id.root] > 0
            ):
                self._stop_sent = True
                at = shell.record.envelope.core.run.now_at
                stop = ControlInput(
                    control_id=ControlId(root="stop"), action="stop", mode=StopMode.DRAIN
                )
                shell.submit(RunControlEvent(control=stop, now_at=at), now_at=at)
            return result

        return enact

    def __call__(self, request: Request, core: CoreState) -> Answer:
        """A candidate's job runs for a while, then ends as scripted; all else as the base."""
        if isinstance(request, ObserveOwnedJob):
            job = next(
                item
                for item in core.evaluation.jobs
                if isinstance(item, OwnedJob) and item.resource_id == request.resource_id
            )
            if job.plan.purpose != "baseline":
                self._polled[request.resource_id.root] += 1
                if self._polled[request.resource_id.root] <= self._running_polls:
                    return Running(resource_id=request.resource_id)
                if self._ends_failed:
                    return Failed(accepted=True)
        return super().__call__(request, core)


def test_a_candidate_whose_job_is_still_running_when_its_attempt_settles_still_settles() -> None:
    world = SlowCandidateJobs(
        running_polls=3, ends_failed=False, stop_while_running=True, passed=True
    )

    finished = simulate(world)

    assert_ends_live(finished)
    for attempt in finished.core.attempts.attempts:
        assert attempt.phase == AttemptPhase.TERMINAL
        assert attempt.release_dependencies == ()
    assert finished.core.settlement.pending == ()
