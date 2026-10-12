"""The runtime: flows as coroutines driven by hand, and the ``Flow`` handle they receive."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from functools import cache
from typing import TYPE_CHECKING, Any, cast

from pydantic import BaseModel, TypeAdapter

from vs_core._registry import ContractError
from vs_core.children import Dropped
from vs_core.types.common import DecisionId, RunFacts, Seconds
from vs_core.types.kinds import EventKind
from vs_core.types.records import OperationResult
from vs_core.types.search import (
    AdoptionFinished,
    Event,
    OperationFinished,
    StopRequested,
    TimeReached,
    TurnFinished,
)
from vs_core.types.strategy import Decision, Finish, ProposeWinner, Wait, WakeAt, Work
from vs_core.types.until import Failed, Within

from ._errors import FlowCodeChanged, Stopped
from ._state import (
    Issued,
    LiveFlow,
    LiveFlows,
    Recorded,
    RecordedOperation,
    RecordedTurn,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Generator

    from vs_core._registry import OperationRegistry
    from vs_core.types.search import RunView


@dataclass(frozen=True)
class FlowDef:
    """A flow function registered by name, with the type of the snapshot it restarts from.

    A registered flow takes its snapshot as its argument (``async def worker(ctx, s: Snap)``),
    so ``ctx.spawn("a", worker, Snap(...))`` starts it from that snapshot and
    ``ctx.safe_point(snap)`` records a later one.
    """

    name: str
    fn: Callable[..., Coroutine[Any, Any, Any]]
    snapshot: type[Any]


class _Block:
    """What an ``await`` in a flow yields to the runtime; the runtime sends the answer back."""

    def __await__(self) -> Generator[_Block, Any, Any]:
        return (yield self)


@dataclass
class _OnDecision(_Block):
    decision: DecisionId
    work: Work | Finish
    reply: type[BaseModel]


@dataclass
class _OnTime(_Block):
    time: Seconds


@dataclass
class _OnCondition(_Block):
    predicate: Callable[[], bool]


@dataclass
class _OnChild(_Block):
    child: FlowChild[Any]


@dataclass(eq=False)
class _Task:
    """One running flow: the root or a child."""

    path: str
    stoppable: bool = False
    then: Callable[[Any], None] | None = None
    coro: Coroutine[Any, Any, Any] | None = None
    blocked: _Block | None = None
    done: bool = False
    result: Any = None
    stopped: bool = False
    joiners: list[_Task] = field(default_factory=list)
    flow: FlowDef | None = None
    snapshot: object | None = None
    """The latest snapshot of a registered flow: what a restart resumes it from."""


@dataclass(frozen=True)
class _Spawn:
    """What ``ctx.spawn`` asked for."""

    key: str
    fn: Callable[..., Coroutine[Any, Any, Any]]
    args: tuple[object, ...]
    stoppable: bool
    then: Callable[[Any], None] | None


class FlowChild[T]:
    """A child flow started by ``ctx.spawn``; ``await ctx.join(child)`` yields its result."""

    def __init__(self, task: _Task) -> None:
        """Wrap the child's task."""
        self.task = task

    @property
    def done(self) -> bool:
        """Whether the child has returned."""
        return self.task.done

    @property
    def result(self) -> T:
        """The child's return value (it must be done)."""
        if not self.task.done:
            raise ContractError(("child",), "the child has not returned")
        return cast("T", self.task.result)


# -- the author's handle -----------------------------------------------------------------------


class Flow:
    """The ``ctx`` every flow function receives: how a flow waits, reads, spawns and finishes."""

    def __init__(self, run: _Run, task: _Task) -> None:
        """Bind the handle to its run and its own task."""
        self._run = run
        self._task = task
        self.facts: RunFacts = run.facts
        """The run's facts, constant for the run."""

    @property
    def stop_requested(self) -> bool:
        """Whether an operator stop has arrived."""
        return self._run.stop_requested

    async def wait(
        self, request: Work, until: Within, *, reply: type[BaseModel] = BaseModel
    ) -> Event:
        """Start ``request`` and return the event that answers it: its result, or ``Failed``.

        The request's ``decision_id`` is the id root; the runtime stamps it
        ``<flow path><root>.<n>``. ``reply`` is the model of an agent's reply.
        """
        stamped = self._run.stamp(self._task, request)
        self._run.out.append(Wait(request=stamped, until=until))
        return cast("Event", await _OnDecision(stamped.decision_id, stamped, reply))

    async def wake_at(self, time: Seconds) -> None:
        """Sleep until the first ``TimeReached`` at or after ``time``."""
        self._run.out.append(WakeAt(time=time))
        await _OnTime(time)

    async def finish(self, finish: Finish) -> Failed:
        """End the run. Returns only if core refuses the ``Finish``; otherwise never resumes."""
        stamped = self._run.stamp(self._task, finish)
        self._run.out.append(stamped)
        refused = await _OnDecision(stamped.decision_id, stamped, BaseModel)
        if not isinstance(refused, Failed):
            raise ContractError(("finish",), "a finish is answered only by a failure")
        return refused

    def read[T](self, fn: Callable[[RunView], T], as_: type[T] | TypeAdapter[T]) -> T:
        """Read the view. The value is journaled, so live and replay see the same value."""
        return self._run.read(fn, as_)

    def spawn[T, *Ts](
        self,
        key: str,
        fn: Callable[[Flow, *Ts], Coroutine[Any, Any, T]],
        *args: *Ts,
        stoppable: bool = False,
        then: Callable[[T], None] | None = None,
    ) -> FlowChild[T]:
        """Start ``fn(child_ctx, *args)`` now; it runs until its first await.

        ``then(result)`` runs in the caller when the child returns. A ``stoppable`` child gets
        ``Stopped`` thrown at its current await when the operator stops the run.
        """
        return self._run.spawn(self._task, _Spawn(key, fn, args, stoppable, then))

    async def safe_point(self, snapshot: BaseModel) -> None:
        """Record ``snapshot`` as the state this flow can be restarted from after a code change.

        Only registered flows (``FlowDef``) have snapshots. The flow takes the snapshot as its
        argument, so after a restart it resumes here with the new code and the work since is
        redone.
        """
        flow = self._task.flow
        if flow is None:
            raise ContractError(("safe_point",), "only a registered flow has safe points")
        if not isinstance(snapshot, flow.snapshot):
            raise ContractError(
                ("safe_point",), f"{flow.name} snapshots are {flow.snapshot.__name__}"
            )
        self._task.snapshot = snapshot

    async def join[T](self, child: FlowChild[T]) -> T:
        """Wait for ``child`` to return and give its result."""
        if child.done:
            return child.result
        return cast("T", await _OnChild(child))

    async def until(self, predicate: Callable[[], bool]) -> None:
        """Wait until ``predicate()`` holds; it is re-checked after every event."""
        if not predicate():
            await _OnCondition(predicate)


# -- the runtime -------------------------------------------------------------------------------


@cache
def _adapter[T](model: type[T]) -> TypeAdapter[T]:
    return TypeAdapter(model)


class _Run:
    """The live flows of one run, driven by hand: resume what an event unblocks, run to quiet."""

    def __init__(
        self,
        defs: tuple[FlowDef, ...],
        config: object,
        facts: RunFacts,
        registry: OperationRegistry | None,
    ) -> None:
        self.facts = facts
        self._registry = registry
        self.stop_requested = False
        self.out: list[Decision] = []
        self.dropped: list[Dropped] = []
        self._defs = defs  # the root first
        self._config = config
        self._root = _Task(path="")
        self._ready: deque[tuple[_Task, object, BaseException | None]] = deque()
        self._waiting: dict[DecisionId, tuple[_Task, _OnDecision]] = {}
        self._sleeping: list[_Task] = []
        self._conditions: list[_Task] = []
        self._children: list[_Task] = []  # live spawned flows, in spawn order
        self._pending: dict[str, LiveFlow] = {}  # restored children, started when respawned
        self._issued: dict[tuple[str, str], int] = {}
        self._ids: set[DecisionId] = set()
        self._view: RunView | None = None
        self._recorded: deque[str] | None = None
        self._reads: list[str] = []

    # -- one step: init or one event ---------------------------------------------------------

    def start(self) -> list[Decision]:
        """Run the root flow until it first suspends."""
        self._begin(None, None)
        root = self._defs[0]
        self._launch(self._root, root, self._config)
        return self._end()

    def restore(
        self, base: LiveFlows, view: RunView | None, recorded: tuple[str, ...] | None
    ) -> list[Decision]:
        """Restart the flows of ``base`` from their snapshots with the current code.

        The root starts first. Its children start as it spawns them again, each from its own
        latest snapshot (not the arguments of the new spawn), keeping its decision numbering.
        """
        self._begin(view, recorded)
        self.stop_requested = base.stopping
        self._issued = {(i.path, i.label): i.count for i in base.issued}
        self._pending = {live.path: live for live in base.flows if live.path}
        root = next(live for live in base.flows if not live.path)
        definition = self._defined(root.name)
        self._launch(self._root, definition, self._snapshot_of(definition, root))
        if base.stopping:
            self._stop()
        self._settle()
        return self._end()

    def checkpoint(self) -> LiveFlows:
        """Where every running flow can be restarted from."""
        flows = []
        for task in (self._root, *self._children):
            definition = task.flow
            registered = definition is not None
            text = (
                _adapter(definition.snapshot).dump_json(task.snapshot).decode()
                if definition
                else ""
            )
            flows.append(
                LiveFlow(
                    path=task.path,
                    name=definition.name if registered else None,
                    stoppable=task.stoppable,
                    snapshot_json=text,
                )
            )
        issued = tuple(Issued(path=p, label=lb, count=n) for (p, lb), n in self._issued.items())
        return LiveFlows(flows=tuple(flows), issued=issued, stopping=self.stop_requested)

    def _defined(self, name: str | None) -> FlowDef:
        found = next((d for d in self._defs if d.name == name), None)
        if found is None:
            raise FlowCodeChanged(0)  # the new code has no flow of that name to restart
        return found

    @staticmethod
    def _snapshot_of(definition: FlowDef, live: LiveFlow) -> object:
        return _adapter(definition.snapshot).validate_json(live.snapshot_json)

    def _launch(self, task: _Task, definition: FlowDef, argument: object) -> None:
        """Start ``task`` as a registered flow on ``argument`` (its first snapshot)."""
        task.flow, task.snapshot = definition, argument
        task.coro = definition.fn(Flow(self, task), argument)
        self._resume(task, None, None)

    def feed(
        self, item: Event | Recorded, view: RunView | None, recorded: tuple[str, ...] | None
    ) -> list[Decision]:
        """Deliver one event; the flows it unblocks run until all are suspended again."""
        self._begin(view, recorded)
        self._route(item)
        self._settle()
        return self._end()

    def _begin(self, view: RunView | None, recorded: tuple[str, ...] | None) -> None:
        self.out = []
        self._reads = []
        self._view = view
        self._recorded = None if recorded is None else deque(recorded)

    def _end(self) -> list[Decision]:
        if self._recorded:
            raise FlowCodeChanged(-1)  # replay made fewer reads than the journal holds
        return self.out

    @property
    def reads(self) -> tuple[str, ...]:
        """The reads made in the current step, as JSON."""
        return tuple(self._reads)

    # -- reading and naming ------------------------------------------------------------------

    def read[T](self, fn: Callable[[RunView], T], as_: type[T] | TypeAdapter[T]) -> T:
        adapter = as_ if isinstance(as_, TypeAdapter) else _adapter(as_)
        if self._recorded is not None:
            if not self._recorded:
                raise FlowCodeChanged(-1)  # replay reads more than the journal holds
            text = self._recorded.popleft()
        elif self._view is None:
            raise ContractError(("read",), "the view is not available while the run starts")
        else:
            text = adapter.dump_json(fn(self._view)).decode()
        self._reads.append(text)
        return adapter.validate_json(text)

    def stamp[W: Work | Finish](self, task: _Task, work: W) -> W:
        """``work`` with its decision id root turned into ``<path><root>.<n>``."""
        root = work.decision_id.root
        count = self._issued.get((task.path, root), 0) + 1
        self._issued[task.path, root] = count
        identity = DecisionId(root=f"{task.path}{root}.{count}")
        if identity in self._ids:
            raise ContractError(("decision_id",), f"decision id {identity.root} is not unique")
        self._ids.add(identity)
        return work.model_copy(update={"decision_id": identity})

    # -- driving flows -----------------------------------------------------------------------

    def spawn[T](self, parent: _Task, request: _Spawn) -> FlowChild[T]:
        key = request.key
        if not key or "/" in key:
            raise ContractError(
                ("spawn", "key"), "a child key must be non-empty and contain no '/'"
            )
        path = f"{parent.path}{key}/"
        if any(live.path == path for live in self._children):
            raise ContractError(("spawn", "key"), f"a child {path} is already running")
        task = _Task(path=path, stoppable=request.stoppable, then=request.then)
        self._children.append(task)
        restored = self._pending.pop(path, None)
        definition = next((d for d in self._defs if d.fn is request.fn), None)
        if restored is not None:  # a restart: resume from the child's own latest snapshot
            definition = self._defined(restored.name)
            self._launch(task, definition, self._snapshot_of(definition, restored))
        elif definition is not None:
            if len(request.args) != 1 or not isinstance(request.args[0], definition.snapshot):
                message = f"{definition.name} takes its {definition.snapshot.__name__} snapshot"
                raise ContractError(("spawn", "args"), message)
            self._launch(task, definition, request.args[0])
        else:
            task.coro = request.fn(Flow(self, task), *request.args)
            self._resume(task, None, None)
        return FlowChild(task)

    def _resume(self, task: _Task, value: object, error: BaseException | None) -> None:
        """Run ``task`` to its next suspension, or to its end."""
        if task.coro is None:
            raise ContractError(("flow",), "a flow is resumed only after it was started")
        task.blocked = None
        try:
            block = task.coro.send(value) if error is None else task.coro.throw(error)
        except StopIteration as returned:
            self._returned(task, returned.value)
        else:
            self._suspend(task, block)

    def _suspend(self, task: _Task, block: _Block) -> None:
        task.blocked = block
        match block:
            case _OnDecision(decision=decision):
                self._waiting[decision] = (task, block)
            case _OnTime():
                self._sleeping.append(task)
            case _OnCondition():
                self._conditions.append(task)
            case _OnChild(child=child):
                child.task.joiners.append(task)
            case _:
                raise ContractError(("await",), "a flow may await only Flow methods")

    def _returned(self, task: _Task, result: object) -> None:
        task.done, task.result = True, result
        if task in self._children:
            self._children.remove(task)
        if task.then is not None:
            task.then(result)
        for joiner in task.joiners:
            self._ready.append((joiner, result, None))

    def _abandon(self, task: _Task) -> None:
        """Stop waiting for what ``task`` waits for; a late result for it will be dropped."""
        match task.blocked:
            case _OnDecision(decision=decision):
                del self._waiting[decision]
            case _OnTime():
                self._sleeping.remove(task)
            case _OnCondition():
                self._conditions.remove(task)
            case _OnChild(child=child):
                child.task.joiners.remove(task)

    # -- routing -----------------------------------------------------------------------------

    def _route(self, item: Event | Recorded) -> None:
        match item:
            case RecordedTurn(decision=decision) | RecordedOperation(decision=decision):
                self._answer(decision, item)
            case TimeReached(time=now):
                for task in list(self._sleeping):
                    if isinstance(task.blocked, _OnTime) and task.blocked.time <= now:
                        self._sleeping.remove(task)
                        self._ready.append((task, None, None))
            case StopRequested(control=control):
                if control.action == "stop":
                    self._stop()
            case AdoptionFinished(decision=None):
                proposal = next(
                    (d for d, (_, b) in self._waiting.items() if isinstance(b.work, ProposeWinner)),
                    None,
                )
                if proposal is not None:
                    self._answer(proposal, item)
            case _:
                decision = getattr(item, "decision", None)
                if decision is not None:
                    self._answer(decision, item)

    def _answer(self, decision: DecisionId, item: Event | Recorded) -> None:
        waiter = self._waiting.pop(decision, None)
        if waiter is None:
            kind = item.kind
            if isinstance(item, RecordedTurn):
                kind = EventKind.TURN_FINISHED
            elif isinstance(item, RecordedOperation):
                kind = EventKind.OPERATION_FINISHED
            self.dropped.append(Dropped(event=EventKind(kind), decision=decision))
            return
        task, block = waiter
        self._ready.append((task, self._rebuilt(item, block), None))

    def _rebuilt(self, item: Event | Recorded, block: _OnDecision) -> Event:
        """The event a recorded item stands for: live and replay both go through here."""
        match item:
            case RecordedTurn():
                return TurnFinished(
                    decision=item.decision,
                    invocation=item.invocation,
                    reply=block.reply.model_validate_json(item.reply_json),
                    checkpoint=item.checkpoint,
                )
            case RecordedOperation():
                context = {"operation_registry": self._registry, "persisted_operation": True}
                result = OperationResult.model_validate_json(item.result_json, context=context)
                return OperationFinished(decision=item.decision, result=result)
            case _:
                return item

    def _stop(self) -> None:
        self.stop_requested = True
        for task in self._children:
            if task.stoppable and not task.stopped:
                task.stopped = True
                self._abandon(task)
                self._ready.append((task, None, Stopped()))

    def _settle(self) -> None:
        """Run what is ready, then flows whose condition holds, until nothing moves."""
        while True:
            while self._ready:
                self._resume(*self._ready.popleft())
            waiting = next(
                (t for t in self._conditions if cast("_OnCondition", t.blocked).predicate()), None
            )
            if waiting is None:
                return
            self._conditions.remove(waiting)
            self._ready.append((waiting, None, None))
