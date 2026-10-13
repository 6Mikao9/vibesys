"""The dynamic search as a story: measure the input, plan and run workstreams, then adopt a winner.

This is the whole search, top to bottom. Planning and the workstreams are tasks in two groups, so
they run concurrently; the root decides only when to start one and when the search is over.
`RunRecord` holds what the search knows, and the tasks' results are folded into it. An operator
stop reaches the workstreams (they settle what they started) but not the planner; the root sees
it as `StopRequested`, plans no more, and finishes once nothing is in flight.
"""

from functools import partial

from vibesys.candidates import api as candidates
from vibesys.orchestration.dynamic.flow._ending import finish
from vibesys.orchestration.dynamic.flow._outcomes import WorkstreamDone
from vibesys.orchestration.dynamic.flow._parents import offer_as_parent, parent_of
from vibesys.orchestration.dynamic.flow._planner import plan
from vibesys.orchestration.dynamic.flow._run_state import NoPlan, RunRecord, Scheduled
from vibesys.orchestration.dynamic.flow._workstream import workstream
from vibesys.orchestration.dynamic.search.api import (
    INTERPRET_KIND,
    OPERATIONS,
    STRATEGY_ID,
    VERIFY_KIND,
    DynamicConfig,
    InterpretEvidence,
    measurement_recipe,
)
from vs_core.api.search import (
    EventKind,
    Flow,
    FlowSearch,
    SchemaRef,
    SearchDeclaration,
    StopRequested,
    TaskStopped,
    WorkKind,
)

FLOW_STATE_SCHEMA = SchemaRef(name="dynamic.flow-state", version=1)
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
    return FlowSearch(DECLARATION, dynamic, config, OPERATIONS.registry())


async def dynamic(ctx: Flow, config: DynamicConfig) -> None:
    """Measure the input, run workstreams in the free slots within budget, adopt the best."""
    measuring = candidates.InputMeasurement(
        recipe=measurement_recipe(config, ctx.facts),
        interpret=lambda refs: InterpretEvidence(evidence=refs),
        operation_seconds=config.operation_seconds,
    )
    baseline = await candidates.measure_input(ctx, config, measuring)
    run = RunRecord(config, base=ctx.facts.baseline, baseline=baseline)
    async with (
        ctx.group(limit=config.max_in_flight) as workstreams,
        ctx.group(limit=1, stops=False) as planning,
    ):
        while not run.over(planning=not planning.free, stopping=ctx.stop_requested):
            for start in run.ready_to_start(workstreams.free, partial(parent_of, ctx, run)):
                workstreams.start(start.key, workstream, start)
            if planning.free and run.may_plan(stopping=ctx.stop_requested):
                planning.start(run.next_call(), plan, run)
            match await ctx.first(planning, workstreams, ctx.stopped):
                case Scheduled() | NoPlan() as outcome:
                    run.planned(outcome)
                case WorkstreamDone() as done:
                    run.finished(done)
                    offer_as_parent(ctx, run, done)
                case TaskStopped(key=key):
                    run.stopped_before_start(key)
                case StopRequested():
                    pass  # the workstreams' group has stopped them
            ctx.publish(run.snapshot())
    await finish(ctx, run)
