"""One workstream: a slot's worth of work from parent verification to settlement.

A workstream is a child machine of the search. Its phase says what it waits for and holds only
the data valid there; `update` is one `match` over the phase and the event. It verifies its
parent, opens a workspace, runs a role's turns, measures, decodes the evidence and settles the
workspace. A turn that ends without a measurable candidate is asked again only while the
progress policy (`_progress`) says another turn is worth it.

When the workstream is over its phase is `Done`; the search records it and drops the child.
"""

from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from vibesys.candidates.api import (
    Baseline,
    assessment,
    stages,
    until_deadline,
)
from vibesys.candidates.api import settle_for as candidate_settle_for
from vibesys.hypothesis import HypothesisConfig, HypothesisOutcome
from vibesys.hypothesis.cadence import keeps_hypothesis_active, review_due
from vibesys.metrics import AcceptedReading, accept_readings, reading_of
from vibesys.orchestration.dynamic.models import ImplementerResult, ReviewResult
from vibesys.orchestration.dynamic.search import _briefs as briefs
from vibesys.orchestration.dynamic.search import _review_evidence as review_evidence
from vibesys.orchestration.dynamic.search._config import DynamicConfig
from vibesys.orchestration.dynamic.search._operations import (
    EvidenceReadings,
    InterpretEvidence,
    ParentVerification,
    VerifyParentRevision,
)
from vibesys.orchestration.dynamic.search._parents import ParentSnapshot
from vibesys.orchestration.dynamic.search._progress import refusal
from vibesys.orchestration.dynamic.search._values import (
    Blocker,
    BlockerKind,
    Failure,
    FailureKind,
    Proof,
    Role,
    RoundRecord,
    Unreachable,
    UnreachableReason,
    WorkKind,
    WorkPlan,
)
from vs_core.api.search import (
    THIS_RUN,
    AskAgent,
    AssessmentKind,
    AttemptBudget,
    AttemptId,
    AttemptRef,
    Decision,
    DecisionId,
    Declined,
    Event,
    EvidenceKind,
    EvidenceRef,
    Failed,
    FailureReason,
    InvocationRef,
    Measure,
    MeasurementFinished,
    MeasurementRecipe,
    MeasurementResult,
    Operation,
    OperationFinished,
    RevisionRef,
    RunView,
    Scope,
    Settle,
    Settlement,
    StartAttempt,
    StopRequested,
    TurnFinished,
    UnexpectedEvent,
    Value,
    Wait,
    WaitRefusal,
    Withdraw,
    Within,
    WorkspaceClosed,
    WorkspaceMode,
    WorkspaceOpened,
    WorkspacePlan,
    attempt_id,
    item_id,
    may_resubmit,
    trusted_keys,
)

VERIFY = DecisionId(root="verify")
START = DecisionId(root="start")
SETTLE = DecisionId(root="settle")
SETTLE_AGAIN = DecisionId(root="settle.1")
INTERPRET = DecisionId(root="interpret")


# -- what the reply of a role established ---------------------------------------------------------


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


class Spent(Value):
    """Implementer turns charged so far, and every turn that ended without a measurable candidate."""

    turns: int = Field(ge=1)
    blockers: tuple[Blocker, ...] = ()


# -- phases ------------------------------------------------------------------------------------------


class VerifyingParent(Value):
    """The parent revision is being proven to reproduce its retained content."""

    kind: Literal["verifying_parent"] = "verifying_parent"


class Opening(Value):
    """The attempt was requested; ``stopping`` records a stop that arrived before it opened."""

    kind: Literal["opening"] = "opening"
    stopping: bool = False


class Implementing(Value):
    """An implementer turn is out. ``prior`` is the earlier candidate, unreviewed, if any."""

    kind: Literal["implementing"] = "implementing"
    spent: Spent
    prior: Result | None = None


class Profiling(Value):
    """A profiler turn is out."""

    kind: Literal["profiling"] = "profiling"


class ReadingEvidence(Value):
    """The evidence owner is decoding the evaluations the candidate's author ran."""

    kind: Literal["reading_evidence"] = "reading_evidence"
    spent: Spent
    result: Result
    refs: tuple[EvidenceRef, ...]


class Judging(Value):
    """A judge turn is out."""

    kind: Literal["judging"] = "judging"
    spent: Spent
    result: Result


class Measuring(Value):
    """A trusted measurement is out; ``submissions`` counts how often it was submitted."""

    kind: Literal["measuring"] = "measuring"
    result: Result | None
    submissions: int = Field(ge=1)


class Interpreting(Value):
    """The trusted evidence is being decoded."""

    kind: Literal["interpreting"] = "interpreting"
    result: Result | None
    proof: Proof


class Settling(Value):
    """The close of the workspace, with what it asserts, is out.

    It reads as the finished attempt the shared settlement policy judges.
    """

    kind: Literal["settling"] = "settling"
    result: Result | None
    readings: tuple[AcceptedReading, ...] = ()
    failure: Failure | None = None
    proof: Proof | None = None
    unreachable: Unreachable | None = None
    refused: int = Field(default=0, ge=0)
    """How many closes core refused; the second proposal is the plain one."""

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


class Done(Value):
    """The workstream is over; the search records it and drops the child.

    ``refunded`` says the slot was never used (the attempt was never admitted). ``withheld`` is a
    parent revision that could not be verified and is never offered again.
    """

    kind: Literal["done"] = "done"
    failure: Failure | None
    refunded: bool = False
    withheld: RevisionRef | None = None
    settlement: Settlement | None = None
    result: Result | None = None
    readings: tuple[AcceptedReading, ...] = ()
    proof: Proof | None = None
    unreachable: Unreachable | None = None


type WorkPhase = Annotated[
    VerifyingParent
    | Opening
    | Implementing
    | Profiling
    | ReadingEvidence
    | Judging
    | Measuring
    | Interpreting
    | Settling
    | Done,
    Field(discriminator="kind"),
]


class Workstream(Value):
    """One scheduled workstream: its plan, the parent it builds on and the phase it is in."""

    kind: Literal["workstream"] = "workstream"
    sequence: int = Field(gt=0)
    plan: WorkPlan
    attempt: AttemptId
    generation: int = Field(default=0, ge=0)
    parent: RevisionRef
    """Frozen when the workstream is scheduled; a better candidate later never changes it."""
    parent_hypothesis_id: str | None = None
    prior: RoundRecord | None = None
    """The previous round of the same hypothesis, when this workstream continues it."""
    phase: WorkPhase

    @property
    def ref(self) -> AttemptRef:
        """The exact attempt generation."""
        return AttemptRef(attempt_id=self.attempt, generation=self.generation)

    @property
    def scope(self) -> Scope:
        """The scope the attempt's own work runs in."""
        return Scope(owner=self.attempt, generation=self.generation)


@dataclass(frozen=True)
class Env:
    """What a workstream reads and never keeps: the policy, the baseline and the run's view."""

    config: DynamicConfig
    baseline: Baseline
    view: RunView
    recipe: MeasurementRecipe


# -- decisions ---------------------------------------------------------------------------------------


def _wait(request: Operation | AskAgent | Measure | StartAttempt | Withdraw, limit: float) -> Wait:
    return Wait(request=request, until=Within(limit=limit))


def _verify(ws: Workstream, env: Env) -> Decision:
    request = Operation(
        decision_id=VERIFY, scope=THIS_RUN, request=VerifyParentRevision(parent=ws.parent)
    )
    return _wait(request, env.config.operation_seconds)


def _start(ws: Workstream, env: Env) -> Decision:
    config = env.config
    isolated = ws.plan.kind is WorkKind.IMPLEMENT
    request = StartAttempt(
        decision_id=START,
        scope=THIS_RUN,
        attempt_id=ws.attempt,
        item_id=item_id(ws.plan.work_id, ws.sequence),
        workspace=WorkspacePlan(
            mode=WorkspaceMode.ISOLATED_CHILD if isolated else WorkspaceMode.READ_ONLY_REVISION,
            base=ws.parent,
        ),
        budget=AttemptBudget(
            admission_charge=1,
            paid_invocation_limit=config.max_retries_per_round,
            retry_limit=config.max_corrections + config.max_turn_drops,
        ),
    )
    return Wait(request=request, until=until_deadline(env.view))


def _ask(  # noqa: PLR0913  # LW-220403 [PLR0913]; each argument is a distinct fact of the ask; bundling them in a throwaway record only renames the parameters.
    ws: Workstream, role: Role, name: str, prompt: Value, revision: RevisionRef, seconds: float
) -> Decision:
    request = AskAgent(
        decision_id=DecisionId(root=name),
        scope=ws.scope,
        agent=role.value,
        input=prompt,
        revision=revision,
    )
    return _wait(request, seconds)


def _ask_implementer(ws: Workstream, env: Env, spent: Spent) -> Decision:
    prompt = briefs.implement_prompt(ws.plan, ws.parent, ws.prior, spent.blockers)
    name = f"implementer.{spent.turns - 1}"
    return _ask(ws, Role.IMPLEMENTER, name, prompt, ws.parent, env.config.implementer_turn_seconds)


def _ask_judge(  # noqa: PLR0913  # LW-220404 [PLR0913]; the judge ask reads the workstream, environment and four distinct review facts; a wrapper record adds no meaning.
    ws: Workstream,
    env: Env,
    spent: Spent,
    result: Result,
    refs: tuple[EvidenceRef, ...],
    readings: tuple[AcceptedReading, ...],
) -> tuple[Workstream, list[Decision]]:
    revision = _candidate_of(result)
    prompt = briefs.review_prompt(
        ws.plan,
        revision,
        result.candidate.summary,
        review_evidence.evaluations(refs, readings),
    )
    name = f"judge.{spent.turns - 1}"
    ask = _ask(ws, Role.JUDGE, name, prompt, revision, env.config.judge_turn_seconds)
    return _moved(ws, Judging(spent=spent, result=result)), [ask]


def _candidate_of(result: Result) -> RevisionRef:
    revision = result.candidate.revision
    if revision is None:
        message = "a reviewed candidate has a retained revision"
        raise ValueError(message)
    return revision


def _measured_revision(ws: Workstream, result: Result | None) -> RevisionRef:
    """The revision a measurement names: a profile measures its parent."""
    if ws.plan.kind is WorkKind.PROFILE or result is None or result.candidate.revision is None:
        return ws.parent
    return result.candidate.revision


def _measure(ws: Workstream, env: Env, result: Result | None, index: int) -> Decision:
    profile = ws.plan.kind is WorkKind.PROFILE
    names = ("profile",) if profile else stages(env.config)
    recipe = env.recipe
    if profile and ws.plan.required_fields:
        recipe = recipe.requiring("profile", ws.plan.required_fields)
    plan = recipe.plan(
        _measured_revision(ws, result),
        purpose="profile" if profile else "official",
        now_at=env.view.now_at,
        stages=names,
        # Core retries a submission only after an infrastructure failure, and only within
        # this bound, which core's own run limit repeats.
        submission_limit=env.config.max_input_measurement_attempts,
    )
    request = Measure(decision_id=DecisionId(root=f"measure.{index}"), scope=ws.scope, plan=plan)
    return Wait(request=request, until=Within(limit=recipe.budget(names)))


def _decode(env: Env, name: str, refs: tuple[EvidenceRef, ...], ws: Workstream) -> Decision:
    request = Operation(
        decision_id=DecisionId(root=name), scope=ws.scope, request=InterpretEvidence(evidence=refs)
    )
    return _wait(request, env.config.operation_seconds)


# -- failure -----------------------------------------------------------------------------------------


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


def _blocked_failure(blocker: Blocker) -> Failure:
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


def _unreachable_of(ws: Workstream, failure: Failure) -> Unreachable | None:
    if failure.kind is FailureKind.WAIT_REFUSED and failure.refusal is WaitRefusal.NOT_OFFERED:
        return Unreachable(
            reason=UnreachableReason.RESUME_CAPABILITY_NOT_OFFERED, subject=ws.plan.work_id
        )
    return None


# -- moving between phases -------------------------------------------------------------------------


def _moved(ws: Workstream, phase: WorkPhase) -> Workstream:
    return ws.model_copy(update={"phase": phase})


def _done(ws: Workstream, done: Done) -> tuple[Workstream, list[Decision]]:
    return _moved(ws, done), []


def _carried(phase: WorkPhase) -> Result | None:
    """The result a phase holds, which a stop or a failure settles with."""
    match phase:
        case Implementing(prior=prior):
            return prior
        case ReadingEvidence(result=result) | Judging(result=result):
            return result
        case Measuring(result=result) | Interpreting(result=result):
            return result
        case _:
            return None


def _close(settling: Settling, ws: Workstream, env: Env) -> Decision:
    """The close that settles the attempt: the full assertion, then the plain one."""
    if settling.refused:
        disposition = Settle(
            assessments=(), eligible=False, retention="discard", outcome="failed", candidate=None
        )
        name = SETTLE_AGAIN
    else:
        disposition = _disposition(ws, settling, env)
        name = SETTLE
    request = Withdraw(decision_id=name, scope=THIS_RUN, target=ws.ref, disposition=disposition)
    return Wait(request=request, until=until_deadline(env.view))


def _disposition(ws: Workstream, settling: Settling, env: Env) -> Settle:
    """The assessments, eligibility and retention to propose for a finished attempt.

    A profile workstream measures a revision without proposing one, so it settles as ineligible
    and discarded.
    """
    if ws.plan.kind is WorkKind.PROFILE:
        profile = reading_of(settling.readings, EvidenceKind.PROFILING)
        return Settle(
            assessments=assessment(AssessmentKind.PROFILING, profile, None),
            eligible=False,
            retention="discard",
            outcome="failed" if settling.failed else "succeeded",
            candidate=None,
        )
    return candidate_settle_for(settling, env.baseline, env.config)


def settle(  # noqa: PLR0913  # LW-220410 [PLR0913]; settling reads the workstream, environment and four distinct facts of the finished attempt; a record only renames them.
    ws: Workstream,
    env: Env,
    result: Result | None,
    *,
    readings: tuple[AcceptedReading, ...] = (),
    failure: Failure | None = None,
    proof: Proof | None = None,
    unreachable: Unreachable | None = None,
) -> tuple[Workstream, list[Decision]]:
    """Close the workspace, asserting what the workstream established."""
    settling = Settling(
        result=result, readings=readings, failure=failure, proof=proof, unreachable=unreachable
    )
    return _moved(ws, settling), [_close(settling, ws, env)]


def fail(  # noqa: PLR0913  # LW-220411 [PLR0913]; a failure carries the workstream, environment, the typed failure and its settlement facts; a record only renames them.
    ws: Workstream,
    env: Env,
    failure: Failure,
    result: Result | None,
    *,
    proof: Proof | None = None,
    unreachable: Unreachable | None = None,
) -> tuple[Workstream, list[Decision]]:
    """End the workstream with a typed failure; an admitted attempt must still settle."""
    if isinstance(ws.phase, VerifyingParent | Opening):
        return _done(ws, Done(failure=failure, refunded=True))
    return settle(ws, env, result, failure=failure, proof=proof, unreachable=unreachable)


# -- the first decisions of a workstream -----------------------------------------------------------


def begin(  # noqa: PLR0913  # LW-220412 [PLR0913]; a workstream starts from its plan, position, parent, scope and environment, all distinct facts; a record only renames them.
    plan: WorkPlan,
    sequence: int,
    parent: RevisionRef,
    parent_hypothesis_id: str | None,
    prior: RoundRecord | None,
    *,
    stopping: bool,
    env: Env,
) -> tuple[Workstream, list[Decision]]:
    """A scheduled workstream: stopped at once, opened at once, or verifying its parent first."""
    start = Workstream(
        sequence=sequence,
        plan=plan,
        attempt=attempt_id(plan.work_id, sequence),
        parent=parent,
        parent_hypothesis_id=parent_hypothesis_id,
        prior=prior,
        phase=VerifyingParent(),
    )
    if stopping:
        stopped = Failure(kind=FailureKind.STOPPED)
        return _done(start, Done(failure=stopped))
    if parent == env.view.facts.baseline:
        return _moved(start, Opening()), [_start(start, env)]
    return start, [_verify(start, env)]


def _first_turn(ws: Workstream, env: Env) -> tuple[Workstream, list[Decision]]:
    if ws.plan.kind is WorkKind.PROFILE:
        prompt = briefs.profile_prompt(ws.plan, ws.parent)
        ask = _ask(
            ws, Role.PROFILER, "profiler.0", prompt, ws.parent, env.config.profiler_turn_seconds
        )
        return _moved(ws, Profiling()), [ask]
    spent = Spent(turns=1)
    return _moved(ws, Implementing(spent=spent)), [_ask_implementer(ws, env, spent)]


# -- update ---------------------------------------------------------------------------------------


def update(ws: Workstream, event: Event, env: Env) -> tuple[Workstream, list[Decision]]:  # noqa: C901, PLR0911, PLR0912  # LW-220401 [C901, PLR0911, PLR0912]; one exhaustive match over (phase, event) is the machine; splitting it across handlers would hide which phases take which events.
    """One event: move to the next phase and say what that phase needs."""
    match ws.phase, event:
        # an operator stop reaches every phase; it never cancels a turn, it settles around it
        case VerifyingParent(), StopRequested():
            return _done(ws, Done(failure=Failure(kind=FailureKind.STOPPED)))
        case Opening(), StopRequested():
            return _moved(ws, Opening(stopping=True)), []
        case Settling(), StopRequested():
            return ws, []
        case (
            Implementing()
            | Profiling()
            | ReadingEvidence()
            | Judging()
            | Measuring()
            | Interpreting(),
            StopRequested(),
        ):
            stopped = Failure(kind=FailureKind.STOPPED)
            return settle(ws, env, _carried(ws.phase), failure=stopped)

        case VerifyingParent(), OperationFinished(result=result):
            return _verified(ws, env, result.outcome)
        case VerifyingParent(), Failed():
            return fail(ws, env, failure_of(event, None), None)

        case Opening(stopping=stopping), WorkspaceOpened(attempt=opened):
            opened_ws = ws.model_copy(update={"generation": opened.generation})
            if stopping:
                return settle(opened_ws, env, None, failure=Failure(kind=FailureKind.STOPPED))
            return _first_turn(opened_ws, env)
        case Opening(), Failed():
            return fail(ws, env, failure_of(event, None), None)

        case Implementing(spent=spent), TurnFinished(reply=ImplementerResult() as reply):
            return _implementer_replied(ws, env, spent, event, reply)
        case Implementing(spent=spent, prior=prior), Failed(
            reason=FailureReason.TIMED_OUT | FailureReason.TURN_FAILED
        ):
            blocker = Blocker(
                kind=BlockerKind.NO_REPLY,
                ended=event.reason if event.reason is FailureReason.TIMED_OUT else None,
                turn_failure=event.turn_failure,
                digest=event.digest,
            )
            return _retry_unmeasured(ws, env, spent, prior, blocker)
        case Implementing(prior=prior), Failed():
            return _failed_turn(ws, env, event, Role.IMPLEMENTER, prior)

        case ReadingEvidence(spent=spent, result=result, refs=refs), OperationFinished(
            result=finished
        ):
            readings = _readings(finished.outcome, refs)
            return _ask_judge(ws, env, spent, result, refs, readings)
        case ReadingEvidence(result=result), Failed():
            return fail(ws, env, failure_of(event, None), result)

        case Judging(spent=spent, result=result), TurnFinished(
            reply=ReviewResult() as reply, invocation=invocation
        ):
            graded = result.model_copy(
                update={"verdict": Verdict(passed=reply.passed, invocation=invocation)}
            )
            if reply.passed:
                return _measure_or_settle(ws, env, graded)
            blocker = Blocker(
                kind=BlockerKind.REJECTED,
                summary=reply.feedback or reply.analysis,
                revision=result.candidate.revision,
            )
            return _retry_unmeasured(ws, env, spent, graded, blocker)
        case Judging(result=result), Failed():
            return _failed_turn(ws, env, event, Role.JUDGE, result)

        case Profiling(), TurnFinished():
            return _profiled(ws, env)
        case Profiling(), Failed():
            return _failed_turn(ws, env, event, Role.PROFILER, None)

        case Measuring(result=result, submissions=submitted), MeasurementFinished(result=found):
            return _measured(ws, env, result, submitted, found)
        case Measuring(result=result), Failed():
            return fail(ws, env, failure_of(event, None), result)

        case Interpreting(result=result, proof=proof), OperationFinished(result=finished):
            return _interpreted(ws, env, result, proof, finished.outcome)
        case Interpreting(result=result, proof=proof), Failed():
            return fail(ws, env, failure_of(event, None), result, proof=proof)

        case Settling() as settling, WorkspaceClosed(settlement=settlement):
            return _done(ws, _finished(settling, settlement))
        case Settling() as settling, Failed(decision=decision) if decision in (
            SETTLE,
            SETTLE_AGAIN,
        ):
            return _close_refused(ws, env, settling, event)
        case Settling() as settling, Failed():
            # a late failure of work the settlement already went around: it only fills in why
            late = settling.failure or failure_of(event, None)
            return _moved(ws, settling.model_copy(update={"failure": late})), []
        case Settling(), TurnFinished() | OperationFinished() | MeasurementFinished():
            return ws, []

        case _:
            raise UnexpectedEvent(ws.phase, event)


def _verified(
    ws: Workstream, env: Env, outcome: BaseModel | None
) -> tuple[Workstream, list[Decision]]:
    if (
        isinstance(outcome, ParentVerification)
        and outcome.status == "succeeded"
        and outcome.verified
    ):
        return _moved(ws, Opening()), [_start(ws, env)]
    unverified = Failure(kind=FailureKind.PARENT_UNVERIFIED, revision=ws.parent)
    return _done(ws, Done(failure=unverified, refunded=True, withheld=ws.parent))


def _failed_turn(
    ws: Workstream, env: Env, event: Failed, role: Role, carried: Result | None
) -> tuple[Workstream, list[Decision]]:
    failure = failure_of(event, role)
    return fail(ws, env, failure, carried, unreachable=_unreachable_of(ws, failure))


def _implementer_replied(
    ws: Workstream, env: Env, spent: Spent, event: TurnFinished, reply: ImplementerResult
) -> tuple[Workstream, list[Decision]]:
    """Fold the reply with core's checkpoint of the turn, which a non-blocked reply awaits."""
    if reply.outcome is HypothesisOutcome.BLOCKED:
        return _implemented(ws, env, spent, reply, None)
    match event.checkpoint:
        case Declined(reason=declined):
            failure = Failure(kind=FailureKind.CHECKPOINT_DECLINED, declined=declined)
            return fail(ws, env, failure, None)
        case None:
            raise UnexpectedEvent(ws.phase, event)
        case checkpoint:
            return _implemented(ws, env, spent, reply, checkpoint.revision)


def _implemented(
    ws: Workstream, env: Env, spent: Spent, reply: ImplementerResult, revision: RevisionRef | None
) -> tuple[Workstream, list[Decision]]:
    candidate = Candidate(
        outcome=reply.outcome, summary=reply.summary, next_step=reply.next_step, revision=revision
    )
    result = Result(candidate=candidate)
    if reply.outcome is HypothesisOutcome.IMPLEMENTATION_FAILED:
        blocker = Blocker(
            kind=BlockerKind.FAILED,
            summary=reply.summary,
            next_step=reply.next_step,
            revision=revision,
        )
        return _retry_unmeasured(ws, env, spent, result, blocker)
    if reply.outcome is HypothesisOutcome.BLOCKED:
        return settle(ws, env, result)
    if revision is None or revision == ws.parent:
        return fail(ws, env, Failure(kind=FailureKind.NO_CHANGED_CANDIDATE), result)
    config = env.config
    hypothesis = HypothesisConfig(
        max_rounds=config.start_budget,
        judge_every=config.judge_every,
        max_retries_per_round=config.max_retries_per_round,
        max_continuation_rounds=config.max_continuation_rounds,
    )
    if review_due(hypothesis, round_number=ws.sequence, outcome=reply.outcome):
        return _begin_review(ws, env, spent, result, revision)
    return _measure_or_settle(ws, env, result)


def _begin_review(
    ws: Workstream, env: Env, spent: Spent, result: Result, revision: RevisionRef
) -> tuple[Workstream, list[Decision]]:
    refs = review_evidence.review_refs(env.view, ws.ref, revision)
    if not refs:
        return _ask_judge(ws, env, spent, result, (), ())
    name = f"review-evidence.{spent.turns - 1}"
    phase = ReadingEvidence(spent=spent, result=result, refs=refs)
    return _moved(ws, phase), [_decode(env, name, refs, ws)]


def _readings(
    outcome: BaseModel | None, refs: tuple[EvidenceRef, ...]
) -> tuple[AcceptedReading, ...]:
    """What the owner decoded of ``refs``; a refusal leaves the evidence listed without numbers."""
    if not isinstance(outcome, EvidenceReadings) or outcome.status != "succeeded":
        return ()
    accepted = accept_readings(outcome.readings, refs)
    return () if isinstance(accepted, str) else accepted


def _measure_or_settle(
    ws: Workstream, env: Env, result: Result
) -> tuple[Workstream, list[Decision]]:
    if stages(env.config):
        return _moved(ws, Measuring(result=result, submissions=1)), [_measure(ws, env, result, 0)]
    return settle(ws, env, result)


def _retry_unmeasured(
    ws: Workstream,
    env: Env,
    spent: Spent,
    result: Result | None,
    blocker: Blocker,
) -> tuple[Workstream, list[Decision]]:
    """Ask again after a turn that ended without a measurable candidate, or settle as failed."""
    blockers = (*spent.blockers, blocker)
    if (refused := refusal(blockers, env.config, ws.parent)) is not None:
        return fail(ws, env, refused, result)
    if ws.plan.kind is not WorkKind.IMPLEMENT or spent.turns >= env.config.max_retries_per_round:
        return fail(ws, env, _blocked_failure(blocker), result)
    again = Spent(turns=spent.turns + 1, blockers=blockers)
    prior = None if result is None else result.model_copy(update={"verdict": None})
    return _moved(ws, Implementing(spent=again, prior=prior)), [_ask_implementer(ws, env, again)]


def _profiled(ws: Workstream, env: Env) -> tuple[Workstream, list[Decision]]:
    """The profiler answered; its reply is never read, only what a trusted capture measures."""
    if not env.config.profile_measurement:
        return settle(ws, env, None)
    if "profile-capture" not in env.view.capabilities.lifecycle:
        missing = Unreachable(
            reason=UnreachableReason.PROFILE_CAPABILITY_NOT_OFFERED, subject=ws.plan.work_id
        )
        unavailable = Failure(kind=FailureKind.PROFILE_CAPTURE_UNAVAILABLE)
        return settle(ws, env, None, failure=unavailable, unreachable=missing)
    return _moved(ws, Measuring(result=None, submissions=1)), [_measure(ws, env, None, 0)]


def _measured(
    ws: Workstream,
    env: Env,
    result: Result | None,
    submitted: int,
    found: MeasurementResult,
) -> tuple[Workstream, list[Decision]]:
    """Record a measurement's trusted evidence for decoding.

    A measurement that the machinery interrupted says nothing about the candidate, and one that
    may be either's fault might be a fluke, so both are submitted again while `may_resubmit`
    allows it. Anything else is final: its evidence, failed or not, goes to decoding, so the
    agent reads why.
    """
    limit = env.config.max_input_measurement_attempts
    if may_resubmit(found.failure, submissions=submitted, limit=limit):
        measuring = Measuring(result=result, submissions=submitted + 1)
        return _moved(ws, measuring), [_measure(ws, env, result, submitted)]
    keys = trusted_keys(
        found,
        scope=found.scope,
        candidate=_measured_revision(ws, result),
        purpose="profile" if ws.plan.kind is WorkKind.PROFILE else "official",
    )
    if not keys:
        none = Failure(
            kind=FailureKind.NO_TRUSTED_EVIDENCE,
            status=found.status,
            output=found.diagnostic,
        )
        return fail(ws, env, none, result)
    refs = tuple(item for item in found.evidence if item.key in keys)
    proof = Proof(refs=refs)
    phase = Interpreting(result=result, proof=proof)
    return _moved(ws, phase), [_decode(env, INTERPRET.root, refs, ws)]


def _interpreted(
    ws: Workstream, env: Env, result: Result | None, proof: Proof, outcome: BaseModel | None
) -> tuple[Workstream, list[Decision]]:
    if not isinstance(outcome, EvidenceReadings):
        return fail(ws, env, Failure(kind=FailureKind.EVIDENCE_UNDECODABLE), result, proof=proof)
    readings = accept_readings(outcome.readings, proof.refs)
    if isinstance(readings, str):
        return fail(ws, env, Failure(kind=FailureKind.EVIDENCE_REFUSED), result, proof=proof)
    if outcome.status != "succeeded" or not readings:
        return fail(ws, env, Failure(kind=FailureKind.EVIDENCE_UNDECODABLE), result, proof=proof)
    return settle(ws, env, result, readings=readings, proof=proof)


def _finished(settling: Settling, settlement: Settlement | None) -> Done:
    return Done(
        failure=settling.failure,
        settlement=settlement,
        result=settling.result,
        readings=settling.readings,
        proof=settling.proof,
        unreachable=settling.unreachable,
    )


def _close_refused(
    ws: Workstream, env: Env, settling: Settling, event: Failed
) -> tuple[Workstream, list[Decision]]:
    """Core would not take the close: propose the plain one, and give up if that fails too.

    A close that nobody takes would leave the workstream, and its slot, waiting for ever.
    """
    failure = settling.failure or failure_of(event, None)
    if settling.refused:
        done = _finished(settling.model_copy(update={"failure": failure}), None)
        return _done(ws, done)
    plain = settling.model_copy(update={"refused": 1, "failure": failure})
    return _moved(ws, plain), [_close(plain, ws, env)]


# -- what a finished workstream leaves behind -------------------------------------------------------


def _failing_tail(done: Done) -> str:
    """What the round's failed trusted check printed, else what a failed evaluation printed.

    A benchmark that crashed the server leaves its output in the reading's feedback; that text,
    not the agent's summary, is what tells the next agent why the candidate failed.
    """
    accuracy = reading_of(done.readings, EvidenceKind.CORRECTNESS)
    benchmark = reading_of(done.readings, EvidenceKind.BENCHMARK)
    for reading in (benchmark, accuracy):
        if reading is not None and not reading.passed and reading.feedback:
            return reading.feedback
    return "" if done.failure is None else done.failure.output


def round_of(ws: Workstream, done: Done, config: DynamicConfig) -> RoundRecord:
    """The round an implement workstream adds to its hypothesis."""
    accuracy = reading_of(done.readings, EvidenceKind.CORRECTNESS)
    benchmark = reading_of(done.readings, EvidenceKind.BENCHMARK)
    result = done.result
    outcome = None if result is None else result.candidate.outcome
    next_step = "" if result is None else result.candidate.next_step
    kept = keeps_hypothesis_active(
        outcome=outcome,
        next_step=next_step,
        continuation_rounds=0,
        max_continuation_rounds=config.max_continuation_rounds,
    )
    settlement = done.settlement
    return RoundRecord(
        sequence=ws.sequence,
        attempt=ws.attempt,
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


def snapshot_of(ws: Workstream, done: Done) -> ParentSnapshot | None:
    """The exact candidate offered as a parent, once it is settled with accuracy proven."""
    accuracy = reading_of(done.readings, EvidenceKind.CORRECTNESS)
    result = done.result
    if (
        accuracy is None
        or not accuracy.passed
        or result is None
        or result.candidate.revision is None
    ):
        return None
    return ParentSnapshot(
        hypothesis_id=ws.plan.work_id,
        generation=ws.generation,
        revision=result.candidate.revision,
        accuracy=accuracy,
        benchmark=reading_of(done.readings, EvidenceKind.BENCHMARK),
        submission_index=ws.sequence,
        change_summary=result.candidate.summary or None,
    )
