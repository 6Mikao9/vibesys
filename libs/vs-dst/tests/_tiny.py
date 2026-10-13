"""A tiny search and its world, for exercising vs-dst without a concrete product search.

The search works through its items one at a time: open a workspace (core asks the executors
for one), let it settle on leaving the block, and return after the last item, so the framework
adopts the input and ends the run. The
world answers a workspace request with a resource named by how many workspaces it has handed
out, so the position of the world shows in the state of the run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from vs_core.api import (
    AttemptBudget,
    AttemptId,
    EnsureWorkspace,
    Flow,
    FlowState,
    ItemId,
    Limits,
    OperationRegistry,
    ResourceId,
    RevisionRef,
    RunEnvelope,
    RunFacts,
    Value,
    Wait,
    WakeAt,
    Within,
    WorkspaceMode,
    WorkspacePlan,
)
from vs_core.api.testing import Harness, Succeeded
from vs_core.flows import search
from vs_dst.api import RunSetup
from vs_runtime.api.testing import Launch

if TYPE_CHECKING:
    from vs_core.api import CoreState, Decision, Request
    from vs_core.api.testing import Answer

BASELINE = RevisionRef.of_git_commit("abc123")
FACTS = RunFacts(
    objective="settle every item",
    baseline=BASELINE,
    evaluator_digest="evaluator",
    workload_digest="workload",
    environment_digest="environment",
)

PATIENCE = Within(limit=1000)


class TinyConfig(Value):
    """How many items the tiny search settles."""

    items: int


@search("tiny", config=TinyConfig)
async def tiny(ctx: Flow, config: TinyConfig) -> None:
    """Open and settle a workspace for each item in turn; the run then ends on the input."""
    for index in range(config.items):
        plan = WorkspacePlan(mode=WorkspaceMode.EXCLUSIVE_ROOT, base=ctx.facts.baseline)
        async with ctx.workspaces.open(
            attempt=AttemptId(root=f"attempt:{index}"),
            item=ItemId(root=f"item:{index}"),
            plan=plan,
            budget=AttemptBudget(),
            within=PATIENCE,
        ):
            pass


def tiny_setup(items: int, max_concurrent: int = 1) -> RunSetup:
    """The tiny search over ``items`` items, hosted with no operations."""
    return RunSetup(
        new_launch=lambda: Launch(tiny, TinyConfig(items=items)),
        harness=Harness(
            registry=OperationRegistry(()),
            facts=FACTS,
            limits=Limits(max_attempts=8, max_turns=8, max_parallel=1, max_retries=2),
            envelope_type=RunEnvelope[FlowState],
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
