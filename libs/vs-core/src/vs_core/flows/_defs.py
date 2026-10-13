"""Flow registration (``@flow``) and the bookkeeping of one running flow."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cache
from typing import TYPE_CHECKING, Any

from pydantic import TypeAdapter

from ._sources import First, Pending, Signal, StopSource, Task

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from ._flow import Group

UNBOUNDED = 1_000_000
"""``Group.free`` of a group without a limit."""

FLOW_ATTRIBUTE = "__flow_def__"


@dataclass(frozen=True)
class FlowDef:
    """A flow function registered by name, with the type of the snapshot it restarts from."""

    name: str
    fn: Callable[..., Coroutine[Any, Any, Any]]
    snapshot: type[Any]


def flow[F: Callable[..., Coroutine[Any, Any, Any]]](
    *, snapshot: type[Any], name: str | None = None
) -> Callable[[F], F]:
    """Register a flow function that takes a ``snapshot`` as its argument and can restart from one.

    ``group.start(key, fn, snapshot)`` starts it from that snapshot; ``ctx.checkpoint(s)``
    records a newer one.
    """

    def register(fn: F) -> F:
        setattr(fn, FLOW_ATTRIBUTE, FlowDef(name or getattr(fn, "__name__", "flow"), fn, snapshot))
        return fn

    return register


def definition_of(fn: Callable[..., Coroutine[Any, Any, Any]]) -> FlowDef | None:
    """The registration of ``fn``, if it has one."""
    return getattr(fn, FLOW_ATTRIBUTE, None)


@cache
def _adapter[T](model: type[T]) -> TypeAdapter[T]:
    return TypeAdapter(model)


@dataclass(eq=False)
class _Task:
    """One running flow: the root or a task of a group."""

    path: str
    group: Group[Any] | None = None
    coro: Coroutine[Any, Any, Any] | None = None
    blocked: First | None = None
    flow: FlowDef | None = None
    snapshot: object | None = None
    """The latest snapshot of a registered flow: what a restart resumes it from."""
    checkpointed: bool = False
    shield: int = 0
    stop_pending: bool = False
    stopped: bool = False
    stop: StopSource = field(default_factory=StopSource)
    handle: Task[Any] = field(default_factory=lambda: Task("root"))
    started: list[Pending] = field(default_factory=list)
    signals: list[Signal] = field(default_factory=list)
