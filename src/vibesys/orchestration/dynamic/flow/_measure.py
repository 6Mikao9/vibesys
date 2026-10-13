"""The trusted measurement of a candidate, and what its evidence says."""

from vibesys.candidates.api import stages
from vibesys.metrics import AcceptedReading, accept_readings
from vibesys.orchestration.dynamic.flow._outcomes import (
    Attempt,
    Evidence,
    WorkEndedError,
    WorkstreamStart,
    ended_by,
)
from vibesys.orchestration.dynamic.search.api import (
    EvidenceReadings,
    Failure,
    FailureKind,
    InterpretEvidence,
    Proof,
    WorkKind,
    measurement_recipe,
    trusted_keys,
)
from vs_core.api.search import (
    Again,
    DecisionId,
    Failed,
    Flow,
    GaveUp,
    Measure,
    MeasurementFinished,
    MeasurementPurpose,
    MeasurementResult,
    Operation,
    OperationFinished,
    RetryPolicy,
    RevisionRef,
    Tries,
    UnexpectedEvent,
    Within,
    may_resubmit,
)


async def measure_and_interpret(
    ctx: Flow, w: WorkstreamStart, attempt: Attempt, revision: RevisionRef
) -> Evidence:
    """Measure ``revision`` with the trusted evaluations and decode what the evidence says."""
    found = await measure(ctx, w, attempt, revision)
    keys = trusted_keys(found, scope=found.scope, candidate=revision, purpose=purpose_of(w))
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
    policy = RetryPolicy(max_tries=max(1, w.config.max_input_measurement_attempts))
    found: MeasurementResult | GaveUp[MeasurementResult] = await ctx.retry(
        lambda tries: _submit(ctx, w, attempt, revision, tries), policy
    )
    return found.reasons[-1] if isinstance(found, GaveUp) else found


async def _submit(
    ctx: Flow,
    w: WorkstreamStart,
    attempt: Attempt,
    revision: RevisionRef,
    tries: Tries[MeasurementResult],
) -> MeasurementResult | Again[MeasurementResult]:
    """One submission: its result, or the result of a machinery failure to submit again."""
    config = w.config
    profile = w.plan.kind is WorkKind.PROFILE
    names = ("profile",) if profile else stages(config)
    recipe = measurement_recipe(config, ctx.facts)
    if profile and w.plan.required_fields:
        recipe = recipe.requiring("profile", w.plan.required_fields)
    plan = recipe.plan(
        revision,
        purpose=purpose_of(w),
        now_at=ctx.now(),
        stages=names,
        # Core retries a submission only after an infrastructure failure, and only within
        # this bound, which core's own run limit repeats.
        submission_limit=config.max_input_measurement_attempts,
    )
    request = Measure(decision_id=DecisionId(root="measure"), scope=attempt.scope, plan=plan)
    match await ctx.wait(request, Within(limit=recipe.budget(names))):
        case MeasurementFinished(result=found):
            limit = config.max_input_measurement_attempts
            if may_resubmit(found.failure, submissions=tries.number, limit=limit):
                return Again(reason=found)
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


def purpose_of(w: WorkstreamStart) -> MeasurementPurpose:
    """What the measurement is for: a profile is looked at, anything else is scored."""
    return "profile" if w.plan.kind is WorkKind.PROFILE else "official"
