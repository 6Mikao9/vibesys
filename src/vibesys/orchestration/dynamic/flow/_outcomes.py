"""What a workstream establishes and leaves behind, as plain typed values.

A workstream is told what to do (`WorkstreamStart`), establishes a `Result` and `Evidence`, and
ends with a `WorkstreamDone` the search folds into its record. Whatever stops it early is a
`WorkEndedError` carrying the typed `Failure`, and the helpers here turn what core reported (a
`Failed` event, a blocked turn) into that failure.
"""

from dataclasses import dataclass, field

from vibesys.candidates.api import Baseline
from vibesys.hypothesis import HypothesisOutcome
from vibesys.hypothesis.cadence import keeps_hypothesis_active
from vibesys.metrics import AcceptedReading, reading_of
from vibesys.orchestration.dynamic.search.api import (
    Blocker,
    BlockerKind,
    DynamicConfig,
    Failure,
    FailureKind,
    ParentSnapshot,
    Proof,
    Role,
    RoundRecord,
    Unreachable,
    UnreachableReason,
    WorkPlan,
)
from vs_core.api.search import (
    AttemptId,
    AttemptRef,
    EvidenceKind,
    Failed,
    FailureReason,
    InvocationRef,
    RevisionRef,
    Scope,
    Settlement,
    Value,
    WaitRefusal,
    attempt_id,
)

# -- what a workstream is told ---------------------------------------------------------------------


@dataclass(frozen=True)
class WorkstreamStart:
    """One scheduled workstream: its plan, and the parent it builds on, frozen at scheduling."""

    key: str
    sequence: int
    plan: WorkPlan
    parent: RevisionRef
    parent_hypothesis_id: str | None
    prior: RoundRecord | None
    """The previous round of the same hypothesis, when this workstream continues it."""
    config: DynamicConfig
    baseline: Baseline

    @property
    def attempt(self) -> AttemptId:
        """The identity of the attempt this workstream opens."""
        return attempt_id(self.plan.work_id, self.sequence)


@dataclass
class Attempt:
    """The opened attempt, and the candidate a stop or a failure settles it with."""

    ref: AttemptRef
    carried: "Result | None" = None

    @property
    def scope(self) -> Scope:
        """The scope the attempt's own work runs in."""
        return Scope(owner=self.ref.attempt_id, generation=self.ref.generation)


# -- what a workstream establishes ---------------------------------------------------------------------


class Candidate(Value):
    """What the implementer's reply and core's checkpoint established."""

    outcome: HypothesisOutcome
    summary: str
    next_step: str = ""
    revision: RevisionRef | None = None
    """The revision core retained for the turn; None when no checkpoint was awaited or none exists."""


class Verdict(Value):
    """The judge's decision on a candidate, with the turn it came from."""

    passed: bool
    invocation: InvocationRef


class Result(Value):
    """A candidate and, once reviewed, the verdict on it."""

    candidate: Candidate
    verdict: Verdict | None = None

    def unreviewed(self) -> "Result":
        """The same candidate, to be reviewed again."""
        return self.model_copy(update={"verdict": None})


class Unmeasured(Value):
    """A turn that ended without a candidate that can be measured: why, and what it left."""

    blocker: Blocker
    result: Result | None = None


class Evidence(Value):
    """The trusted readings of a candidate, and the evidence they were decoded from."""

    readings: tuple[AcceptedReading, ...] = ()
    proof: Proof | None = None


class Closing(Value):
    """What the close of the workspace asserts: the finished attempt the settlement judges."""

    result: Result | None = None
    evidence: Evidence = Evidence()
    failure: Failure | None = None
    unreachable: Unreachable | None = None

    @property
    def readings(self) -> tuple[AcceptedReading, ...]:
        """The decoded trusted evaluations of the attempt's candidate."""
        return self.evidence.readings

    @property
    def candidate(self) -> RevisionRef | None:
        """The revision the attempt retained, if any."""
        return None if self.result is None else self.result.candidate.revision

    @property
    def review_passed(self) -> bool | None:
        """The judge's verdict, or None when no review ran."""
        verdict = None if self.result is None else self.result.verdict
        return None if verdict is None else verdict.passed

    @property
    def judge_invocation(self) -> InvocationRef | None:
        """The judge turn the verdict came from."""
        verdict = None if self.result is None else self.result.verdict
        return None if verdict is None else verdict.invocation

    @property
    def outcome(self) -> HypothesisOutcome | None:
        """The implementer's own account of its result."""
        return None if self.result is None else self.result.candidate.outcome

    @property
    def stopped(self) -> bool:
        """Whether the operator stopped the attempt."""
        return self.failure is not None and self.failure.kind is FailureKind.STOPPED

    @property
    def failed(self) -> bool:
        """Whether the attempt recorded a failure, an operator stop included."""
        return self.failure is not None


# -- how a workstream ends ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkstreamDone:
    """The workstream is over; the search records it.

    ``refunded`` says the slot was never used (the attempt was never admitted). ``withheld`` is a
    parent revision that could not be verified and is never offered again.
    """

    start: WorkstreamStart
    failure: Failure | None
    generation: int = 0
    refunded: bool = False
    withheld: RevisionRef | None = None
    settlement: Settlement | None = None
    result: Result | None = None
    evidence: Evidence = field(default_factory=Evidence)
    unreachable: Unreachable | None = None


class WorkEndedError(Exception):
    """A workstream cannot go on: it ends with this failure and settles with what it has."""

    def __init__(
        self,
        failure: Failure,
        *,
        proof: Proof | None = None,
        unreachable: Unreachable | None = None,
        withheld: RevisionRef | None = None,
    ) -> None:
        """Carry the failure and the facts that settle with it."""
        super().__init__(failure.kind.value)
        self.failure = failure
        self.proof = proof
        self.unreachable = unreachable
        self.withheld = withheld


STOPPED = Failure(kind=FailureKind.STOPPED)


def failure_of(event: Failed, role: Role | None) -> Failure:  # noqa: PLR0911  # LW-220402 [PLR0911]; one return per wait-failure reason keeps the mapping to the typed failure exhaustive and visible.
    """The typed account of a wait that ended without a result."""
    match event.reason:
        case FailureReason.REFUSED:
            return Failure(kind=FailureKind.REFUSED, cause=event.cause)
        case FailureReason.OUT_OF_BUDGET:
            return Failure(kind=FailureKind.OUT_OF_BUDGET)
        case FailureReason.INVALID_REPLY:
            return Failure(kind=FailureKind.INVALID_REPLY, role=role)
        case FailureReason.RENDER_FAILED:
            return Failure(kind=FailureKind.RENDER_FAILED, role=role)
        case FailureReason.WAIT_REFUSED:
            return Failure(kind=FailureKind.WAIT_REFUSED, role=role, refusal=event.refusal)
        case FailureReason.TURN_FAILED:
            return Failure(kind=FailureKind.TURN_FAILED, role=role, turn_failure=event.turn_failure)
        case (
            FailureReason.TIMED_OUT
            | FailureReason.WORKSPACE_CLOSED
            | FailureReason.OWNER_GONE
            | FailureReason.LOST
            | FailureReason.MEASUREMENT_UNTRUSTED
        ):
            return Failure(kind=FailureKind.ENDED, ended=event.reason, role=role)


def ended_by(event: Failed, role: Role | None, *, plan: WorkPlan | None = None) -> WorkEndedError:
    """The end of a workstream whose wait failed.

    A turn core could not suspend for lack of the resume capability also records that fact,
    named by the plan it was for.
    """
    failure = failure_of(event, role)
    missing = None
    if (
        plan is not None
        and failure.kind is FailureKind.WAIT_REFUSED
        and failure.refusal is WaitRefusal.NOT_OFFERED
    ):
        missing = Unreachable(
            reason=UnreachableReason.RESUME_CAPABILITY_NOT_OFFERED, subject=plan.work_id
        )
    return WorkEndedError(failure, unreachable=missing)


def blocked_failure(blocker: Blocker) -> Failure:
    """Why a workstream that may not retry ended, from the last turn that gave nothing."""
    match blocker.kind:
        case BlockerKind.FAILED:
            return Failure(kind=FailureKind.IMPLEMENTATION_FAILED)
        case BlockerKind.REJECTED:
            return Failure(kind=FailureKind.REVIEW_REJECTED)
        case BlockerKind.NO_REPLY:
            if blocker.turn_failure is not None:
                return Failure(
                    kind=FailureKind.TURN_FAILED,
                    role=Role.IMPLEMENTER,
                    turn_failure=blocker.turn_failure,
                )
            return Failure(
                kind=FailureKind.ENDED,
                role=Role.IMPLEMENTER,
                ended=blocker.ended or FailureReason.TIMED_OUT,
            )


# -- what a finished workstream leaves behind -------------------------------------------------------


def _failing_tail(done: WorkstreamDone) -> str:
    """What the round's failed trusted check printed, else what a failed evaluation printed.

    A benchmark that crashed the server leaves its output in the reading's feedback; that text,
    not the agent's summary, is what tells the next agent why the candidate failed.
    """
    accuracy = reading_of(done.evidence.readings, EvidenceKind.CORRECTNESS)
    benchmark = reading_of(done.evidence.readings, EvidenceKind.BENCHMARK)
    for reading in (benchmark, accuracy):
        if reading is not None and not reading.passed and reading.feedback:
            return reading.feedback
    return "" if done.failure is None else done.failure.output


def round_of(done: WorkstreamDone) -> RoundRecord:
    """The round an implement workstream adds to its hypothesis."""
    start = done.start
    accuracy = reading_of(done.evidence.readings, EvidenceKind.CORRECTNESS)
    benchmark = reading_of(done.evidence.readings, EvidenceKind.BENCHMARK)
    result = done.result
    outcome = None if result is None else result.candidate.outcome
    next_step = "" if result is None else result.candidate.next_step
    kept = keeps_hypothesis_active(
        outcome=outcome,
        next_step=next_step,
        continuation_rounds=0,
        max_continuation_rounds=start.config.max_continuation_rounds,
    )
    settlement = done.settlement
    return RoundRecord(
        sequence=start.sequence,
        attempt=start.attempt,
        outcome=outcome,
        summary="" if result is None else result.candidate.summary,
        review_passed=None if result is None or result.verdict is None else result.verdict.passed,
        candidate=None if settlement is None else settlement.candidate,
        accuracy_passed=None if accuracy is None else accuracy.passed,
        benchmark_passed=None if benchmark is None else benchmark.passed,
        metrics=() if benchmark is None else benchmark.metrics,
        partial=None if benchmark is None else benchmark.partial,
        eligible=settlement is not None and settlement.eligible,
        failure=done.failure,
        failure_tail=_failing_tail(done),
        settlement=None if settlement is None else settlement.settlement_id,
        kept_active=kept,
    )


def snapshot_of(done: WorkstreamDone) -> ParentSnapshot | None:
    """The exact candidate offered as a parent, once it is settled with accuracy proven."""
    accuracy = reading_of(done.evidence.readings, EvidenceKind.CORRECTNESS)
    result = done.result
    if (
        accuracy is None
        or not accuracy.passed
        or result is None
        or result.candidate.revision is None
    ):
        return None
    return ParentSnapshot(
        hypothesis_id=done.start.plan.work_id,
        generation=done.generation,
        revision=result.candidate.revision,
        accuracy=accuracy,
        benchmark=reading_of(done.evidence.readings, EvidenceKind.BENCHMARK),
        submission_index=done.start.sequence,
        change_summary=result.candidate.summary or None,
    )
