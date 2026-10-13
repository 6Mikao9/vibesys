"""One round's workspace: open it, let the implementer work in it, settle it.

Both serial searches give a round an isolated candidate workspace and ask the implementer in it,
one paid turn at a time, until the attempt is over or its paid turns are spent. What each turn
does with the reply (a self-review, a judge, the trusted gates) is the search's; this is what
they share. The workspace is a resource, so it always settles: with the assertion the attempt
made, with a failure, or as cancelled when the operator stops the run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from vs_core.api.search import (
    Again,
    AttemptBudget,
    Flow,
    NotOpened,
    RetryPolicy,
    Role,
    Settlement,
    Tries,
    Workspace,
    WorkspaceMode,
    WorkspacePlan,
    attempt_id,
    item_id,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from vibesys.hypothesis import OrchestratorPlan
    from vs_core.api.search import Assessment, RevisionRef, Session


class Attempting(Protocol):
    """What the shared work needs to know of a search's attempt."""

    number: int
    plan: OrchestratorPlan
    base: RevisionRef

    def failed(self, reason: str) -> object:
        """Record that the attempt cannot go on."""
        ...

    def asserted(self) -> tuple[RevisionRef | None, Assessment]:
        """The revision to keep and what to assert about it when the workspace closes."""
        ...


type Turn = Callable[[Session, Workspace], Awaitable[str | None]]
"""One paid turn of the implementer: None when the attempt is over, else why to ask again."""


async def work(
    ctx: Flow, attempt: Attempting, *, implementer: Role, paid_turns: int, turn: Turn
) -> Settlement | None:
    """Open the round's workspace, ask the implementer in it, settle it; core's settlement.

    The result is None when the workspace could not be opened: nothing was settled.
    """
    hypothesis = attempt.plan.hypothesis_id
    async with ctx.workspaces.open(
        attempt=attempt_id(hypothesis, attempt.number),
        item=item_id(hypothesis, attempt.number),
        plan=WorkspacePlan(mode=WorkspaceMode.ISOLATED_CHILD, base=attempt.base),
        budget=AttemptBudget(admission_charge=1, paid_invocation_limit=paid_turns),
    ) as opened:
        if isinstance(opened, NotOpened):
            attempt.failed(f"the workspace could not be opened ({opened.failed.reason.value})")
            return None
        session = ctx.agents.session(implementer, workspace=opened, revision=attempt.base)

        async def ask(tries: Tries[str]) -> bool | Again[str]:
            del tries
            again = await turn(session, opened)
            return True if again is None else Again(reason=again)

        await ctx.retry(ask, RetryPolicy(max_tries=paid_turns))
        ctx.workspaces.keep(opened, *attempt.asserted())
    return opened.settlement if isinstance(opened, Workspace) else None
