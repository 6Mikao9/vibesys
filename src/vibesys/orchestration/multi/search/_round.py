"""One round: a hypothesis's one attempt, from opening its workspace to settling it.

A round is a child machine of the search. Its phase says what it waits for and holds only the
data valid there; `update` is one `match` over the phase and the event. The implementer works on
the attempt's workspace; when the cadence says so, the judge reviews the exact candidate it
retained, the implementer's validation recipes run, and the gates the cadence asks for measure it
and the evidence is decoded. A reply the judge fails, a recipe that fails or a gate that rejects
the candidate is repaired by another paid turn while the round's budget lasts. Then the workspace
is settled.

The round carries the hypothesis ledger it was started with and the notes it adds while it runs
(an attempt that failed, a claim approved); the search takes it back when the round is done. When
the round is over its phase is `Done`; the search records it and drops the child.
"""

from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import Field

from vibesys.candidates.api import Baseline, stages, until_deadline
from vibesys.candidates.api import settle_for as candidate_settle_for
from vibesys.hypothesis import HypothesisOutcome, OrchestratorPlan, reviewed, rounds
from vibesys.metrics import AcceptedReading, accept_readings, judged_evidence, reading_of
from vibesys.orchestration.multi.models import ImplementerResponse, JudgeResponse
from vibesys.orchestration.multi.search import _briefs as briefs
from vibesys.orchestration.multi.search._config import MultiConfig
from vibesys.orchestration.multi.search._operations import (
    VALIDATE_LOCAL_KIND,
    EvidenceReadings,
    InterpretEvidence,
    LocalValidationReport,
    ValidateLocal,
)
from vibesys.orchestration.multi.search._values import (
    DOMINATED,
    ArchiveConflictView,
    Note,
    NoteKind,
    Repair,
    RepairKind,
    Unreachable,
    UnreachableReason,
    ValidationFailure,
)
from vibesys.orchestration.review import Verdict
from vibesys.serial_search.api import (
    Failure,
    FailureKind,
    NoReply,
    NoReplyKind,
    OfficialReason,
    Role,
    StoredLedger,
    Unmeasured,
    decode_ledger,
    encode_ledger,
    failure_of,
    focus_of,
    focus_policy,
    official_reason,
)
from vs_core.api.search import (
    THIS_RUN,
    AskAgent,
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
    WorkspaceOpened,
    WorkspacePlan,
    attempt_id,
    item_id,
    may_resubmit,
    trusted_keys,
)

START = DecisionId(root="start")
SETTLE = DecisionId(root="settle")
SETTLE_AGAIN = DecisionId(root="settle.1")
INTERPRET = DecisionId(root="interpret")


# -- phases ------------------------------------------------------------------------------------------


class Opening(Value):
    """The attempt was requested; ``stopping`` records a stop that arrived before it opened."""

    kind: Literal["opening"] = "opening"
    stopping: bool = False


class Implementing(Value):
    """An implementer turn is out."""

    kind: Literal["implementing"] = "implementing"


class Reviewing(Value):
    """A judge turn is out; ``conflict`` is the live-archive conflict it was told about."""

    kind: Literal["reviewing"] = "reviewing"
    conflict: ArchiveConflictView | None = None


class Validating(Value):
    """The trusted run of the implementer's validation recipes is out."""

    kind: Literal["validating"] = "validating"


class Measuring(Value):
    """A trusted measurement is out; ``submissions`` counts how often it was submitted."""

    kind: Literal["measuring"] = "measuring"
    submissions: int = Field(ge=1)


class Interpreting(Value):
    """The trusted evidence is being decoded."""

    kind: Literal["interpreting"] = "interpreting"
    refs: tuple[EvidenceRef, ...]


class Settling(Value):
    """The close of the workspace, with what it asserts, is out.

    ``refused`` counts the closes core refused; the second proposal is the plain one.
    """

    kind: Literal["settling"] = "settling"
    refused: int = Field(default=0, ge=0)


class Done(Value):
    """The round is over; the search records it and drops the child.

    ``refunded`` says the attempt was never admitted, so its slot was never used. ``settlement``
    is what core settled, or None when the round ended without core's settlement.
    """

    kind: Literal["done"] = "done"
    refunded: bool = False
    settlement: Settlement | None = None


type RoundPhase = Annotated[
    Opening | Implementing | Reviewing | Validating | Measuring | Interpreting | Settling | Done,
    Field(discriminator="kind"),
]


class RoundStart(Value):
    """What a round starts from: the hypothesis, the plan, the revision and the ledger."""

    number: int = Field(gt=0)
    hypothesis_id: str = Field(min_length=1)
    plan_json: str
    base: RevisionRef
    ledger: StoredLedger
    carried: Repair | None = None
    """What the hypothesis's previous round left to repair, for a continued hypothesis."""
    revalidation_required: bool = False
    """The ledger says a gate rejected the hypothesis, so its next review is not skipped."""
    planned: OfficialReason | None = None
    """Why the official gates are scheduled after this round, as the prompts show it."""


class Round(Value):
    """One round: its plan, its attempt, what it has established and the phase it is in."""

    kind: Literal["round"] = "round"
    number: int = Field(gt=0)
    hypothesis_id: str = Field(min_length=1)
    plan_json: str
    base: RevisionRef
    attempt: AttemptId
    generation: int = Field(default=0, ge=0)
    ledger: StoredLedger
    phase: RoundPhase
    spent: int = Field(default=0, ge=0)
    """Paid implementer turns charged so far, bounded by ``max_retries_per_round``."""
    candidate: RevisionRef | None = None
    response_json: str | None = None
    """The last valid implementer reply (JSON)."""
    review_started: bool = False
    """A review has started in this round, so it must finish; a gate failure reopens review."""
    review_skipped: bool = False
    revalidation_required: bool = False
    judge_pass: bool | None = None
    judge_invocation: InvocationRef | None = None
    repair: Repair | None = None
    no_reply: NoReply | None = None
    planned: OfficialReason | None = None
    official: OfficialReason | None = None
    readings: tuple[AcceptedReading, ...] = ()
    passed: bool = False
    gate_failed: bool = False
    unmeasured: Unmeasured | None = None
    failure: Failure | None = None
    notes: tuple[Note, ...] = ()
    unreachable: tuple[Unreachable, ...] = ()

    @property
    def ref(self) -> AttemptRef:
        """The exact attempt generation."""
        return AttemptRef(attempt_id=self.attempt, generation=self.generation)

    @property
    def scope(self) -> Scope:
        """The scope the attempt's own work runs in."""
        return Scope(owner=self.attempt, generation=self.generation)

    @property
    def plan(self) -> OrchestratorPlan:
        """The designer's validated plan."""
        return OrchestratorPlan.model_validate_json(self.plan_json)

    @property
    def response(self) -> ImplementerResponse | None:
        """The last valid implementer reply, if any."""
        if self.response_json is None:
            return None
        return ImplementerResponse.model_validate_json(self.response_json)

    @property
    def review_passed(self) -> bool | None:
        """The judge's verdict, which the Pareto guard may have turned into a failure."""
        return self.judge_pass

    @property
    def outcome(self) -> HypothesisOutcome | None:
        """The hypothesis outcome the implementer reported, if it replied."""
        reply = self.response
        return None if reply is None else reply.hypothesis_outcome

    @property
    def stopped(self) -> bool:
        """Whether the operator stopped the attempt."""
        return self.failure is not None and self.failure.kind is FailureKind.STOPPED

    @property
    def failed(self) -> bool:
        """Whether the attempt recorded a failure, an operator stop included."""
        return self.failure is not None


@dataclass(frozen=True)
class Env:
    """What a round reads and never keeps: the policy, the baseline and the run's view."""

    config: MultiConfig
    baseline: Baseline
    view: RunView
    recipe: MeasurementRecipe


# -- decisions ---------------------------------------------------------------------------------------


def _start(rnd: Round, env: Env) -> Decision:
    config = env.config
    request = StartAttempt(
        decision_id=START,
        scope=THIS_RUN,
        attempt_id=rnd.attempt,
        item_id=item_id(rnd.hypothesis_id, rnd.number),
        workspace=WorkspacePlan(mode=config.workspace_mode, base=rnd.base),
        budget=AttemptBudget(
            admission_charge=1,
            paid_invocation_limit=config.max_retries_per_round,
            retry_limit=config.max_corrections + config.max_turn_drops,
        ),
    )
    return Wait(request=request, until=until_deadline(env.view))


def _ask(rnd: Round, env: Env, *, repair: Repair | None, no_reply: NoReply | None) -> Decision:
    ledger = decode_ledger(rnd.ledger, env.config.metric_space)
    prompt = briefs.implement_prompt(
        number=rnd.number,
        retry=rnd.spent + 1,
        plan=rnd.plan,
        base=rnd.base,
        config=env.config,
        hypothesis=ledger.search.active_hypothesis,
        focus=focus_of(ledger, focus_policy(env.config.attribution)),
        repair=repair,
        no_reply=no_reply,
        official_reason=rnd.planned,
    )
    request = AskAgent(
        decision_id=DecisionId(root=f"implementer.{rnd.spent}"),
        scope=rnd.scope,
        agent=Role.IMPLEMENTER.value,
        input=prompt,
        revision=rnd.base,
    )
    return Wait(request=request, until=Within(limit=env.config.implementer_turn_seconds))


def _review(rnd: Round, env: Env, reviewing: Reviewing) -> Decision:
    """The judge reads the exact candidate, in a conversation of its own for each review."""
    reply = rnd.response
    if reply is None or rnd.candidate is None:
        message = "a review needs the implementer's reply and its candidate"
        raise ValueError(message)
    ledger = decode_ledger(rnd.ledger, env.config.metric_space)
    prompt = briefs.judge_prompt(
        number=rnd.number,
        retry=rnd.spent,
        plan=rnd.plan,
        config=env.config,
        hypothesis=ledger.search.active_hypothesis,
        reply=reply,
        conflict=reviewing.conflict,
        official_reason=rnd.planned,
        cited_evidence=judged_evidence(env.view, rnd.scope),
    )
    request = AskAgent(
        decision_id=DecisionId(root=f"review.{rnd.spent}/{Role.JUDGE.value}"),
        scope=rnd.scope,
        agent=Role.JUDGE.value,
        input=prompt,
        revision=rnd.candidate,
    )
    return Wait(request=request, until=Within(limit=env.config.judge_turn_seconds))


def _validate(rnd: Round, env: Env, recipe: str) -> Decision:
    if rnd.candidate is None:
        message = "a validated round has a candidate"
        raise ValueError(message)
    request = Operation(
        decision_id=DecisionId(root=f"validate.{rnd.spent}"),
        scope=rnd.scope,
        request=ValidateLocal(revision=rnd.candidate, recipe_artifact=recipe),
    )
    return Wait(request=request, until=Within(limit=env.config.operation_seconds))


def _measure(rnd: Round, env: Env, index: int) -> Decision:
    names = stages(env.config)
    if rnd.candidate is None:
        message = "a measured round has a candidate"
        raise ValueError(message)
    plan = env.recipe.plan(
        rnd.candidate,
        purpose="official",
        now_at=env.view.now_at,
        stages=names,
        # Core retries a submission only after an infrastructure failure, and only within
        # this bound, which core's own run limit repeats.
        submission_limit=env.config.max_input_measurement_attempts,
    )
    request = Measure(decision_id=DecisionId(root=f"measure.{index}"), scope=rnd.scope, plan=plan)
    return Wait(request=request, until=Within(limit=env.recipe.budget(names)))


def _decode(rnd: Round, env: Env, refs: tuple[EvidenceRef, ...]) -> Decision:
    request = Operation(
        decision_id=INTERPRET, scope=rnd.scope, request=InterpretEvidence(evidence=refs)
    )
    return Wait(request=request, until=Within(limit=env.config.operation_seconds))


def _close(rnd: Round, settling: Settling, env: Env) -> Decision:
    """The close that settles the attempt: the full assertion, then the plain one."""
    if settling.refused:
        disposition = Settle(
            assessments=(), eligible=False, retention="discard", outcome="failed", candidate=None
        )
        name = SETTLE_AGAIN
    else:
        disposition = _disposition(rnd, env)
        name = SETTLE
    request = Withdraw(decision_id=name, scope=THIS_RUN, target=rnd.ref, disposition=disposition)
    return Wait(request=request, until=until_deadline(env.view))


def _disposition(rnd: Round, env: Env) -> Settle:
    """The assessments, eligibility and retention to propose for the finished attempt.

    A round no independent verdict supports can never be adopted. A round that passed review but
    was not measured retains its checkpoint as a buildable parent; a round that failed after
    producing a checkpoint keeps it as work in progress, so a continued hypothesis builds on it.
    """
    settled = candidate_settle_for(rnd, env.baseline, env.config)
    if settled.eligible and rnd.judge_pass is not True:
        settled = settled.model_copy(update={"eligible": False})
    if settled.retention == "discard" and rnd.candidate is not None and rnd.failure is None:
        return settled.model_copy(
            update={
                "retention": "candidate" if rnd.passed else "wip",
                "candidate": rnd.candidate,
                "eligible": False,
            }
        )
    return settled


# -- moving between phases -----------------------------------------------------------------------------


def _moved(rnd: Round, phase: RoundPhase, **update: object) -> Round:
    return rnd.model_copy(update={"phase": phase, **update})


def _settle(rnd: Round, env: Env, **update: object) -> tuple[Round, list[Decision]]:
    """Close the workspace, asserting what the round established."""
    settling = Settling()
    moved = _moved(rnd, settling, **update)
    return moved, [_close(moved, settling, env)]


def fail(
    rnd: Round, env: Env, failure: Failure, unreachable: Unreachable | None = None
) -> tuple[Round, list[Decision]]:
    """End the round with a typed failure; an admitted attempt must still settle."""
    gaps = rnd.unreachable if unreachable is None else (*rnd.unreachable, unreachable)
    if isinstance(rnd.phase, Opening):
        return _moved(rnd, Done(refunded=True), failure=failure), []
    return _settle(rnd, env, failure=failure, unreachable=gaps)


def begin(start: RoundStart, env: Env) -> tuple[Round, list[Decision]]:
    """A started round: its attempt is requested at once."""
    rnd = Round(
        number=start.number,
        hypothesis_id=start.hypothesis_id,
        plan_json=start.plan_json,
        base=start.base,
        attempt=attempt_id(start.hypothesis_id, start.number),
        ledger=start.ledger,
        repair=start.carried,
        revalidation_required=start.revalidation_required,
        planned=start.planned,
        phase=Opening(),
    )
    return rnd, [_start(rnd, env)]


def _attempt(
    rnd: Round, env: Env, *, repair: Repair | None, no_reply: NoReply | None
) -> tuple[Round, list[Decision]]:
    """Ask the implementer for the next paid turn, with nothing of the last attempt's review."""
    ask = _ask(rnd, env, repair=repair, no_reply=no_reply)
    moved = _moved(
        rnd,
        Implementing(),
        spent=rnd.spent + 1,
        repair=repair,
        no_reply=no_reply,
        readings=(),
        judge_pass=None,
        judge_invocation=None,
        review_skipped=False,
        official=None,
    )
    return moved, [ask]


# -- update ---------------------------------------------------------------------------------------


def update(rnd: Round, event: Event, env: Env) -> tuple[Round, list[Decision]]:  # noqa: C901, PLR0911, PLR0912  # LW-220504 [C901, PLR0911, PLR0912]; one exhaustive match over (phase, event) is the machine; splitting it across handlers would hide which phases take which events.
    """One event: move to the next phase and say what that phase needs."""
    match rnd.phase, event:
        # an operator stop reaches every phase; it never cancels a turn, it settles around it
        case Opening(), StopRequested():
            return _moved(rnd, Opening(stopping=True)), []
        case Settling() | Done(), StopRequested():
            return rnd, []
        case (
            Implementing() | Reviewing() | Validating() | Measuring() | Interpreting(),
            StopRequested(),
        ):
            return _settle(rnd, env, failure=Failure(kind=FailureKind.STOPPED))

        case Opening(stopping=stopping), WorkspaceOpened(attempt=opened):
            opened_round = rnd.model_copy(update={"generation": opened.generation})
            if stopping:
                return _settle(opened_round, env, failure=Failure(kind=FailureKind.STOPPED))
            return _attempt(opened_round, env, repair=rnd.repair, no_reply=None)
        case Opening(), Failed():
            return fail(rnd, env, failure_of(event, None))

        case Implementing(), TurnFinished(reply=ImplementerResponse() as reply):
            return _implemented(rnd, env, reply, event)
        case Implementing(), Failed(reason=FailureReason.INVALID_REPLY, errors=errors):
            return _no_reply(rnd, env, NoReply(kind=NoReplyKind.INVALID_REPLY, errors=errors))
        case Implementing(), Failed(reason=FailureReason.TIMED_OUT | FailureReason.TURN_FAILED):
            return _no_reply(rnd, env, _unanswered(event))
        case Implementing(), Failed():
            failure = failure_of(event, Role.IMPLEMENTER)
            return fail(rnd, env, failure, _unreachable_of(rnd, failure))

        case Reviewing(conflict=conflict), TurnFinished(
            reply=JudgeResponse() as verdict, invocation=invocation
        ):
            return _judged(rnd, env, verdict, conflict, invocation)
        case Reviewing(), Failed(reason=FailureReason.INVALID_REPLY, errors=errors):
            return _judge_lost(rnd, env, NoReply(kind=NoReplyKind.INVALID_REPLY, errors=errors))
        case Reviewing(), Failed(reason=FailureReason.TIMED_OUT | FailureReason.TURN_FAILED):
            return _judge_lost(rnd, env, _unanswered(event))
        case Reviewing(), Failed():
            failure = failure_of(event, Role.JUDGE)
            return fail(rnd, env, failure, _unreachable_of(rnd, failure))

        case Validating(), OperationFinished(result=finished):
            return _validated(rnd, env, event, finished.outcome)

        case Measuring(submissions=submitted), MeasurementFinished(result=found):
            return _measured(rnd, env, submitted, found)
        case Validating() | Measuring() | Interpreting(), Failed():
            return fail(rnd, env, failure_of(event, None))

        case Interpreting(refs=refs), OperationFinished(result=finished):
            return _interpreted(rnd, env, refs, finished.outcome)

        case Settling(), WorkspaceClosed(settlement=settlement):
            return _moved(rnd, Done(settlement=settlement)), []
        case Settling(), Failed(decision=decision) if decision in (SETTLE, SETTLE_AGAIN):
            return _close_refused(rnd, env, event)
        case Settling(), Failed():
            # a late failure of work the settlement already went around: it only fills in why
            late = rnd.failure or failure_of(event, None)
            return rnd.model_copy(update={"failure": late}), []
        case Settling(), TurnFinished() | OperationFinished() | MeasurementFinished():
            return rnd, []

        case _:
            raise UnexpectedEvent(rnd.phase, event)


def _unanswered(event: Failed) -> NoReply:
    """A turn that ended without a reply, with how it ended."""
    ended = event.reason if event.reason is FailureReason.TIMED_OUT else None
    return NoReply(kind=NoReplyKind.NO_REPLY, ended=ended, turn_failure=event.turn_failure)


def _unreachable_of(rnd: Round, failure: Failure) -> Unreachable | None:
    if failure.kind is FailureKind.WAIT_REFUSED and failure.refusal is WaitRefusal.NOT_OFFERED:
        return Unreachable(
            reason=UnreachableReason.RESUME_CAPABILITY_NOT_OFFERED, subject=rnd.hypothesis_id
        )
    return None


def _no_reply(rnd: Round, env: Env, no_reply: NoReply) -> tuple[Round, list[Decision]]:
    """The attempt produced no valid response: retry within the paid budget or settle the round.

    A round that ends this way settles without a failure, so the candidate it may have
    retained earlier is still kept as work in progress.
    """
    cleared = rnd.model_copy(update={"response_json": None, "judge_pass": None})
    if rnd.spent >= env.config.max_retries_per_round:
        return _settle(cleared, env, no_reply=no_reply)
    return _attempt(cleared, env, repair=None, no_reply=no_reply)


def _fail_attempt(rnd: Round, env: Env, repair: Repair) -> tuple[Round, list[Decision]]:
    """A repairable failure: another attempt may repair it, or the round ends."""
    if rnd.spent >= env.config.max_retries_per_round:
        return _settle(rnd, env, repair=repair)
    return _attempt(rnd, env, repair=repair, no_reply=None)


def _noted(rnd: Round, env: Env, sentence: str, gate: rounds.GateFailure | None = None) -> Round:
    """The round with the failed attempt noted in its ledger."""
    config = env.config
    ledger = decode_ledger(rnd.ledger, config.metric_space)
    noted = rounds.note_attempt_failure(config.hypotheses, ledger, sentence, gate)
    return rnd.model_copy(update={"ledger": encode_ledger(noted)})


def _implemented(
    rnd: Round, env: Env, reply: ImplementerResponse, event: TurnFinished
) -> tuple[Round, list[Decision]]:
    """Take a valid reply with its checkpoint, then review it when the cadence says so."""
    match event.checkpoint:
        case Declined(reason=declined):
            return fail(rnd, env, Failure(kind=FailureKind.CHECKPOINT_DECLINED, declined=declined))
        case None:
            raise UnexpectedEvent(rnd.phase, event)
        case checkpoint:
            revision = checkpoint.revision
    config = env.config
    ledger = decode_ledger(rnd.ledger, config.metric_space)
    held = rnd.model_copy(
        update={"response_json": reply.model_dump_json(), "candidate": revision, "no_reply": None}
    )
    due = reviewed.review_due(
        config.hypotheses,
        ledger,
        reply,
        round_number=rnd.number,
        review_started=rnd.review_started,
        revalidation_required=rnd.revalidation_required,
    )
    if not due:
        return _settle(held, env, review_skipped=True)
    conflict = rounds.pareto_conflict(config.hypotheses, ledger, reply)
    reviewing = Reviewing(conflict=None if conflict is None else briefs.conflict_view(conflict))
    moved = _moved(held, reviewing, review_started=True, revalidation_required=False)
    return moved, [_review(moved, env, reviewing)]


def _judge_lost(rnd: Round, env: Env, no_reply: NoReply) -> tuple[Round, list[Decision]]:
    """The judge gave no verdict: the candidate is intact, so the round ends unreviewed.

    It is retained as work in progress instead of paying for another implementation.
    """
    note = Note(kind=NoteKind.JUDGE_LOST, no_reply=no_reply)
    return _settle(rnd, env, judge_pass=None, judge_invocation=None, notes=(*rnd.notes, note))


def _judged(
    rnd: Round,
    env: Env,
    verdict: JudgeResponse,
    conflict: ArchiveConflictView | None,
    invocation: InvocationRef,
) -> tuple[Round, list[Decision]]:
    """Apply the Pareto guard to the verdict, then repair, validate or measure."""
    passing = verdict.verdict is Verdict.PASS and conflict is None
    judged = rnd.model_copy(update={"judge_pass": passing, "judge_invocation": invocation})
    if passing:
        return _after_review(judged, env)
    if conflict is not None and verdict.verdict is Verdict.PASS:
        repair = Repair(kind=RepairKind.ARCHIVE, text=verdict.feedback, conflict=conflict)
    else:
        text = verdict.feedback or (DOMINATED if conflict is not None else "")
        repair = Repair(kind=RepairKind.REVIEW, text=text)
    return _fail_attempt(_noted(judged, env, repair.sentence), env, repair)


def _after_review(rnd: Round, env: Env) -> tuple[Round, list[Decision]]:
    """A passing review: validate the implementer's recipes when it wrote any, else approve."""
    reply = rnd.response
    recipe = None if reply is None else reply.validation_recipe_artifact
    if recipe is None or rnd.candidate is None:
        return _approve(rnd, env)
    offered = any(item.kind == VALIDATE_LOCAL_KIND for item in env.view.capabilities.operations)
    if not offered:
        missing = Unreachable(
            reason=UnreachableReason.VALIDATE_LOCAL_NOT_OFFERED, subject=rnd.hypothesis_id
        )
        return _approve(rnd.model_copy(update={"unreachable": (*rnd.unreachable, missing)}), env)
    moved = _moved(rnd, Validating())
    return moved, [_validate(moved, env, recipe)]


def _validated(rnd: Round, env: Env, event: Event, outcome: object) -> tuple[Round, list[Decision]]:
    """Fold the recipes' trusted run: a failure is feedback for another attempt."""
    if not isinstance(outcome, LocalValidationReport):
        raise UnexpectedEvent(rnd.phase, event)
    if outcome.status != "succeeded":
        # The run could not be validated (nothing to blame on the candidate): approve it on the
        # judge's verdict, as a run that does not offer validation does.
        note = Note(kind=NoteKind.VALIDATION_UNRUN, detail=outcome.detail)
        return _approve(rnd.model_copy(update={"notes": (*rnd.notes, note)}), env)
    if outcome.passed:
        return _approve(rnd, env)
    failure = ValidationFailure(
        recipe=outcome.recipe,
        error=outcome.error,
        output=outcome.output,
        recipe_unusable=outcome.recipe_unusable,
    )
    repair = Repair(kind=RepairKind.VALIDATION, validation=failure)
    return _fail_attempt(_noted(rnd, env, repair.sentence), env, repair)


def _approve(rnd: Round, env: Env) -> tuple[Round, list[Decision]]:
    """Approve the reply's claims, then settle or schedule the official gates."""
    reply = rnd.response
    if reply is None:
        return _settle(rnd, env)
    config = env.config
    ledger = reviewed.approve_candidate(
        config.hypotheses, decode_ledger(rnd.ledger, config.metric_space), reply
    )
    reason = official_reason(
        config.hypotheses,
        ledger,
        round_number=rnd.number,
        requested=rnd.plan.request_official_evaluation,
        candidate_ready=reviewed.candidate_ready(reply),
    )
    measurable = config.accuracy_configured or config.benchmark_configured
    if reason is None or not measurable or rnd.candidate is None:
        approved = rnd.model_copy(update={"ledger": encode_ledger(ledger)})
        return _settle(approved, env, passed=True, official=None)
    ledger = reviewed.approve_performance(config.hypotheses, ledger, reply)
    moved = _moved(rnd, Measuring(submissions=1), ledger=encode_ledger(ledger), official=reason)
    return moved, [_measure(moved, env, 0)]


def _measured(
    rnd: Round, env: Env, submitted: int, found: MeasurementResult
) -> tuple[Round, list[Decision]]:
    """Record a measurement's trusted evidence for decoding.

    A measurement the machinery interrupted says nothing about the candidate, so it is submitted
    again while `may_resubmit` allows it. Anything else is final: its evidence, failed or not,
    goes to decoding, so the agent reads why. A measurement that left no trusted evidence says
    nothing against the candidate either, so the round settles without a failure and the agent
    is not asked to repair the infrastructure.
    """
    if rnd.candidate is None:
        message = "a measured round has a candidate"
        raise ValueError(message)
    limit = env.config.max_input_measurement_attempts
    if may_resubmit(found.failure, submissions=submitted, limit=limit):
        again = _moved(rnd, Measuring(submissions=submitted + 1))
        return again, [_measure(again, env, submitted)]
    keys = trusted_keys(found, scope=found.scope, candidate=rnd.candidate, purpose="official")
    if not keys:
        unmeasured = Unmeasured(status=found.status, output=found.diagnostic)
        return _settle(rnd, env, unmeasured=unmeasured)
    refs = tuple(item for item in found.evidence if item.key in keys)
    return _moved(rnd, Interpreting(refs=refs)), [_decode(rnd, env, refs)]


def _interpreted(
    rnd: Round, env: Env, refs: tuple[EvidenceRef, ...], outcome: object
) -> tuple[Round, list[Decision]]:
    if not isinstance(outcome, EvidenceReadings):
        return fail(rnd, env, Failure(kind=FailureKind.EVIDENCE_UNDECODABLE))
    readings = accept_readings(outcome.readings, refs)
    if isinstance(readings, str):
        return fail(rnd, env, Failure(kind=FailureKind.EVIDENCE_REFUSED))
    if outcome.status != "succeeded" or not readings:
        return fail(rnd, env, Failure(kind=FailureKind.EVIDENCE_UNDECODABLE))
    accuracy = reading_of(readings, EvidenceKind.CORRECTNESS)
    benchmark = reading_of(readings, EvidenceKind.BENCHMARK)
    read = rnd.model_copy(update={"readings": readings})
    for reading in (accuracy, benchmark):
        if reading is not None and not reading.passed:
            return _gate_failed(read, env, reading.kind, reading.feedback or "", accuracy)
    return _settle(read, env, passed=True)


def _gate_failed(
    rnd: Round, env: Env, gate: EvidenceKind, text: str, accuracy: AcceptedReading | None
) -> tuple[Round, list[Decision]]:
    """A trusted gate rejected the candidate: another attempt may repair it, or the round ends."""
    repair = Repair(kind=RepairKind.GATE, text=text, gate=gate)
    noted = _noted(
        rnd,
        env,
        repair.sentence,
        rounds.GateFailure(
            None if rnd.candidate is None else rnd.candidate.revision_id.root,
            accuracy_passed=accuracy is not None and accuracy.passed,
        ),
    )
    failed = noted.model_copy(
        update={"gate_failed": True, "passed": False, "revalidation_required": True}
    )
    return _fail_attempt(failed, env, repair)


def _close_refused(rnd: Round, env: Env, event: Failed) -> tuple[Round, list[Decision]]:
    """Core would not take the close: propose the plain one, and give up if that fails too.

    A close that nobody takes would leave the round, and its slot, waiting for ever. When the
    plain close is refused as well the round ends here with its failure and no settlement; the
    search closes it from its state.
    """
    failure = rnd.failure or failure_of(event, None)
    if isinstance(rnd.phase, Settling) and rnd.phase.refused:
        return _moved(rnd, Done(), failure=failure), []
    plain = Settling(refused=1)
    moved = _moved(rnd, plain, failure=failure)
    return moved, [_close(moved, plain, env)]
