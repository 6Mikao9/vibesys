"""Shell contract: a request that never finishes is cancelled at its limit and reported.

The real kernel runs. There is no real time: the clock is the ``now_at`` the test passes
and the executor waits on an event nobody sets, so the only thing that can end the
request is the loop's cancel at the limit it was given.
"""

import asyncio
from typing import ClassVar, Literal

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import BaseModel

from vs_core.api import (
    Capabilities,
    ClockAdvanced,
    CoreState,
    Decision,
    DecisionId,
    Event,
    EventKind,
    ExecuteRegisteredOperation,
    Failed,
    FailureReason,
    LifecycleClass,
    Operation,
    OperationDescriptor,
    OperationRegistration,
    OperationRegistry,
    OperationRequest,
    OperationSchemaRef,
    Request,
    RequestPhase,
    RunFacts,
    RunView,
    SchemaRef,
    Scope,
    SearchDeclaration,
    StrategyState,
    Value,
    Wait,
    Within,
    WorkKind,
    initial_state,
)
from vs_project.api import FakeStateStore
from vs_runtime.api.core import (
    CoreRuntime,
    CoreRuntimeBindings,
    ExecutionContext,
    ExecutionOutcome,
    RequestExecutors,
)
from vs_runtime.api.testing import FakePublicationDelivery

LIMIT = 10.0


class Written(Value):
    done: bool


class Write(OperationRequest):
    kind: Literal["test.cancel.write"] = "test.cancel.write"
    lifecycle: Literal[LifecycleClass.IDEMPOTENT_WRITE] = LifecycleClass.IDEMPOTENT_WRITE
    outcome_model: ClassVar[type[BaseModel]] = Written


class Heard(StrategyState):
    reasons: tuple[FailureReason, ...] = ()


class OneWriteSearch:
    """Starts one write with a limit and remembers why each wait failed."""

    state_model = Heard

    def __init__(self, declaration: SearchDeclaration) -> None:
        self.declaration = declaration

    def init(self, config: object, facts: RunFacts) -> tuple[Heard, list[Decision]]:
        del config, facts
        write = Operation(
            decision_id=DecisionId(root="write"),
            scope=Scope(owner=initial_state().run.run_id, generation=0),
            request=Write(),
        )
        return Heard(schema_version=1), [Wait(request=write, until=Within(limit=LIMIT))]

    def update(self, state: Heard, event: Event, view: RunView) -> tuple[Heard, list[Decision]]:
        del view
        if isinstance(event, Failed):
            return state.model_copy(update={"reasons": (*state.reasons, event.reason)}), []
        return state, []


class NeverFinishes:
    """An operations executor whose request never completes on its own."""

    def __init__(self) -> None:
        self.running = asyncio.Event()
        self.contexts: list[ExecutionContext] = []
        self._never = asyncio.Event()

    async def execute(self, request: Request, context: ExecutionContext) -> ExecutionOutcome:
        del request
        self.contexts.append(context)
        self.running.set()
        await self._never.wait()
        raise AssertionError  # pragma: no cover


def _world() -> tuple[CoreState, OperationRegistry, OneWriteSearch]:
    descriptor = OperationDescriptor(
        kind="test.cancel.write",
        lifecycle=LifecycleClass.IDEMPOTENT_WRITE,
        inspect=True,
        request_schema=SchemaRef(name="write", version=1),
        outcome_schema=SchemaRef(name="written", version=1),
    )
    codec = OperationRegistry(
        (OperationRegistration(descriptor=descriptor, request_model=Write, outcome_model=Written),)
    )
    state = initial_state()
    declaration = state.run.declaration.model_copy(
        update={
            "required_operations": (
                OperationSchemaRef(
                    kind=descriptor.kind,
                    request_schema=descriptor.request_schema,
                    outcome_schema=descriptor.outcome_schema,
                    lifecycle=descriptor.lifecycle,
                ),
            ),
            "uses": frozenset({WorkKind.OPERATION}),
            "handles": state.run.declaration.handles | {EventKind.OPERATION_FINISHED},
        }
    )
    state = state.model_copy(
        update={
            "registry": codec.descriptors,
            "run": state.run.model_copy(
                update={
                    "declaration": declaration,
                    "deadline_at": 1000.0,
                    "capabilities": Capabilities(operations=codec.descriptors),
                }
            ),
        }
    )
    return state, codec, OneWriteSearch(declaration)


@pytest.mark.asyncio
@given(after=st.floats(min_value=0.0, max_value=50.0, allow_nan=False))
async def test_a_request_that_never_finishes_is_cancelled_at_its_limit_and_failed(
    after: float,
) -> None:
    state, codec, search = _world()
    executor = NeverFinishes()
    store = FakeStateStore()
    shell = CoreRuntime(
        store,
        search,
        state,
        config=None,
        bindings=CoreRuntimeBindings(
            registry=codec, executors=RequestExecutors(operations=executor)
        ),
    )
    shell.start("host", now_at=0, lease_duration=1e9)
    delivery = FakePublicationDelivery(store)

    # The request starts and is still running before its limit.
    await shell.settle(delivery, now_at=1)
    await asyncio.wait_for(executor.running.wait(), timeout=30)  # only guards against a hang
    (context,) = executor.contexts
    assert shell.record.envelope.strategy.reasons == ()

    # At the limit core ends the record and the loop cancels the running executor.
    moment = context.until.at + after
    shell.submit(ClockAdvanced(now_at=moment), now_at=moment)
    await asyncio.wait_for(shell.run_until_idle(delivery, now_at=moment), timeout=30)
    (ended,) = (
        r
        for r in shell.record.envelope.core.records.records
        if isinstance(r.request, ExecuteRegisteredOperation)
    )
    assert ended.phase is RequestPhase.BLOCKED
    assert ended.failure is not None
    assert ended.failure.reason is FailureReason.TIMED_OUT
    assert shell.record.envelope.strategy.reasons == (FailureReason.TIMED_OUT,)
