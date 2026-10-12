"""Flows: a search written as straight-line async code whose effects are journaled and replayed.

A flow is an ``async def`` that reads top to bottom::

    async def root(ctx: Flow, config: Config) -> None:
        plan = await ctx.wait(AskAgent(decision_id=..., agent="planner", ...), Within(600), reply=Plan)
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
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from functools import cache
from hashlib import sha256
from typing import TYPE_CHECKING, Any, cast

from pydantic import BaseModel, TypeAdapter

from ._registry import ContractError
from .children import Dropped
from .types.agents import Checkpoint
from .types.common import DecisionId, InvocationRef, RunFacts, Seconds, Value
from .types.kinds import EventKind
from .types.search import (
    AdoptionFinished,
    Event,
    StopRequested,
    TimeReached,
    TurnFinished,
)
from .types.strategy import Decision, Finish, ProposeWinner, StrategyState, Wait, WakeAt, Work
from .types.until import Failed, Within

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Generator

    from .types.search import RunView
    from .types.strategy import SearchDeclaration


class Stopped(Exception):  # noqa: N818  # LW-220424 [N818]; read as an event ("the child was Stopped"), like asyncio's CancelledError, not as an error condition.
    """Thrown at the await a ``stoppable`` child is suspended on when the operator stops the run.

    The child may catch it and await more (to wind down). Letting it escape is a bug.
    """


class FlowCodeChanged(Exception):  # noqa: N818  # LW-220424 [N818]; the name states what happened, as the replay check reports it.
    """The flow code no longer reproduces the journal: replay diverged at ``step``."""

    def __init__(self, step: int) -> None:
        """Name the journal step at which the code and the journal disagree."""
        super().__init__(f"the flow code diverged from its journal at step {step}")
        self.step = step


# -- the journal -------------------------------------------------------------------------------


class RecordedTurn(Value):
    """A ``TurnFinished`` as the journal keeps it: the reply as JSON, rebuilt by the waiter's model."""

    decision: DecisionId
    invocation: InvocationRef
    reply_json: str
    checkpoint: Checkpoint | None = None


class Step(Value):
    """What one ``init`` or ``update`` did: its input, the reads it made, its output's digest."""

    event: Event | None = None
    turn: RecordedTurn | None = None
    reads: tuple[str, ...] = ()
    digest: str


class FlowState(StrategyState):
    """The persisted state of a flow search: the run's facts and the journal replay needs."""

    facts: RunFacts
    journal: tuple[Step, ...] = ()
    dropped: tuple[Dropped, ...] = ()
    """Results that reached no waiting flow (an abandoned or unknown decision)."""


_DECISIONS = TypeAdapter(list[Decision])


def _digest(
    before: str, item: Event | RecordedTurn | None, reads: tuple[str, ...], out: list[Decision]
) -> str:
    """A hash chained over the history, so equal digests mean equal journals so far."""
    parts = [
        before,
        item.model_dump_json() if item else "",
        *reads,
        _DECISIONS.dump_json(out).decode(),
    ]
    return sha256("\n".join(parts).encode()).hexdigest()


# -- what a suspended flow waits for -------------------------------------------------------------


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
        return self._run.spawn(self._task, key, lambda ctx: fn(ctx, *args), stoppable, then)

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
        root: Callable[[Flow, Any], Coroutine[Any, Any, None]],
        config: object,
        facts: RunFacts,
    ) -> None:
        self.facts = facts
        self.stop_requested = False
        self.out: list[Decision] = []
        self.dropped: list[Dropped] = []
        self._root = root
        self._config = config
        self._ready: deque[tuple[_Task, object, BaseException | None]] = deque()
        self._waiting: dict[DecisionId, tuple[_Task, _OnDecision]] = {}
        self._sleeping: list[_Task] = []
        self._conditions: list[_Task] = []
        self._children: list[_Task] = []  # live spawned flows, in spawn order
        self._issued: dict[tuple[str, str], int] = {}
        self._ids: set[DecisionId] = set()
        self._view: RunView | None = None
        self._recorded: deque[str] | None = None
        self._reads: list[str] = []

    # -- one step: init or one event ---------------------------------------------------------

    def start(self) -> list[Decision]:
        """Run the root flow until it first suspends."""
        task = _Task(path="")
        task.coro = self._root(Flow(self, task), self._config)
        self._begin(None, None)
        self._resume(task, None, None)
        return self._end()

    def feed(
        self, item: Event | RecordedTurn, view: RunView | None, recorded: tuple[str, ...] | None
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

    def spawn[T](
        self,
        parent: _Task,
        key: str,
        start: Callable[[Flow], Coroutine[Any, Any, T]],
        stoppable: bool,  # noqa: FBT001  # LW-220425 [FBT001]; forwarded from the keyword-only `Flow.spawn` argument of the same name.
        then: Callable[[T], None] | None,
    ) -> FlowChild[T]:
        if not key or "/" in key:
            raise ContractError(
                ("spawn", "key"), "a child key must be non-empty and contain no '/'"
            )
        path = f"{parent.path}{key}/"
        if any(live.path == path for live in self._children):
            raise ContractError(("spawn", "key"), f"a child {path} is already running")
        task = _Task(path=path, stoppable=stoppable, then=then)
        task.coro = start(Flow(self, task))
        self._children.append(task)
        self._resume(task, None, None)
        return FlowChild(task)

    def _resume(self, task: _Task, value: object, error: BaseException | None) -> None:
        """Run ``task`` to its next suspension, or to its end."""
        assert task.coro is not None  # noqa: S101  # LW-220426 [S101]; set where every task is made.
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

    def _route(self, item: Event | RecordedTurn) -> None:
        match item:
            case RecordedTurn(decision=decision):
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

    def _answer(self, decision: DecisionId, item: Event | RecordedTurn) -> None:
        waiter = self._waiting.pop(decision, None)
        if waiter is None:
            kind = EventKind.TURN_FINISHED if isinstance(item, RecordedTurn) else item.kind
            self.dropped.append(Dropped(event=EventKind(kind), decision=decision))
            return
        task, block = waiter
        if isinstance(item, RecordedTurn):
            item = TurnFinished(
                decision=decision,
                invocation=item.invocation,
                reply=block.reply.model_validate_json(item.reply_json),
                checkpoint=item.checkpoint,
            )
        self._ready.append((task, item, None))

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


# -- the search --------------------------------------------------------------------------------


def _recorded(event: Event) -> tuple[Event | None, RecordedTurn | None]:
    """The journal's form of an event: a turn keeps its reply as JSON."""
    if not isinstance(event, TurnFinished):
        return event, None
    turn = RecordedTurn(
        decision=event.decision,
        invocation=event.invocation,
        reply_json=event.reply.model_dump_json(),
        checkpoint=event.checkpoint,
    )
    return None, turn


def _key(state: FlowState) -> tuple[int, str]:
    return len(state.journal), state.journal[-1].digest


class FlowSearch[C]:
    """A ``Search`` whose behavior is one root flow function.

    ``update`` keeps the live runtime of the newest state it produced. Given any other state
    (after a restart, or a test branching from an older one) it rebuilds the runtime by replaying
    the journal, and raises ``FlowCodeChanged`` if the code produces different decisions.
    """

    def __init__(
        self,
        declaration: SearchDeclaration,
        root: Callable[[Flow, C], Coroutine[Any, Any, None]],
        config: C,
    ) -> None:
        """Build the search from its declaration, its root flow and its configuration."""
        self.declaration = declaration
        self._root = root
        self._config = config
        self._cached: tuple[tuple[int, str], _Run] | None = None

    def fresh(self) -> FlowSearch[C]:
        """A search with the same flow and config and no live runtime, as a restart builds one."""
        return FlowSearch(self.declaration, self._root, self._config)

    @property
    def state_model(self) -> type[FlowState]:
        """The class of the persisted state."""
        return FlowState

    def init(self, config: C, facts: RunFacts) -> tuple[FlowState, list[Decision]]:
        """Start the root flow; the journal begins with the step that started it."""
        if config != self._config:
            raise ContractError(("config",), "init must get the config the search was built with")
        run = _Run(self._root, self._config, facts)
        decisions = run.start()
        step = Step(digest=_digest("", None, (), decisions))
        state = FlowState(schema_version=1, facts=facts, journal=(step,))
        self._cached = (_key(state), run)
        return state, decisions

    def update(
        self, state: FlowState, event: Event, view: RunView
    ) -> tuple[FlowState, list[Decision]]:
        """Deliver ``event`` to the flows it unblocks and return what they decided."""
        run = self._runtime(state)
        self._cached = None  # a failed update leaves the runtime half-way: never reuse it
        recorded, turn = _recorded(event)
        item = recorded or turn
        assert item is not None  # noqa: S101  # LW-220426 [S101]; _recorded returns exactly one.
        decisions = run.feed(item, view, None)
        step = Step(
            event=recorded,
            turn=turn,
            reads=run.reads,
            digest=_digest(state.journal[-1].digest, item, run.reads, decisions),
        )
        new = state.model_copy(
            update={"journal": (*state.journal, step), "dropped": tuple(run.dropped)}
        )
        self._cached = (_key(new), run)
        return new, decisions

    def _runtime(self, state: FlowState) -> _Run:
        """The live runtime for ``state``: the cached one, or one rebuilt by replay."""
        if self._cached is not None and self._cached[0] == _key(state):
            return self._cached[1]
        run = _Run(self._root, self._config, state.facts)
        before = ""
        for index, step in enumerate(state.journal):
            item = step.event or step.turn
            try:
                decisions = run.start() if item is None else run.feed(item, None, step.reads)
            except FlowCodeChanged as changed:
                raise FlowCodeChanged(index) from changed
            if _digest(before, item, step.reads, decisions) != step.digest:
                raise FlowCodeChanged(index)
            before = step.digest
        return run
