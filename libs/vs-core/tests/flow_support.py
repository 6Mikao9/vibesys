"""Shared pieces of the flow tests: a declaration, requests, replies and a run starter."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import vs_core.api as core
from vs_core.api.testing import FakeView, FlowRun

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

FACTS = core.initial_state().run.facts
DECLARATION = core.SearchDeclaration(
    strategy_id=core.StrategyId(root="flows"),
    state_schema=core.SchemaRef(name="flows", version=1),
)
SHORT = core.Within(limit=10.0)


class Config(core.Value):
    """The toy flows' configuration."""

    width: int = 2


class Prompt(core.Value):
    """What a toy agent is asked."""

    text: str


class Plan(core.Value):
    """What a toy agent answers."""

    n: int


def ask(label: str, agent: str = "worker") -> core.AskAgent:
    """An ask whose decision id root is ``label``."""
    return core.AskAgent(
        decision_id=core.DecisionId(root=label),
        scope=core.THIS_RUN,
        agent=agent,
        input=Prompt(text=label),
    )


def finish(reason: str, outcome: str = "success") -> core.Finish:
    """A drain-finish with a reason."""
    return core.Finish(
        decision_id=core.DecisionId(root="done"),
        scope=core.THIS_RUN,
        mode="drain",
        result=core.RunResultProposal(outcome=outcome, reason=reason),  # ty: ignore[invalid-argument-type]
    )


def answer_of(event: core.Event) -> int:
    """The ``n`` of a plan reply, or -1 for anything else."""
    if isinstance(event, core.TurnFinished) and isinstance(event.reply, Plan):
        return event.reply.n
    return -1


def start(
    root: Callable[[core.Flow, Config], Coroutine[Any, Any, None]],
    *,
    restarting: bool = False,
    view: FakeView | None = None,
) -> FlowRun[Config]:
    """Start a flow search; with ``restarting`` it restarts from JSON after every step."""
    config = Config()
    search = core.FlowSearch(DECLARATION, root, config)
    return FlowRun.start(search, config, FACTS, view=view, restarting=restarting)
