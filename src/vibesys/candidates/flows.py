"""The shared candidate lifecycle as flows: measure the input, and adopt the winner.

These are the straight-line forms of `baseline` (the input measurement) and `selection` (the
adoption of the winner). A search written as a flow awaits them; a search written as a state
machine keeps embedding the machines, which stay the single home of the judgments both forms
share (`input_from_readings`, `update_selection`).
"""

from vibesys.candidates.baseline import (
    Baseline,
    InputFailure,
    InputFailureKind,
    InputMeasurement,
    NotConfigured,
    Unmeasurable,
    input_from_readings,
    stages,
)
from vibesys.candidates.config import CandidateConfig
from vibesys.candidates.selection import (
    PROPOSE,
    Adopting,
    Winner,
    selection_of,
    until_deadline,
)
from vibesys.candidates.selection import update as update_selection
from vs_core.api.search import (
    THIS_RUN,
    Again,
    DecisionId,
    EvidenceReadingsBase,
    Failed,
    Flow,
    GaveUp,
    Measure,
    MeasurementFinished,
    MeasurementResult,
    Operation,
    OperationFinished,
    ProposeWinner,
    RetryPolicy,
    Tries,
    UnexpectedEvent,
    Value,
    Within,
    may_resubmit,
    trusted_keys,
)

_MEASURE = DecisionId(root="measure")
_INTERPRET = DecisionId(root="interpret")
_RUN_START = 0.0
"""A run starts at 0 on its own clock, and the first measurement is asked before any event."""


class Submitted(Value):
    """A measurement of the input, and how many submissions it took to get it."""

    result: MeasurementResult
    submissions: int


async def measure_input(
    ctx: Flow, config: CandidateConfig, measuring: InputMeasurement
) -> Baseline:
    """Measure the input revision and decode its evidence; the result is typed, never raised.

    A measurement that ends in an infrastructure failure is submitted again while `may_resubmit`
    allows it. An input with no gate configured has nothing to measure.
    """
    names = stages(config)
    if not names:
        return NotConfigured()
    limit = config.max_input_measurement_attempts
    policy = RetryPolicy(max_tries=max(1, limit))
    done: Submitted | Unmeasurable | GaveUp[Submitted] = await ctx.retry(
        lambda tries: _submit(ctx, measuring, names, tries, limit), policy
    )
    if isinstance(done, Unmeasurable):
        return done
    return await _decode(ctx, measuring, done.reasons[-1] if isinstance(done, GaveUp) else done)


async def _submit(
    ctx: Flow,
    measuring: InputMeasurement,
    names: tuple[str, ...],
    tries: Tries[Submitted],
    limit: int,
) -> Submitted | Unmeasurable | Again[Submitted]:
    """Submit the measurement once: its result, to be submitted again or decoded."""
    plan = measuring.recipe.plan(
        ctx.facts.baseline,
        purpose="baseline",
        now_at=_RUN_START if tries.number == 1 else ctx.now(),
        stages=names,
        submission_limit=limit,
    )
    request = Measure(decision_id=_MEASURE, scope=THIS_RUN, plan=plan)
    match await ctx.wait(request, Within(limit=measuring.recipe.budget(names))):
        case MeasurementFinished(result=result):
            submitted = Submitted(result=result, submissions=tries.number)
            if may_resubmit(result.failure, submissions=tries.number, limit=limit):
                return Again(reason=submitted)
            return submitted
        case Failed() as failed:
            return Unmeasurable(attempts=tries.number, failure=_ended(failed))
        case unexpected:
            raise UnexpectedEvent("measure_input", unexpected)


async def _decode(ctx: Flow, measuring: InputMeasurement, submitted: Submitted) -> Baseline:
    """Ask the search's evidence owner what the trusted evidence of the input says."""
    result, submissions = submitted.result, submitted.submissions
    keys = trusted_keys(
        result, scope=result.scope, candidate=ctx.facts.baseline, purpose="baseline"
    )
    if not keys:
        none = InputFailure(
            kind=InputFailureKind.NO_EVIDENCE, status=result.status, output=result.diagnostic
        )
        return Unmeasurable(attempts=submissions, failure=none)
    refs = tuple(item for item in result.evidence if item.key in keys)
    request = Operation(decision_id=_INTERPRET, scope=THIS_RUN, request=measuring.interpret(refs))
    match await ctx.wait(request, Within(limit=measuring.operation_seconds)):
        case OperationFinished(result=finished):
            outcome = finished.outcome
            if not isinstance(outcome, EvidenceReadingsBase):
                undecoded = InputFailure(kind=InputFailureKind.READINGS_UNDECODABLE)
                return Unmeasurable(attempts=submissions, failure=undecoded)
            return input_from_readings(submissions, refs, outcome.status, tuple(outcome.readings))
        case Failed() as failed:
            return Unmeasurable(attempts=submissions, failure=_ended(failed))
        case unexpected:
            raise UnexpectedEvent("measure_input", unexpected)


def _ended(failed: Failed) -> InputFailure:
    return InputFailure(kind=InputFailureKind.WAIT_ENDED, ended=failed.reason, cause=failed.cause)


async def adopt(ctx: Flow, winner: Winner) -> Winner | None:
    """Propose the winner for adoption, once; the winner core adopted, or None."""
    proposal = ProposeWinner(decision_id=PROPOSE, scope=THIS_RUN, selection=selection_of(winner))
    event = await ctx.wait(proposal, ctx.read(until_deadline, Within))
    return update_selection(Adopting(winner=winner), event)
