"""Shared pieces of the flow tests: a role, a prompt, replies and a run starter."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import vs_core.api as core
from vs_core.api.testing import FakeView, FlowRun, Prompt, fake_search

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine
    from contextlib import AbstractAsyncContextManager

FACTS = core.initial_state().run.facts
SHORT = core.Within(limit=10.0)


class Config(core.Value):
    """The toy flows' configuration."""

    width: int = 2


class Plan(core.Value):
    """What a toy agent answers."""

    n: int


EXCHANGES = core.Exchanges(
    correction="fake/correction.j2",
    resume="fake/resume.j2",
    wait_unrecorded="fake/wait_unrecorded.j2",
)
WORKER = core.Role(
    name="worker",
    prompt="fake/worker.j2",
    reply=Plan,
    workspace=None,
    access=core.Access.READ_ONLY,
    turn_time=600.0,
    corrections=1,
    lost_turns=2,
    charge=core.Charge.TURN,
    exchanges=EXCHANGES,
)
"""The one role the toy flows ask: it answers a ``Plan``."""

LENIENT = WORKER.model_copy(update={"name": "lenient", "corrections": 0, "lost_turns": 0})
"""A role core never retries for: its first failure is the flow's."""

ROLES = (WORKER, LENIENT)


async def ask(
    ctx: core.Flow, label: str, role: core.Role = WORKER, *, within: float = 10.0
) -> core.TurnFinished | core.Failed:
    """Ask ``role`` with a prompt whose text is ``label`` (a fresh session each call)."""
    return await ctx.agents.session(role).turn(Prompt(text=label), within=within)


def answer_of(event: object) -> int:
    """The ``n`` of a plan reply, or -1 for anything else."""
    if isinstance(event, core.TurnFinished) and isinstance(event.reply, Plan):
        return event.reply.n
    return -1


def start(
    root: Callable[[core.Flow, Config], Coroutine[Any, Any, core.NoWinner | None]],
    *,
    restarting: bool = False,
    adopting: bool = True,
    view: FakeView | None = None,
) -> FlowRun[Config]:
    """Start a flow search; with ``restarting`` it restarts from JSON after every step."""
    definition = fake_search("flows", Config, ROLES)(root)
    return FlowRun.start(definition, Config(), view=view, restarting=restarting, adopting=adopting)


def open_workspace(
    ctx: core.Flow, name: str
) -> AbstractAsyncContextManager[core.Workspace | core.NotOpened]:
    """``ctx.workspaces.open`` for an exclusive-root workspace of ``name`` on the run's input."""
    return ctx.workspaces.open(
        attempt=core.AttemptId(root=name),
        item=core.ItemId(root=name),
        plan=core.WorkspacePlan(mode=core.WorkspaceMode.EXCLUSIVE_ROOT, base=ctx.facts.baseline),
        budget=core.AttemptBudget(),
    )
