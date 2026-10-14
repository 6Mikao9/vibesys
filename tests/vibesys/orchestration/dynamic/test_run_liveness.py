"""Whole dynamic runs on the production shell, simulated by vs-dst: liveness under executor faults.

The flow stories pin each decision; these runs ask what only a whole run shows. Whatever the
executors answer (a measurement refused, ended without evidence, never answered; a turn
unparsable, retryable, lost; a job that keeps running), the run ends terminal with nothing left
open, its requests stay bounded, none repeats without new information and none executes twice.
They also pin how the dynamic search sits on the run deadline: core bounds every turn it asks
by that deadline, and a run shorter than a role's turn budget still asks its turns.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from enum import StrEnum
from typing import TYPE_CHECKING, Any, TypedDict

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st
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
from vs_runtime.api.core import REQUEST_DISPATCH
from vs_sim.api.testing import examples

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


@pytest.mark.parametrize("position", range(2))
def test_a_measurement_nothing_ever_answers_ends_the_run_live(position: int) -> None:
    """The ``position``-th submission of the run (the input, then a candidate) is never answered."""
    outcomes = [Measurement.SUCCEEDED] * position + [Measurement.LOST]

    assert_ends_live(simulate(Scenario(outcomes, [])))


@pytest.mark.parametrize("outcome", [Turn.LOST, Turn.RETRYABLE_FAILURE])
def test_a_turn_the_executor_never_finishes_ends_the_run_live(outcome: Turn) -> None:
    assert_ends_live(simulate(Scenario([], [outcome])))


@pytest.mark.parametrize("measurement", _ANSWERED_MEASUREMENTS)
def test_the_committed_ledger_alone_shows_no_repeated_request(measurement: Measurement) -> None:
    """A harness that sees only the committed record checks the same no-spin rule."""
    finished = simulate(Scenario([measurement], []))

    assert finished.halted is None
    assert spin_violations(Journal.from_ledger(finished.core)) == []


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


@settings(max_examples=examples(pr=3, nightly=40), deadline=None, derandomize=True, database=None)
# Pinned: the job ends at once, the job outlives its attempt and fails, and a stop meets it.
@example(running_polls=0, ends_failed=False, stop_while_running=False, passed=True)
@example(running_polls=3, ends_failed=True, stop_while_running=False, passed=True)
@example(running_polls=3, ends_failed=False, stop_while_running=True, passed=False)
@given(
    running_polls=st.integers(0, 6),
    ends_failed=st.booleans(),
    stop_while_running=st.booleans(),
    passed=st.booleans(),
)
def test_a_candidate_whose_job_is_still_running_when_its_attempt_settles_still_settles(
    *, running_polls: int, ends_failed: bool, stop_while_running: bool, passed: bool
) -> None:
    world = SlowCandidateJobs(
        running_polls=running_polls,
        ends_failed=ends_failed,
        stop_while_running=stop_while_running,
        passed=passed,
    )

    finished = simulate(world)

    assert_ends_live(finished)
    for attempt in finished.core.attempts.attempts:
        assert attempt.phase == AttemptPhase.TERMINAL
        assert attempt.release_dependencies == ()
    assert finished.core.settlement.pending == ()


# -- prompts ---------------------------------------------------------------------------------------


def test_every_turn_of_a_run_is_given_the_prompt_a_render_operation_made_for_it() -> None:
    """Prompts are rendered by a declared operation, never by the search, before each turn."""
    run = simulate(Hosted(two_workstreams()))

    assert_ends_live(run)
    registry = OPERATIONS.registry()
    renders = [
        record.request
        for record in run.core.records.records
        if isinstance(record.request, ExecuteRegisteredOperation)
        and isinstance(registry.decode(record.request.operation), RenderRoleArtifacts)
    ]
    turns = [
        record.request
        for record in run.core.records.records
        if isinstance(record.request, DispatchTurn | ResumeSessionTurn)
    ]
    rendered = {f"prompt:{request.operation_id.root}" for request in renders}
    assert turns
    assert len(renders) == len(turns)
    assert all(
        turn.turn.prompts and {item.artifact_id.root for item in turn.turn.prompts} <= rendered
        for turn in turns
    )


# -- every request kind answered a faulty way ------------------------------------------------------

# Kinds the dynamic search never issues in a run with no fault: core issues them only to recover
# (inspect, cancel, block) or to resume a suspended turn.
RECOVERY_ONLY = frozenset(
    {
        "inspect_request",
        "inspect_turn",
        "inspect_owned_job",
        "cancel_turn",
        "cancel_owned_job",
        "cancel_owned_resource",
        "block_request",
        "resume_session_turn",
        "collect_evidence",
        "snapshot_and_retain_run",
        "restore_revision",
    }
)
ISSUED = frozenset(
    {
        "adopt_revision",
        "close_attempt_scope",
        "close_session",
        "discard_workspace",
        "dispatch_turn",
        "ensure_session",
        "ensure_workspace",
        "execute_registered_operation",
        "observe_owned_job",
        "retain_revision",
        "snapshot_and_retain",
        "submit_measurement",
        "verify_adoption",
    }
)
# A run that stays open idles until its deadline, one durable commit per clock tick, so the
# deadline sets the cost of such a case. The slowest run that ends (every submission answered
# Unknown) ends 12,601 s in.
DEADLINE = 15_000.0


class Reply(StrEnum):
    UNKNOWN_ONCE = "unknown_once"
    RETRYABLE_ONCE = "retryable_once"
    SILENT = "silent"


class Faulted(Hosted):
    """A world that answers the requests of one kind the way the case says, and counts each."""

    def __init__(self, kind: str, how: Reply, *, cap: int) -> None:
        """Fault the requests of ``kind`` (once, or every time when silent) at concurrency ``cap``."""
        super().__init__(two_workstreams(), cap=cap, deadline_at=DEADLINE)
        self._kind = kind
        self._how = how
        self.executed: Counter[str] = Counter()
        self._seen_of_kind = 0

    def __call__(self, request: Request, core: CoreState) -> Answer:
        """The faulty answer for the chosen kind, else the base world's."""
        assert request.request_id is not None
        self.executed[request.request_id.root] += 1
        if request.kind == self._kind:
            position, self._seen_of_kind = self._seen_of_kind, self._seen_of_kind + 1
            if self._how is Reply.SILENT or position == 0:
                return Retryable() if self._how is Reply.RETRYABLE_ONCE else Unknown()
        return super().__call__(request, core)


def test_every_request_kind_core_knows_is_classified_as_issued_or_recovery_only() -> None:
    """A kind added to core is classified here, so the dynamic search is checked against it."""
    known = {request.model_fields["kind"].default for request in REQUEST_DISPATCH}

    assert known == ISSUED | RECOVERY_ONLY


@pytest.mark.parametrize("cap", [1, 2])
def test_a_run_with_every_answer_conclusive_ends_live_and_executes_no_request_twice(
    cap: int,
) -> None:
    world = Faulted("", Reply.SILENT, cap=cap)  # the empty kind matches no request

    assert_ends_live(simulate(world))
    assert max(world.executed.values()) == 1


# Pairs the conformance suite (`tests/system/delivery/test_request_kinds.py`, CONCLUSIVE_AFTER_FAULT)
# does not run, though the dynamic search's run ends live: core retries a request answered
# retryably once, and a registered operation nothing ever answers is ended at its bound.
CONCLUSIVE_PAIRS = (
    *(
        (kind, Reply.RETRYABLE_ONCE)
        for kind in (
            "close_attempt_scope",
            "close_session",
            "discard_workspace",
            "ensure_session",
            "ensure_workspace",
            "observe_owned_job",
            "retain_revision",
            "snapshot_and_retain",
        )
    ),
    ("execute_registered_operation", Reply.SILENT),
)


@pytest.mark.parametrize("cap", [1, 2])
@pytest.mark.parametrize(("kind", "how"), CONCLUSIVE_PAIRS)
def test_a_request_kind_answered_a_faulty_way_still_ends_the_run_live(
    kind: str, how: Reply, cap: int
) -> None:
    assert_ends_live(simulate(Faulted(kind, how, cap=cap)))


# -- the run deadline ----------------------------------------------------------------------------


@settings(max_examples=3, deadline=None, derandomize=True, database=None)
@given(deadline_at=st.floats(min_value=100.0, max_value=7200.0))
def test_a_run_shorter_than_a_turn_budget_asks_its_turns_within_the_run(
    deadline_at: float,
) -> None:
    run = simulate(Hosted(deadline_at=deadline_at))

    assert_ends_live(run)
    result = run.core.run.result
    assert result is not None
    assert result.outcome == "success", result.reason
    # Core bounds what it dispatches by the run's deadline, whatever a role's own budget is.
    dispatched = [
        record
        for record in run.core.records.records
        if isinstance(record.request, DispatchTurn | ResumeSessionTurn)
    ]
    roles = {
        record.request.turn.session.role_id.root
        for record in dispatched
        if isinstance(record.request, DispatchTurn | ResumeSessionTurn)
    }
    assert roles >= {"dynamic-orchestrator", "dynamic-implementer"}
    assert all(record.until.at <= deadline_at for record in dispatched)
