"""One workstream: a slot's worth of work, from parent verification to settlement.

The story of `workstream` is the story of the slot: prove the parent, open an attempt, let an
agent do the work, measure what it made, close the attempt with what was established. Whatever
cannot go on raises `WorkEndedError` with a typed failure, and the attempt is still closed with
what it has: an admitted attempt must always settle. An operator stop ends the workstream the
same way, wherever it is awaiting; opening and closing the attempt are never abandoned.
"""

from pydantic import BaseModel, TypeAdapter

from vibesys.candidates.api import assessment, stages, until_deadline
from vibesys.candidates.api import settle_for as candidate_settle_for
from vibesys.hypothesis import HypothesisConfig, HypothesisOutcome
from vibesys.hypothesis.cadence import review_due
from vibesys.metrics import AcceptedReading, accept_readings, reading_of
from vibesys.orchestration.dynamic.flow._outcomes import (
    STOPPED,
    Attempt,
    Candidate,
    Closing,
    Evidence,
    Result,
    Unmeasured,
    Verdict,
    WorkEndedError,
    WorkstreamDone,
    WorkstreamStart,
    blocked_failure,
    ended_by,
    failure_of,
)
from vibesys.orchestration.dynamic.flow._shield import uninterrupted
from vibesys.orchestration.dynamic.models import ImplementerResult, ReviewResult
from vibesys.orchestration.dynamic.search.api import (
    Blocker,
    BlockerKind,
    EvidenceReadings,
    Failure,
    FailureKind,
    InterpretEvidence,
    ParentVerification,
    Proof,
    Role,
    Unreachable,
    UnreachableReason,
    VerifyParentRevision,
    WorkKind,
    implement_prompt,
    measurement_recipe,
    profile_prompt,
    refusal,
    review_evaluations,
    review_prompt,
    review_refs,
    trusted_keys,
)
from vs_core.api.search import (
    THIS_RUN,
    AskAgent,
    AssessmentKind,
    AttemptBudget,
    DecisionId,
    Declined,
    Event,
    EvidenceKind,
    EvidenceRef,
    Failed,
    FailureReason,
    Flow,
    Measure,
    MeasurementFinished,
    MeasurementPurpose,
    MeasurementResult,
    Operation,
    OperationFinished,
    RevisionRef,
    RunView,
    Settle,
    Settlement,
    StartAttempt,
    Stopped,
    TurnFinished,
    UnexpectedEvent,
    Value,
    Withdraw,
    Within,
    WorkspaceClosed,
    WorkspaceMode,
    WorkspaceOpened,
    WorkspacePlan,
    item_id,
    may_resubmit,
)

_REFS = TypeAdapter(tuple[EvidenceRef, ...])


async def workstream(ctx: Flow, w: WorkstreamStart) -> WorkstreamDone:
    """Run one scheduled workstream to the settlement of its attempt."""
    if ctx.stop_requested:
        return await _ended(ctx, w, None, WorkEndedError(STOPPED))  # scheduled after the stop
    attempt: Attempt | None = None
    try:
        await verify_parent(ctx, w)
        attempt = await open_attempt(ctx, w)
        closing = await do_work(ctx, w, attempt)
    except Stopped:
        return await _ended(ctx, w, attempt, WorkEndedError(STOPPED))
    except WorkEndedError as ended:
        return await _ended(ctx, w, attempt, ended)
    return await settle(ctx, w, attempt, closing)


async def do_work(ctx: Flow, w: WorkstreamStart, attempt: Attempt) -> Closing:
    """Let the role this workstream is for do its work in the attempt."""
    if ctx.stop_requested:
        raise WorkEndedError(STOPPED)  # a stop that came while the attempt was opening
    if w.plan.kind is WorkKind.PROFILE:
        return await profile(ctx, w, attempt)
    return await implement_and_measure(ctx, w, attempt)


async def implement_and_measure(ctx: Flow, w: WorkstreamStart, attempt: Attempt) -> Closing:
    """Have the implementer make a candidate, then measure it with the trusted evaluations."""
    result = await implement_until_measurable(ctx, w, attempt)
    if result.candidate.outcome is HypothesisOutcome.BLOCKED or not stages(w.config):
        return Closing(result=result)  # nothing was made, or nothing is configured to measure
    evidence = await measure_and_interpret(ctx, w, attempt, result.candidate.revision or w.parent)
    return Closing(result=result, evidence=evidence)


async def profile(ctx: Flow, w: WorkstreamStart, attempt: Attempt) -> Closing:
    """Have the profiler look at the parent, then capture a trusted profile of it.

    What the profiler reports is never read; only what the trusted capture measures is.
    """
    config = w.config
    prompt = profile_prompt(w.plan, w.parent)
    event = await ask(
        ctx,
        attempt,
        Role.PROFILER,
        prompt,
        w.parent,
        config.profiler_turn_seconds,
        ImplementerResult,
    )
    if isinstance(event, Failed):
        raise ended_by(event, Role.PROFILER, plan=w.plan)
    if not config.profile_measurement:
        return Closing()
    if not ctx.read(_can_capture_profiles, bool):
        missing = Unreachable(
            reason=UnreachableReason.PROFILE_CAPABILITY_NOT_OFFERED, subject=w.plan.work_id
        )
        raise WorkEndedError(
            Failure(kind=FailureKind.PROFILE_CAPTURE_UNAVAILABLE), unreachable=missing
        )
    return Closing(evidence=await measure_and_interpret(ctx, w, attempt, w.parent))


# -- before the work ---------------------------------------------------------------------------------


async def verify_parent(ctx: Flow, w: WorkstreamStart) -> None:
    """A parent other than the input must still reproduce its retained content."""
    if w.parent == ctx.facts.baseline:
        return
    request = Operation(
        decision_id=DecisionId(root="verify"),
        scope=THIS_RUN,
        request=VerifyParentRevision(parent=w.parent),
    )
    match await ctx.wait(request, Within(limit=w.config.operation_seconds)):
        case OperationFinished(result=result) if _reproduces(result.outcome):
            return
        case OperationFinished():
            unverified = Failure(kind=FailureKind.PARENT_UNVERIFIED, revision=w.parent)
            raise WorkEndedError(unverified, withheld=w.parent)
        case Failed() as failed:
            raise ended_by(failed, None)
        case unexpected:
            raise UnexpectedEvent(w, unexpected)


def _reproduces(outcome: BaseModel | None) -> bool:
    return (
        isinstance(outcome, ParentVerification)
        and outcome.status == "succeeded"
        and outcome.verified
    )


async def open_attempt(ctx: Flow, w: WorkstreamStart) -> Attempt:
    """Ask core for the attempt's workspace, and wait for it even if the run is stopped."""
    isolated = w.plan.kind is WorkKind.IMPLEMENT
    request = StartAttempt(
        decision_id=DecisionId(root="start"),
        scope=THIS_RUN,
        attempt_id=w.attempt,
        item_id=item_id(w.plan.work_id, w.sequence),
        workspace=WorkspacePlan(
            mode=WorkspaceMode.ISOLATED_CHILD if isolated else WorkspaceMode.READ_ONLY_REVISION,
            base=w.parent,
        ),
        budget=AttemptBudget(
            admission_charge=1,
            paid_invocation_limit=w.config.max_retries_per_round,
            retry_limit=w.config.max_corrections + w.config.max_turn_drops,
        ),
    )
    match await uninterrupted(ctx, "open", request, ctx.read(until_deadline, Within)):
        case WorkspaceOpened(attempt=opened):
            return Attempt(ref=opened)
        case Failed() as failed:
            raise ended_by(failed, None)
        case unexpected:
            raise UnexpectedEvent(w, unexpected)


# -- the implementer, and the judge ------------------------------------------------------------------


async def implement_until_measurable(ctx: Flow, w: WorkstreamStart, attempt: Attempt) -> Result:
    """Ask the implementer (and the judge, when a review is due) until a candidate is ready.

    A turn that ends without a measurable candidate is asked again only while the progress
    policy says another turn is worth it; the workstream ends otherwise.
    """
    blockers: tuple[Blocker, ...] = ()
    while True:
        turn = await implementer_turn(ctx, w, attempt, blockers)
        if isinstance(turn, Result) and _review_due(w, turn):
            turn = await review(ctx, w, attempt, turn)
        if isinstance(turn, Result):
            return turn
        blockers = (*blockers, turn.blocker)
        attempt.carried = turn.result
        _require_another_turn(w, blockers)
        attempt.carried = None if turn.result is None else turn.result.unreviewed()


def _require_another_turn(w: WorkstreamStart, blockers: tuple[Blocker, ...]) -> None:
    """End the workstream unless the turns so far say another one is worth asking."""
    if (refused := refusal(blockers, w.config, w.parent)) is not None:
        raise WorkEndedError(refused)
    if w.plan.kind is not WorkKind.IMPLEMENT or len(blockers) >= w.config.max_retries_per_round:
        raise WorkEndedError(blocked_failure(blockers[-1]))


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


# -- the trusted measurement -------------------------------------------------------------------------


async def measure_and_interpret(
    ctx: Flow, w: WorkstreamStart, attempt: Attempt, revision: RevisionRef
) -> Evidence:
    """Measure ``revision`` with the trusted evaluations and decode what the evidence says."""
    found = await measure(ctx, w, attempt, revision)
    keys = trusted_keys(found, scope=found.scope, candidate=revision, purpose=_purpose(w))
    if not keys:
        none = Failure(
            kind=FailureKind.NO_TRUSTED_EVIDENCE, status=found.status, output=found.diagnostic
        )
        raise WorkEndedError(none)
    proof = Proof(refs=tuple(item for item in found.evidence if item.key in keys))
    return Evidence(readings=await interpret(ctx, w, attempt, proof), proof=proof)


async def measure(
    ctx: Flow, w: WorkstreamStart, attempt: Attempt, revision: RevisionRef
) -> MeasurementResult:
    """Submit the measurement, again while the machinery (not the candidate) is to blame."""
    config = w.config
    profile = w.plan.kind is WorkKind.PROFILE
    names = ("profile",) if profile else stages(config)
    recipe = measurement_recipe(config, ctx.facts)
    if profile and w.plan.required_fields:
        recipe = recipe.requiring("profile", w.plan.required_fields)
    submissions = 0
    while True:
        submissions += 1
        plan = recipe.plan(
            revision,
            purpose=_purpose(w),
            now_at=ctx.read(_now, float),
            stages=names,
            # Core retries a submission only after an infrastructure failure, and only within
            # this bound, which core's own run limit repeats.
            submission_limit=config.max_input_measurement_attempts,
        )
        request = Measure(decision_id=DecisionId(root="measure"), scope=attempt.scope, plan=plan)
        match await ctx.wait(request, Within(limit=recipe.budget(names))):
            case MeasurementFinished(result=found):
                limit = config.max_input_measurement_attempts
                if not may_resubmit(found.failure, submissions=submissions, limit=limit):
                    return found
            case Failed() as failed:
                raise ended_by(failed, None)
            case unexpected:
                raise UnexpectedEvent(w, unexpected)


async def interpret(
    ctx: Flow, w: WorkstreamStart, attempt: Attempt, proof: Proof
) -> tuple[AcceptedReading, ...]:
    """Have the evidence owner decode the trusted evidence into the readings a settlement needs."""
    request = Operation(
        decision_id=DecisionId(root="interpret"),
        scope=attempt.scope,
        request=InterpretEvidence(evidence=proof.refs),
    )
    match await ctx.wait(request, Within(limit=w.config.operation_seconds)):
        case OperationFinished(result=finished):
            outcome = finished.outcome
        case Failed() as failed:
            error = ended_by(failed, None)
            raise WorkEndedError(error.failure, proof=proof)
        case unexpected:
            raise UnexpectedEvent(w, unexpected)
    if not isinstance(outcome, EvidenceReadings):
        raise WorkEndedError(Failure(kind=FailureKind.EVIDENCE_UNDECODABLE), proof=proof)
    readings = accept_readings(outcome.readings, proof.refs)
    if isinstance(readings, str):
        raise WorkEndedError(Failure(kind=FailureKind.EVIDENCE_REFUSED), proof=proof)
    if outcome.status != "succeeded" or not readings:
        raise WorkEndedError(Failure(kind=FailureKind.EVIDENCE_UNDECODABLE), proof=proof)
    return readings


# -- the end -----------------------------------------------------------------------------------------


async def _ended(
    ctx: Flow, w: WorkstreamStart, attempt: Attempt | None, ended: WorkEndedError
) -> WorkstreamDone:
    """End a workstream that could not go on: refund a slot never used, else settle as failed."""
    if attempt is None:
        stopped = ended.failure.kind is FailureKind.STOPPED
        return WorkstreamDone(
            w, failure=ended.failure, refunded=not stopped, withheld=ended.withheld
        )
    closing = Closing(
        result=attempt.carried,
        evidence=Evidence(proof=ended.proof),
        failure=ended.failure,
        unreachable=ended.unreachable,
    )
    return await settle(ctx, w, attempt, closing)


async def settle(
    ctx: Flow, w: WorkstreamStart, attempt: Attempt, closing: Closing
) -> WorkstreamDone:
    """Close the attempt, asserting what the workstream established.

    If core will not take that assertion, the plain close (discard, failed) is proposed once:
    a close nobody takes would hold the slot for ever.
    """
    match await _close(ctx, attempt, _assertion(w, closing)):
        case WorkspaceClosed(settlement=settlement):
            return _done(w, attempt, closing, settlement)
        case Failed() as refused:
            closing = closing.model_copy(
                update={"failure": closing.failure or failure_of(refused, None)}
            )
    match await _close(ctx, attempt, _PLAIN):
        case WorkspaceClosed(settlement=settlement):
            return _done(w, attempt, closing, settlement)
        case _:
            return _done(w, attempt, closing, None)


_PLAIN = Settle(
    assessments=(), eligible=False, retention="discard", outcome="failed", candidate=None
)


def _assertion(w: WorkstreamStart, closing: Closing) -> Settle:
    """The assessments, eligibility and retention to propose for a finished attempt.

    A profile workstream measures a revision without proposing one, so it settles as ineligible
    and discarded.
    """
    if w.plan.kind is WorkKind.PROFILE:
        profile = reading_of(closing.readings, EvidenceKind.PROFILING)
        return Settle(
            assessments=assessment(AssessmentKind.PROFILING, profile, None),
            eligible=False,
            retention="discard",
            outcome="failed" if closing.failed else "succeeded",
            candidate=None,
        )
    return candidate_settle_for(closing, w.baseline, w.config)


async def _close(ctx: Flow, attempt: Attempt, disposition: Settle) -> Event:
    request = Withdraw(
        decision_id=DecisionId(root="settle"),
        scope=THIS_RUN,
        target=attempt.ref,
        disposition=disposition,
    )
    return await uninterrupted(ctx, "settle", request, ctx.read(until_deadline, Within))


def _done(
    w: WorkstreamStart, attempt: Attempt, closing: Closing, settlement: Settlement | None
) -> WorkstreamDone:
    return WorkstreamDone(
        w,
        failure=closing.failure,
        generation=attempt.ref.generation,
        settlement=settlement,
        result=closing.result,
        evidence=closing.evidence,
        unreachable=closing.unreachable,
    )


# -- asking an agent -----------------------------------------------------------------------------------


async def ask(  # noqa: PLR0913  # LW-220403 [PLR0913]; each argument is a distinct fact of the ask; bundling them in a throwaway record only renames the parameters.
    ctx: Flow,
    attempt: Attempt,
    role: Role,
    prompt: Value,
    revision: RevisionRef,
    seconds: float,
    reply: type[BaseModel],
) -> Event:
    """Ask ``role`` inside the attempt: its reply, or the typed failure that ended the turn."""
    request = AskAgent(
        decision_id=DecisionId(root=role.value),
        scope=attempt.scope,
        agent=role.value,
        input=prompt,
        revision=revision,
    )
    return await ctx.wait(request, Within(limit=seconds), reply=reply)


def _purpose(w: WorkstreamStart) -> MeasurementPurpose:
    return "profile" if w.plan.kind is WorkKind.PROFILE else "official"


def _can_capture_profiles(view: RunView) -> bool:
    return "profile-capture" in view.capabilities.lifecycle


def _now(view: RunView) -> float:
    return view.now_at
