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
from vs_core.api import (
    THIS_RUN,
    DecisionId,
    EvidenceReadingsBase,
    EvidenceRef,
    Failed,
    Flow,
    Measure,
    MeasurementFinished,
    Operation,
    OperationFinished,
    ProposeWinner,
    RunView,
    UnexpectedEvent,
    Within,
    may_resubmit,
    trusted_keys,
)

_MEASURE = DecisionId(root="measure")
_INTERPRET = DecisionId(root="interpret")
_RUN_START = 0.0
"""A run starts at 0 on its own clock, and the first measurement is asked before any event."""


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
    submissions = 0
    while True:
        submissions += 1
        now_at = _RUN_START if submissions == 1 else ctx.read(_now, float)
        plan = measuring.recipe.plan(
            ctx.facts.baseline,
            purpose="baseline",
            now_at=now_at,
            stages=names,
            submission_limit=limit,
        )
        request = Measure(decision_id=_MEASURE, scope=THIS_RUN, plan=plan)
        match await ctx.wait(request, Within(limit=measuring.recipe.budget(names))):
            case MeasurementFinished(result=result):
                if may_resubmit(result.failure, submissions=submissions, limit=limit):
                    continue
                keys = trusted_keys(
                    result, scope=result.scope, candidate=ctx.facts.baseline, purpose="baseline"
                )
                if not keys:
                    none = InputFailure(
                        kind=InputFailureKind.NO_EVIDENCE,
                        status=result.status,
                        output=result.diagnostic,
                    )
                    return Unmeasurable(attempts=submissions, failure=none)
                refs = tuple(item for item in result.evidence if item.key in keys)
                return await _decode(ctx, measuring, submissions, refs)
            case Failed() as failed:
                return Unmeasurable(attempts=submissions, failure=_ended(failed))
            case unexpected:
                raise UnexpectedEvent("measure_input", unexpected)


async def _decode(
    ctx: Flow, measuring: InputMeasurement, submissions: int, refs: tuple[EvidenceRef, ...]
) -> Baseline:
    """Ask the search's evidence owner what the trusted evidence of the input says."""
    request = Operation(decision_id=_INTERPRET, scope=THIS_RUN, request=measuring.interpret(refs))
    match await ctx.wait(request, Within(limit=measuring.operation_seconds)):
        case OperationFinished(result=result):
            outcome = result.outcome
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


def _now(view: RunView) -> float:
    return view.now_at
