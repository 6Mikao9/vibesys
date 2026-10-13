"""The implementer's turns and the judge's review: until a candidate is ready to measure."""

from pydantic import TypeAdapter

from vibesys.hypothesis import HypothesisConfig, HypothesisOutcome
from vibesys.hypothesis.cadence import review_due
from vibesys.metrics import AcceptedReading, accept_readings
from vibesys.orchestration.dynamic.flow._ask import ask
from vibesys.orchestration.dynamic.flow._outcomes import (
    Attempt,
    Candidate,
    Result,
    Unmeasured,
    Verdict,
    WorkEndedError,
    WorkstreamStart,
    blocked_failure,
    ended_by,
)
from vibesys.orchestration.dynamic.models import ImplementerResult, ReviewResult
from vibesys.orchestration.dynamic.search.api import (
    Blocker,
    BlockerKind,
    EvidenceReadings,
    Failure,
    FailureKind,
    InterpretEvidence,
    Role,
    implement_prompt,
    refusal,
    review_evaluations,
    review_prompt,
    review_refs,
)
from vs_core.api.search import (
    Again,
    DecisionId,
    Declined,
    EvidenceRef,
    Failed,
    FailureReason,
    Flow,
    GaveUp,
    Operation,
    OperationFinished,
    RevisionRef,
    Tries,
    TurnFinished,
    UnexpectedEvent,
    Within,
)

_REFS = TypeAdapter(tuple[EvidenceRef, ...])


async def implement_until_measurable(ctx: Flow, w: WorkstreamStart, attempt: Attempt) -> Result:
    """Ask the implementer (and the judge, when a review is due) until a candidate is ready.

    A turn that ends without a measurable candidate is asked again while the workstream's retry
    policy and the progress policy allow it; the workstream ends otherwise.
    """

    async def turn(tries: Tries[Blocker]) -> Result | Again[Blocker]:
        return await take_turn(ctx, w, attempt, tries.reasons)

    match await ctx.retry(turn, w.retries):
        case Result() as result:
            return result
        case GaveUp(reasons=blockers):
            raise WorkEndedError(blocked_failure(blockers[-1]))


async def take_turn(
    ctx: Flow, w: WorkstreamStart, attempt: Attempt, earlier: tuple[Blocker, ...]
) -> Result | Again[Blocker]:
    """One round with the implementer and the judge: a candidate, or why to ask again."""
    attempt.carried = None if attempt.carried is None else attempt.carried.unreviewed()
    turn = await implementer_turn(ctx, w, attempt, earlier)
    if isinstance(turn, Result) and _review_due(w, turn):
        turn = await review(ctx, w, attempt, turn)
    if isinstance(turn, Result):
        return turn
    attempt.carried = turn.result
    if (refused := refusal((*earlier, turn.blocker), w.config, w.parent)) is not None:
        raise WorkEndedError(refused)
    return Again(reason=turn.blocker)


async def implementer_turn(
    ctx: Flow, w: WorkstreamStart, attempt: Attempt, blockers: tuple[Blocker, ...]
) -> Result | Unmeasured:
    """One implementer turn: the candidate it made, or what kept it from making one."""
    prompt = implement_prompt(w.plan, w.parent, w.prior, blockers)
    event = await ask(
        ctx,
        attempt,
        Role.IMPLEMENTER,
        prompt,
        w.parent,
        w.config.implementer_turn_seconds,
        ImplementerResult,
    )
    match event:
        case TurnFinished(reply=ImplementerResult() as reply):
            return _candidate_of(w, attempt, event, reply)
        case Failed(reason=FailureReason.TIMED_OUT | FailureReason.TURN_FAILED) as failed:
            ended = failed.reason if failed.reason is FailureReason.TIMED_OUT else None
            return Unmeasured(
                blocker=Blocker(
                    kind=BlockerKind.NO_REPLY,
                    ended=ended,
                    turn_failure=failed.turn_failure,
                    digest=failed.digest,
                ),
                result=attempt.carried,
            )
        case Failed() as failed:
            raise ended_by(failed, Role.IMPLEMENTER, plan=w.plan)
        case unexpected:
            raise UnexpectedEvent(w, unexpected)


def _candidate_of(
    w: WorkstreamStart, attempt: Attempt, event: TurnFinished, reply: ImplementerResult
) -> Result | Unmeasured:
    """Fold the reply with core's checkpoint of the turn, which a non-blocked reply awaits."""
    revision: RevisionRef | None = None
    if reply.outcome is not HypothesisOutcome.BLOCKED:
        match event.checkpoint:
            case Declined(reason=declined):
                attempt.carried = None
                raise WorkEndedError(
                    Failure(kind=FailureKind.CHECKPOINT_DECLINED, declined=declined)
                )
            case None:
                raise UnexpectedEvent(w, event)
            case checkpoint:
                revision = checkpoint.revision
    result = Result(
        candidate=Candidate(
            outcome=reply.outcome,
            summary=reply.summary,
            next_step=reply.next_step,
            revision=revision,
        )
    )
    attempt.carried = result
    if reply.outcome is HypothesisOutcome.IMPLEMENTATION_FAILED:
        failed = Blocker(
            kind=BlockerKind.FAILED,
            summary=reply.summary,
            next_step=reply.next_step,
            revision=revision,
        )
        return Unmeasured(blocker=failed, result=result)
    if reply.outcome is not HypothesisOutcome.BLOCKED and (
        revision is None or revision == w.parent
    ):
        raise WorkEndedError(Failure(kind=FailureKind.NO_CHANGED_CANDIDATE))
    return result


def _review_due(w: WorkstreamStart, result: Result) -> bool:
    config = w.config
    hypothesis = HypothesisConfig(
        max_rounds=config.start_budget,
        judge_every=config.judge_every,
        max_retries_per_round=config.max_retries_per_round,
        max_continuation_rounds=config.max_continuation_rounds,
    )
    return result.candidate.outcome is not HypothesisOutcome.BLOCKED and review_due(
        hypothesis, round_number=w.sequence, outcome=result.candidate.outcome
    )


async def review(
    ctx: Flow, w: WorkstreamStart, attempt: Attempt, result: Result
) -> Result | Unmeasured:
    """Have the judge grade the candidate, showing it what the author's own evaluations said."""
    revision = result.candidate.revision
    assert revision is not None  # noqa: S101  # LW-220428 [S101]; only a changed candidate is reviewed.
    refs = ctx.read(lambda view: review_refs(view, attempt.ref, revision), _REFS)
    readings = await decode_evaluations(ctx, attempt, w, refs) if refs else ()
    prompt = review_prompt(
        w.plan, revision, result.candidate.summary, review_evaluations(refs, readings)
    )
    event = await ask(
        ctx, attempt, Role.JUDGE, prompt, revision, w.config.judge_turn_seconds, ReviewResult
    )
    match event:
        case TurnFinished(reply=ReviewResult() as reply, invocation=invocation):
            graded = result.model_copy(
                update={"verdict": Verdict(passed=reply.passed, invocation=invocation)}
            )
            if reply.passed:
                attempt.carried = graded
                return graded
            rejected = Blocker(
                kind=BlockerKind.REJECTED,
                summary=reply.feedback or reply.analysis,
                revision=revision,
            )
            return Unmeasured(blocker=rejected, result=graded)
        case Failed() as failed:
            raise ended_by(failed, Role.JUDGE, plan=w.plan)
        case unexpected:
            raise UnexpectedEvent(w, unexpected)


async def decode_evaluations(
    ctx: Flow, attempt: Attempt, w: WorkstreamStart, refs: tuple[EvidenceRef, ...]
) -> tuple[AcceptedReading, ...]:
    """What the evidence owner decoded of the author's own evaluations; none if it could not."""
    request = Operation(
        decision_id=DecisionId(root="review-evidence"),
        scope=attempt.scope,
        request=InterpretEvidence(evidence=refs),
    )
    match await ctx.wait(request, Within(limit=w.config.operation_seconds)):
        case OperationFinished(result=finished):
            outcome = finished.outcome
            if not isinstance(outcome, EvidenceReadings) or outcome.status != "succeeded":
                return ()
            accepted = accept_readings(outcome.readings, refs)
            return () if isinstance(accepted, str) else accepted
        case Failed() as failed:
            raise ended_by(failed, None)
        case unexpected:
            raise UnexpectedEvent(w, unexpected)
