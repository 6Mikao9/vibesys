"""A tiny search and its world, for exercising vs-dst without a concrete product search.

The search works through its items one at a time: start an attempt (core asks the executors
for a workspace), settle it once its workspace is ready, and finish after the last item. The
world answers a workspace request with a resource named by how many workspaces it has handed
out, so the position of the world shows in the state of the run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from vs_core.api import (
    AttemptBudget,
    AttemptId,
    AttemptRef,
    DecisionId,
    EnsureWorkspace,
    EventKind,
    Failed,
    Finish,
    ItemId,
    Limits,
    OperationRegistry,
    ResourceId,
    RevisionRef,
    RunEnvelope,
    RunFacts,
    RunResultProposal,
    Scope,
    Settle,
    StartAttempt,
    StopRequested,
    StrategyState,
    Wait,
    WakeAt,
    Withdraw,
    Within,
    WorkKind,
    WorkspaceClosed,
    WorkspaceMode,
    WorkspaceOpened,
    WorkspacePlan,
    initial_state,
)
from vs_core.api.testing import Harness, Succeeded
from vs_dst.api import RunSetup
from vs_runtime.api.testing import Launch

if TYPE_CHECKING:
    from vs_core.api import CoreState, Decision, Event, Request, RunView
    from vs_core.api.testing import Answer

BASELINE = RevisionRef.of_git_commit("abc123")
FACTS = RunFacts(
    objective="settle every item",
    baseline=BASELINE,
    evaluator_digest="evaluator",
    workload_digest="workload",
    environment_digest="environment",
)


class TinyState(StrategyState):
    done: int = 0


SCOPE = Scope(owner=initial_state().run.run_id, generation=0)
"""The run's own scope: the harness and the scripted host both start from `initial_state`."""
PATIENCE = Within(limit=1000)


class TinySearch:
    """Start and settle each item in turn, then finish. The items to settle are its constructor argument."""

    state_model = TinyState
    declaration = initial_state().run.declaration.model_copy(
        update={
            "uses": frozenset({WorkKind.START_ATTEMPT, WorkKind.WITHDRAW}),
            "handles": frozenset(
                {
                    EventKind.FAILED,
                    EventKind.STOP_REQUESTED,
                    EventKind.WORKSPACE_OPENED,
                    EventKind.WORKSPACE_CLOSED,
                }
            ),
        }
    )

    def __init__(self, items: tuple[ItemId, ...]) -> None:
        self._items = items

    def init(self, config: None, facts: RunFacts) -> tuple[TinyState, list[Decision]]:
        del config
        return TinyState(schema_version=1), self._next(0, facts.baseline)

    def update(
        self, state: TinyState, event: Event, view: RunView
    ) -> tuple[TinyState, list[Decision]]:
        match event:
            case WorkspaceOpened(attempt=attempt):
                index = state.done
                return state, [self._settle(attempt, index)]
            case WorkspaceClosed():
                done = state.done + 1
                return state.model_copy(update={"done": done}), self._next(
                    done, view.facts.baseline
                )
            case StopRequested() | Failed():
                # The run's own deadline stop, or a request the world never answered: the
                # search has no other plan, and what the run reports then is under test.
                return state, []
            case _:
                message = f"unexpected event {event.kind}"
                raise ValueError(message)

    def _next(self, index: int, baseline: RevisionRef) -> list[Decision]:
        items = self._items
        if index == len(items):
            return [
                Finish(
                    decision_id=DecisionId(root="finish"),
                    scope=SCOPE,
                    mode="drain",
                    result=RunResultProposal(outcome="success", reason="every item settled"),
                )
            ]
        start = StartAttempt(
            decision_id=DecisionId(root=f"start:{index}"),
            scope=SCOPE,
            attempt_id=AttemptId(root=f"attempt:{index}"),
            item_id=items[index],
            workspace=WorkspacePlan(mode=WorkspaceMode.EXCLUSIVE_ROOT, base=baseline),
            budget=AttemptBudget(),
        )
        return [Wait(request=start, until=PATIENCE)]

    @staticmethod
    def _settle(attempt: AttemptRef, index: int) -> Decision:
        withdraw = Withdraw(
            decision_id=DecisionId(root=f"settle:{index}"),
            scope=SCOPE,
            target=attempt,
            disposition=Settle(
                assessments=(), eligible=False, retention="discard", outcome="succeeded"
            ),
        )
        return Wait(request=withdraw, until=PATIENCE)


def tiny_setup(items: int, max_concurrent: int = 1) -> RunSetup:
    """The tiny search over ``items`` items, hosted with no operations."""
    names = tuple(ItemId(root=f"item:{n}") for n in range(items))
    return RunSetup(
        new_launch=lambda: Launch(TinySearch(names), None),
        harness=Harness(
            registry=OperationRegistry(()),
            facts=FACTS,
            limits=Limits(max_attempts=8, max_turns=8, max_parallel=1, max_retries=2),
            envelope_type=RunEnvelope[TinyState],
        ),
        schemas={},
        max_concurrent=max_concurrent,
    )


class TinyWorld:
    """Executors that hand out workspaces, one numbered resource after another."""

    def __init__(self, items: int = 2, max_concurrent: int = 1) -> None:
        self.setup = tiny_setup(items, max_concurrent)
        self.workspaces_handed_out = 0
        self.seen: list[str] = []

    def __call__(self, request: Request, core: CoreState) -> Answer:
        del core
        self.seen.append(request.kind)
        if isinstance(request, EnsureWorkspace):
            number = self.workspaces_handed_out
            self.workspaces_handed_out += 1
            return Succeeded(
                resource_id=ResourceId(root=f"workspace:{number}"), revision=request.plan.base
            )
        return Succeeded()


def decision_key(decision: Decision) -> object:
    """What identifies a decision across runs: the id of its work, or the time of a wake-up."""
    match decision:
        case Wait(request=request):
            return request.decision_id
        case WakeAt(time=time):
            return ("wake", time)
        case _:
            return decision.decision_id
