"""Flows: a search written as straight-line async code whose effects are journaled and replayed.

A flow is an ``async def`` that reads top to bottom::

    async def root(ctx: Flow, config: Config) -> None:
        plan = await ctx.wait(AskAgent(decision_id=..., agent="planner", ...), Within(limit=600), reply=Plan)
        match plan:
            case TurnFinished(reply=Plan() as plan): ...
            case Failed(): ...
        child = ctx.spawn("a", workstream, plan, stoppable=True)
        await ctx.join(child)
        await ctx.finish(Finish(...))

``FlowSearch`` runs it as an ordinary ``Search``. There is no asyncio: awaiting ``ctx.wait``,
``ctx.wake_at``, ``ctx.until`` or ``ctx.join`` suspends the coroutine, and ``update`` resumes
exactly the flows the incoming event unblocks, runs them (and the children they spawn) until
every one is suspended again, and returns the decisions they issued, in issue order.

Persistence is replay. The state is ``FlowState``: the run's facts and a journal with one
``Step`` per event, holding the event, every ``ctx.read`` made while handling it, and a digest
of the decisions it produced. To resume from a saved state (a restart, another process) the
search runs the flow code again from ``init`` and feeds it the journaled events and reads; the
code is deterministic, so it ends up suspended where it was. A digest that differs means the
code no longer does what the journal recorded, and ``FlowCodeChanged`` is raised.

Rules a flow must keep so that replay works: take every outside fact through ``ctx.read``
(the clock, attempts, settlements), never from globals; do not branch on anything that
differs between runs. Locals, loops and ``try`` blocks are free.

Changing the code of a run in flight (``FlowDef``, ``ctx.safe_point``): a flow registered with a
snapshot type takes its snapshot as its argument and records newer ones at safe points. If the
search is built with ``flows=(...)``, a code change restarts every running flow from its latest
snapshot with the new code instead of raising, and the journal starts over from there.

The files: ``_runtime`` drives the coroutines and holds ``Flow``; ``_state`` is the persisted
state; ``_search`` is the ``Search`` that replays and restarts.
"""

from ._errors import FlowCodeChanged, Stopped
from ._runtime import Flow, FlowChild, FlowDef
from ._search import FlowSearch
from ._state import FlowState, JournaledEvent

__all__ = [
    "Flow",
    "FlowChild",
    "FlowCodeChanged",
    "FlowDef",
    "FlowSearch",
    "FlowState",
    "JournaledEvent",
    "Stopped",
]
