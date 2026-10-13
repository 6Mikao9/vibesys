"""The run: every live flow of one search, driven by hand."""

from __future__ import annotations

from collections import deque
from contextlib import suppress
from typing import TYPE_CHECKING, Any

from pydantic import TypeAdapter

from vs_core._registry import ContractError
from vs_core.children import Dropped
from vs_core.types.common import AttemptRef, DecisionId, RunFacts, Seconds
from vs_core.types.kinds import EventKind
from vs_core.types.records import OperationResult
from vs_core.types.search import (
    AdoptionFinished,
    Event,
    OperationFinished,
    StopRequested,
    TimeReached,
    TurnFinished,
    WorkspaceClosed,
)
from vs_core.types.strategy import Decision, Finish, ProposeWinner, WakeAt, Withdraw, Work

from ._defs import FlowDef, _adapter, _Task, definition_of
from ._errors import Deadlock, FlowCodeChanged, Stopped, TaskStopped, TimeUp
from ._flow import Flow
from ._sources import First, Pending, Task, Timer
from ._state import Issued, LiveFlow, LiveFlows, Recorded, RecordedOperation, RecordedTurn

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Mapping

    from vs_core._registry import OperationRegistry
    from vs_core.types.search import RunView

    from ._flow import Group


class _Run:
    """The live flows of one run, driven by hand."""

    def __init__(
        self,
        defs: tuple[FlowDef, ...],
        config: object,
        facts: RunFacts,
        registry: OperationRegistry | None,
    ) -> None:
        self.facts = facts
        self.stop: StopRequested | None = None
        self.closed = False
        self.published: str | None = None
        self.out: list[Decision] = []
        self.dropped: list[Dropped] = []
        self._registry = registry
        self._defs = defs  # the root first
        self._config = config
        self._tasks: list[_Task] = [_Task(path="")]  # live flows, the root first
        self._pendings: dict[DecisionId, Pending] = {}
        self._timers: list[Timer] = []
        self._restorable: dict[str, LiveFlow] = {}
        self._issued: dict[tuple[str, str], int] = {}
        self._view: RunView | None = None
        self._recorded: deque[str] | None = None
        self._reads: list[str] = []

    # -- one step: init, restore or one event ------------------------------------------------

    def start(self) -> list[Decision]:
        """Run the root flow until it first suspends."""
        self._begin(None, None)
        self._launch(self._tasks[0], self._defs[0], self._config)
        return self._end()

    def restore(
        self, base: LiveFlows, view: RunView | None, recorded: tuple[str, ...] | None
    ) -> list[Decision]:
        """Restart the flows of ``base`` from their snapshots with the current code.

        The root starts first. A task starts when its group ``start``s its key again, from the
        latest snapshot of the flow that had that key, with its decision numbering kept.
        """
        self._begin(view, recorded)
        self.stop, self.published = base.stop, base.published
        self._issued = {(i.path, i.label): i.count for i in base.issued}
        self._restorable = {live.path: live for live in base.flows if live.path}
        root = next(live for live in base.flows if not live.path)
        definition = self._defined(root.name)
        if base.stop is not None:
            self._tasks[0].stop.set(base.stop)
        self._launch(self._tasks[0], definition, self._snapshot_of(definition, root))
        return self._end()

    def feed(
        self, item: Event | Recorded, view: RunView | None, recorded: tuple[str, ...] | None
    ) -> list[Decision]:
        """Deliver one event; the flows it unblocks run until all are suspended again."""
        self._begin(view, recorded)
        self._route(item)
        return self._end()

    def _begin(self, view: RunView | None, recorded: tuple[str, ...] | None) -> None:
        self.out = []
        self._reads = []
        self._view = view
        self._recorded = None if recorded is None else deque(recorded)

    def _end(self) -> list[Decision]:
        self._settle()
        if self._recorded:
            raise FlowCodeChanged(-1)  # replay made fewer reads than the journal holds
        return self.out

    def close(self) -> None:
        """Retire the run: unwind its suspended flows so their cleanup cannot start new work."""
        self.closed = True
        for task in tuple(self._tasks):
            if task.coro is not None:
                with suppress(ContractError, RuntimeError):
                    task.coro.close()

    @property
    def reads(self) -> tuple[str, ...]:
        """The reads made in the current step, as JSON."""
        return tuple(self._reads)

    def checkpoint(self) -> LiveFlows:
        """Where every running flow can be restarted from."""
        flows = []
        for task in self._tasks:
            definition = task.flow
            text = (
                _adapter(definition.snapshot).dump_json(task.snapshot).decode()
                if definition
                else ""
            )
            flows.append(
                LiveFlow(
                    path=task.path, name=definition.name if definition else None, snapshot_json=text
                )
            )
        issued = tuple(Issued(path=p, label=lb, count=n) for (p, lb), n in self._issued.items())
        return LiveFlows(
            flows=tuple(flows), issued=issued, stop=self.stop, published=self.published
        )

    def waiting(self) -> tuple[tuple[str, tuple[str, ...]], ...]:
        """What each suspended flow waits for: its path and its sources, described."""
        return tuple(
            (task.path, tuple(s.describe() for s in task.blocked.sources))
            for task in self._tasks
            if task.blocked is not None
        )

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
        if self.closed:
            raise ContractError(("run",), "the run is closed")
        root = work.decision_id.root
        count = self._issued.get((task.path, root), 0) + 1
        self._issued[task.path, root] = count
        return work.model_copy(
            update={"decision_id": DecisionId(root=f"{task.path}{root}.{count}")}
        )

    def issue(self, task: _Task, pending: Pending, decision: Decision) -> Pending:
        """Send a decision out and register the ``Pending`` that its outcome will fill."""
        if pending.decision in self._pendings:
            raise ContractError(
                ("decision_id",), f"decision id {pending.decision.root} is not unique"
            )
        self._pendings[pending.decision] = pending
        task.started.append(pending)
        self.out.append(decision)
        return pending

    def forget(self, pending: Pending) -> None:
        """Abandon a request's outcome: if it arrives later it is dropped and recorded."""
        pending.abandoned = True
        self._pendings.pop(pending.decision, None)

    def timer(self, at: Seconds) -> Timer:
        """A timer for ``at``, and the wake-up that makes core deliver the time."""
        if self.closed:
            raise ContractError(("run",), "the run is closed")
        timer = Timer(at)
        self._timers.append(timer)
        self.out.append(WakeAt(time=at))
        return timer

    # -- tasks -------------------------------------------------------------------------------

    def begin_task[R](
        self,
        owner: Flow,
        group: Group[Any],
        key: str,
        start: tuple[Callable[..., Coroutine[Any, Any, R]], tuple[object, ...]],
    ) -> Task[R]:
        fn, args = start
        if not key or "/" in key:
            raise ContractError(("group", "key"), "a task key must be non-empty and contain no '/'")
        path = f"{owner.path}{key}/"
        if any(live.path == path for live in self._tasks):
            raise ContractError(("group", "key"), f"a task {path} is already running")
        task = _Task(path=path, group=group, handle=Task(key))
        self._tasks.append(task)
        group.running += 1
        if self.stop is not None:
            task.stop.set(self.stop)
            task.stop_pending = group.stops
        restored = self._restorable.pop(path, None)
        definition = definition_of(fn)
        if restored is not None:  # after a code change: resume from the flow's own snapshot
            definition = self._defined(restored.name)
            self._launch(task, definition, self._snapshot_of(definition, restored))
        elif definition is not None:
            if len(args) != 1 or not isinstance(args[0], definition.snapshot):
                message = f"{definition.name} takes its {definition.snapshot.__name__} snapshot"
                raise ContractError(("group", "start"), message)
            self._launch(task, definition, args[0])
        else:
            task.coro = fn(Flow(self, task), *args)
            self._resume(task, None, None)
        return task.handle

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

    def _resume(self, task: _Task, value: object, error: BaseException | None) -> None:
        """Run ``task`` to its next suspension, or to its end."""
        if task.coro is None:
            raise ContractError(("flow",), "a flow is resumed only after it was started")
        task.blocked = None
        try:
            block = task.coro.send(value) if error is None else task.coro.throw(error)
        except StopIteration as returned:
            self._ended(task, returned.value)
        except Stopped:
            if task.group is None:
                raise ContractError(("flow",), "the root flow is never stopped") from None
            self._ended(task, TaskStopped(key=task.handle.key))
        else:
            self._suspend(task, block)

    def _suspend(self, task: _Task, block: object) -> None:
        if not isinstance(block, First):
            raise ContractError(("await",), "a flow may await only ctx operations and sources")
        if not any(source.alive() for source in block.sources):
            raise Deadlock(task.path, tuple(s.describe() for s in block.sources))
        task.blocked = block

    def _ended(self, task: _Task, result: object) -> None:
        self._tasks.remove(task)
        for pending in task.started:
            if not pending.alive():
                continue
            self.forget(pending)
        task.handle.set(result)
        if task.group is not None:
            task.group.running -= 1
            task.group.results.append(result)

    # -- events ------------------------------------------------------------------------------

    def _route(self, item: Event | Recorded) -> None:
        match item:
            case TimeReached(time=now):
                for timer in self._timers:
                    if timer.at <= now:
                        timer.set(TimeUp(at=timer.at))
                self._timers = [t for t in self._timers if not t.ready()]
            case StopRequested(control=control):
                if control.action == "stop" and self.stop is None:
                    self._stop(item)
            case _:
                self._answer(item)

    def _answer(self, item: Event | Recorded) -> None:
        decision = self._answered_decision(item)
        if decision is None:
            return  # an event that answers nothing of ours (an agent's own measurement)
        pending = self._pendings.pop(decision, None)
        if pending is None:
            self.dropped.append(Dropped(event=_kind_of(item), decision=decision))
        else:
            pending.set(self._rebuilt(item, pending))

    def _answered_decision(self, item: Event | Recorded) -> DecisionId | None:
        """The request this event answers, by the table ``_ANSWER_RULES``.

        A close answers the ``Withdraw`` of its attempt (core reports it under the decision that
        opened the attempt); an adoption without a decision answers the ``ProposeWinner``;
        anything else answers the decision it names.
        """
        for rule in _ANSWER_RULES:
            found = rule(item, self._pendings)
            if found is not None:
                return found
        return None

    def _rebuilt(self, item: Event | Recorded, pending: Pending) -> Event:
        """The event a recorded item stands for: live and replay both go through here."""
        match item:
            case RecordedTurn():
                return TurnFinished(
                    decision=item.decision,
                    invocation=item.invocation,
                    reply=pending.reply.model_validate_json(item.reply_json),
                    checkpoint=item.checkpoint,
                )
            case RecordedOperation():
                context = {"operation_registry": self._registry, "persisted_operation": True}
                result = OperationResult.model_validate_json(item.result_json, context=context)
                return OperationFinished(decision=item.decision, result=result)
            case _:
                return item

    def _stop(self, item: StopRequested) -> None:
        self.stop = item
        for task in self._tasks:
            task.stop.set(item)
            if task.group is not None and task.group.stops and not task.stopped:
                task.stop_pending = True

    def _settle(self) -> None:
        """Resume flows that can move, in the order they started, until none can."""
        while True:
            for task in tuple(self._tasks):
                if self._move(task):
                    break
            else:
                return

    def _move(self, task: _Task) -> bool:
        """Resume ``task`` if it has a stop to take or a ready source; whether it moved."""
        if task.blocked is None:
            return False
        if task.stop_pending and task.shield == 0:
            task.stop_pending, task.stopped = False, True
            self._resume(task, None, Stopped())
            return True
        ready = next((s for s in task.blocked.sources if s.ready()), None)
        if ready is None:
            return False
        self._resume(task, ready.take(), None)
        return True


def _kind_of(item: Event | Recorded) -> EventKind:
    if isinstance(item, RecordedTurn):
        return EventKind.TURN_FINISHED
    if isinstance(item, RecordedOperation):
        return EventKind.OPERATION_FINISHED
    return EventKind(item.kind)


def _closed_withdraw(
    item: Event | Recorded, waiting: Mapping[DecisionId, Pending]
) -> DecisionId | None:
    if not isinstance(item, WorkspaceClosed):
        return None
    attempt = item.settlement.attempt.attempt_id
    for decision, pending in waiting.items():
        work = pending.work
        if (
            isinstance(work, Withdraw)
            and isinstance(work.target, AttemptRef)
            and work.target.attempt_id == attempt
        ):
            return decision
    return None


def _adoption_of_proposal(
    item: Event | Recorded, waiting: Mapping[DecisionId, Pending]
) -> DecisionId | None:
    if not isinstance(item, AdoptionFinished) or item.decision is not None:
        return None
    return next((d for d, p in waiting.items() if isinstance(p.work, ProposeWinner)), None)


def _own_decision(
    item: Event | Recorded, _waiting: Mapping[DecisionId, Pending]
) -> DecisionId | None:
    return getattr(item, "decision", None)


_ANSWER_RULES = (_closed_withdraw, _adoption_of_proposal, _own_decision)
"""Which waiting request an event answers, tried in order; see ``_Run._answered_decision``."""
