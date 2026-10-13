"""A ``FlowState`` survives the envelope codec: typed replies and operation outcomes included."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Literal

import vs_core.api as core
from vs_core.api.testing import FakeView, FlowRun

if TYPE_CHECKING:
    from pydantic import BaseModel

from typing import get_args

from vs_core.flows import (
    JournaledEvent,
)  # test-isolation: the internal journal union must not drift from `Event`

from .flow_support import DECLARATION, FACTS, SHORT, Config, Plan, answer_of, ask, finish


class Outcome(core.Value):
    status: Literal["succeeded"] = "succeeded"
    result: tuple[str, ...]


class Query(core.OperationRequest):
    kind: Literal["test.typed"] = "test.typed"
    lifecycle: Literal[core.LifecycleClass.QUERY] = core.LifecycleClass.QUERY
    outcome_model: ClassVar[type[BaseModel]] = Outcome


DESCRIPTOR = core.OperationDescriptor(
    kind="test.typed",
    lifecycle=core.LifecycleClass.QUERY,
    request_schema=core.SchemaRef(name="query", version=1),
    outcome_schema=core.SchemaRef(name="outcome", version=1),
)
REGISTRY = core.OperationRegistry(
    (core.OperationRegistration(descriptor=DESCRIPTOR, request_model=Query, outcome_model=Outcome),)
)


class Note(core.Value):
    """The record the toy flow publishes."""

    plan: int


async def ask_then_query(ctx: core.Flow, _config: Config) -> None:
    plan = answer_of(await ctx.wait(ask("planner"), SHORT, reply=Plan))
    ctx.publish(Note(plan=plan))
    request = core.Operation(
        decision_id=core.DecisionId(root="query"), scope=core.THIS_RUN, request=Query()
    )
    event = await ctx.wait(request, SHORT)
    assert isinstance(event, core.OperationFinished)
    assert isinstance(event.result.outcome, Outcome)
    await ctx.finish(finish(f"{plan} {event.result.outcome.result}"))


def _operation_finished(decision: core.DecisionId) -> core.OperationFinished:
    state = core.initial_state()
    observation = core.Observation(
        event_id=core.EventId(root="event"),
        request_id=core.RequestId(root="request"),
        scope=core.Scope(owner=state.run.run_id, generation=0),
        sequence=1,
        observed_at=1.0,
        status=core.ObservationStatus.SUCCEEDED,
        terminal=True,
        released=True,
    )
    result = core.OperationResult(
        operation_id=core.OperationId(root="operation"),
        observation=observation,
        outcome_schema=DESCRIPTOR.outcome_schema,
        operation_schema=REGISTRY.encode(Query()).schema_ref,
        outcome=Outcome(result=("typed",)),
    )
    return core.OperationFinished(decision=decision, result=REGISTRY.validate_event(result))


def _through_the_envelope(state: core.FlowState) -> core.FlowState:
    core_state = core.initial_state()
    envelope = core.RunEnvelope[core.FlowState](
        schema_version=core.ENVELOPE_SCHEMA_VERSION,
        fence=core.HostFence(host_id=core.HostId(root="host"), epoch=0),
        strategy_id=core_state.run.declaration.strategy_id,
        state_schema=core_state.run.declaration.state_schema,
        core=core_state,
        strategy=state,
        event_cursor=core.EventCursor(sequence=0),
    )
    text = REGISTRY.encode_envelope(envelope)
    return REGISTRY.decode_envelope(core.RunEnvelope[core.FlowState], text).strategy


def test_a_flow_state_with_a_typed_reply_and_an_operation_outcome_round_trips_the_envelope() -> (
    None
):
    config = Config()
    search = core.FlowSearch(DECLARATION, ask_then_query, config, REGISTRY)
    run = FlowRun.start(search, config, FACTS, view=FakeView())
    run.reply(run.expect(core.AskAgent), Plan(n=4))
    queried = run.expect(core.Operation)
    run.answer(queried, _operation_finished(queried.decision))
    assert run.finished is not None
    assert run.finished.result.reason == "4 ('typed',)"

    restored = _through_the_envelope(run.state)
    assert restored == run.state
    assert core.FlowSearch.published(restored, Note) == Note(plan=4)
    assert any(
        step.recorded is not None and step.recorded.kind == "recorded_turn"
        for step in restored.journal
    )
    assert any(
        step.recorded is not None and step.recorded.kind == "recorded_operation"
        for step in restored.journal
    )


def test_a_search_resumes_from_the_state_read_back_from_the_envelope() -> None:
    config = Config()
    search = core.FlowSearch(DECLARATION, ask_then_query, config, REGISTRY)
    run = FlowRun.start(search, config, FACTS, view=FakeView())
    run.reply(run.expect(core.AskAgent), Plan(n=4))
    queried = run.expect(core.Operation)

    state = _through_the_envelope(run.state)
    resumed = core.FlowSearch(DECLARATION, ask_then_query, config, REGISTRY)
    _, decisions = resumed.update(
        state, _operation_finished(queried.decision), FakeView().current()
    )
    assert [type(d) for d in decisions] == [core.Finish]


def test_the_journal_holds_every_event_as_it_is_but_turns_and_operation_results() -> None:
    def members(alias: object) -> set[type]:
        annotated = alias.__value__  # ty: ignore[unresolved-attribute]
        return set(get_args(get_args(annotated)[0]))

    assert members(JournaledEvent) == members(core.Event) - {
        core.TurnFinished,
        core.OperationFinished,
    }
