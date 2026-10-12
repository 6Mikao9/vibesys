"""The dynamic search as a story: measure the input, plan and run workstreams, then adopt a winner.

This is the whole search, top to bottom. Planning and the workstreams are children, so they run
concurrently with each other; the root decides only when to start one and when the search is
over. `RunRecord` holds what the search knows, and the children report into it. An operator stop
reaches the workstreams (they settle what they started); the root sees it as `stop_requested`,
plans no more, and finishes once nothing is in flight.
"""

from vibesys.candidates import api as candidates
from vibesys.hypothesis import RunEnding
from vibesys.orchestration.dynamic.flow._planner import plan
from vibesys.orchestration.dynamic.flow._run_state import RunRecord
from vibesys.orchestration.dynamic.flow._workstream import workstream
from vibesys.orchestration.dynamic.search.api import (
    INTERPRET_KIND,
    OPERATIONS,
    STATE_SCHEMA,
    STRATEGY_ID,
    VERIFY_KIND,
    DynamicConfig,
    InterpretEvidence,
    measurement_recipe,
)
from vs_core.api.search import (
    THIS_RUN,
    DecisionId,
    EventKind,
    FailureReason,
    Finish,
    Flow,
    FlowSearch,
    RunResultProposal,
    SchemaRef,
    SearchDeclaration,
    WorkKind,
)

FLOW_STATE_SCHEMA = SchemaRef(name=STATE_SCHEMA.name, version=3)
"""The persisted state is the journal of the flow, not the record the state machine kept."""

DECLARATION = SearchDeclaration(
    strategy_id=STRATEGY_ID,
    state_schema=FLOW_STATE_SCHEMA,
    required_operations=(OPERATIONS.schema_ref(VERIFY_KIND), OPERATIONS.schema_ref(INTERPRET_KIND)),
    optional=frozenset({"suspend", "profile-capture"}),
    uses=frozenset(
        {
            WorkKind.ASK_AGENT,
            WorkKind.START_ATTEMPT,
            WorkKind.WITHDRAW,
            WorkKind.MEASURE,
            WorkKind.PROPOSE_WINNER,
            WorkKind.OPERATION,
            WorkKind.WAKE_AT,
        }
    ),
    handles=frozenset(
        {
            EventKind.WORKSPACE_OPENED,
            EventKind.WORKSPACE_CLOSED,
            EventKind.TURN_FINISHED,
            EventKind.MEASUREMENT_FINISHED,
            EventKind.OPERATION_FINISHED,
            EventKind.ADOPTION_FINISHED,
            EventKind.STOP_REQUESTED,
            EventKind.TIME_REACHED,
            EventKind.FAILED,
        }
    ),
)


def dynamic_search(config: DynamicConfig) -> FlowSearch[DynamicConfig]:
    """The dynamic search of one run, as a search core can run and persist."""
    return FlowSearch(DECLARATION, dynamic, config)


async def dynamic(ctx: Flow, config: DynamicConfig) -> None:
    """Measure the input, run workstreams in the free slots within budget, adopt the best."""
    measuring = candidates.InputMeasurement(
        recipe=measurement_recipe(config, ctx.facts),
        interpret=lambda refs: InterpretEvidence(evidence=refs),
        operation_seconds=config.operation_seconds,
    )
    baseline = await candidates.measure_input(ctx, config, measuring)
    run = RunRecord(config, base=ctx.facts.baseline, baseline=baseline)
    while not run.over(stopping=ctx.stop_requested):
        run.changed = False
        run.publish_parents(ctx)
        for start in run.take_starts(ctx):
            ctx.spawn(start.key, workstream, start, stoppable=True, then=run.finished)
        if run.may_plan(stopping=ctx.stop_requested):
            ctx.spawn(run.begin_planning(), plan, run, then=run.planned)
        # A stop wakes nothing here: it ends the workstreams, and their ending does.
        await ctx.until(lambda: run.changed)
    await finish(ctx, run)


async def finish(ctx: Flow, run: RunRecord) -> None:
    """Choose the winner, have it adopted, and end the run with the result."""
    winner = await select_and_adopt(ctx, run)
    ending = candidates.finish(
        winner, stopping=ctx.stop_requested, no_winner_reason=run.no_winner_reason()
    )
    await ctx.finish(ending)
    # Core refused the result. A run that nobody ends would hang, so the plain failure is
    # proposed once.
    reason = candidates.ending_reason(RunEnding.NO_TRUSTED_RESULT, FailureReason.REFUSED.value)
    plain = RunResultProposal(outcome="failure", reason=reason)
    await ctx.finish(
        Finish(decision_id=DecisionId(root="finish"), scope=THIS_RUN, mode="drain", result=plain)
    )


async def select_and_adopt(ctx: Flow, run: RunRecord) -> candidates.Winner | None:
    """The winner core adopted, or None when there is nothing to adopt."""
    rounds = run.eligible_rounds()
    if (run.gate.failed and not rounds) or (not rounds and not candidates.trusted(run.baseline)):
        return None
    winner = candidates.choose_winner(rounds, run.config, ctx.facts.baseline)
    return await candidates.adopt(ctx, winner)
