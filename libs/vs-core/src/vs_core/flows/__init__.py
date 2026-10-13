"""Flows: a search written as straight-line async code whose effects are journaled and replayed.

The vocabulary and the rules for flow code are in ``docs/contributing/flows.md``. In short, a
flow is an ``async def`` whose first argument is ``ctx: Flow``. It starts requests
(``ctx.wait``, ``ctx.start``), waits on sources (``ctx.first``), runs concurrent tasks in groups,
and ends the run with ``ctx.finish``. It never touches the clock, I/O or randomness.

``FlowSearch`` runs a root flow as an ordinary ``Search``. There is no asyncio: awaiting
suspends the coroutine, and ``update`` resumes exactly the flows whose sources the incoming
event filled, until every flow is suspended again. The decisions issued along the way are the
``update``'s output, in issue order.

Persistence is replay. ``FlowState`` holds the run's facts and a journal with one ``Step`` per
event: the event, every ``ctx.read`` made while handling it, and a digest of the decisions it
produced. To resume a saved state the search runs the flow code again from ``init`` and feeds it
the journal; the code is deterministic, so it ends up suspended where it was. A digest that
differs means the code changed, and ``FlowCodeChanged`` is raised. A search built with
``flows=(...)`` instead restarts every running flow from its latest ``safe_point`` snapshot.

The files: ``_defs`` registers flows; ``_flow`` is ``Flow`` and ``Group``; ``_run`` drives the coroutines; ``_sources`` is
what a flow waits on; ``_resource`` is ``@resource``; ``_retry`` is the retry values; ``_state``
is the persisted state; ``_search`` is the ``Search`` that replays and restarts.
"""

from ._defs import flow
from ._errors import Deadlock, FlowCodeChanged, Stopped, TaskStopped, TimeUp
from ._flow import Flow, Group, Reads, Waits
from ._resource import resource
from ._retry import Again, GaveUp, RetryPolicy, Tries
from ._search import FlowSearch
from ._sources import Pending, Source, StopSource, Task, Timer
from ._state import FlowState, JournaledEvent, Waiting

__all__ = [
    "Again",
    "Deadlock",
    "Flow",
    "FlowCodeChanged",
    "FlowSearch",
    "FlowState",
    "GaveUp",
    "Group",
    "JournaledEvent",
    "Pending",
    "Reads",
    "RetryPolicy",
    "Source",
    "StopSource",
    "Stopped",
    "Task",
    "TaskStopped",
    "TimeUp",
    "Timer",
    "Tries",
    "Waiting",
    "Waits",
    "flow",
    "resource",
]
