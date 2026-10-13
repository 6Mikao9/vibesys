"""``Flow`` (the ``ctx`` of a flow), ``Group``, and the role protocols helpers can take."""

from __future__ import annotations

from collections import deque
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Protocol, Self, overload

from pydantic import BaseModel, TypeAdapter

from vs_core._registry import ContractError
from vs_core.types.strategy import Finish, Wait, Work
from vs_core.types.until import Failed, Within

from ._defs import UNBOUNDED, _Task
from ._errors import Stopped, TaskStopped
from ._retry import Again, GaveUp, RetryPolicy, Tries
from ._sources import First, Pending, Source, StopSource, Task, Timer

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Coroutine, Iterator

    from vs_core.types.common import RunFacts, Seconds
    from vs_core.types.search import Event, RunView

    from ._run import _Run


class Waits(Protocol):
    """The part of ``Flow`` that waits: for a helper that only issues requests."""

    async def wait(
        self, request: Work, until: Within, *, reply: type[BaseModel] = BaseModel
    ) -> Event:
        """Start a request and return its outcome."""
        ...

    def start(self, request: Work, until: Within, *, reply: type[BaseModel] = BaseModel) -> Pending:
        """Start a request; wait for it later."""
        ...

    async def first[T](self, *sources: Source[T]) -> T:
        """The next value of whichever source is ready first."""
        ...


class Reads(Protocol):
    """The part of ``Flow`` that reads the run: for a helper that only needs the view."""

    facts: RunFacts

    def read[T](self, fn: Callable[[RunView], T], as_: type[T] | TypeAdapter[T]) -> T:
        """Read the view (journaled)."""
        ...

    def now(self) -> Seconds:
        """The run clock."""
        ...


# -- the author's handles ----------------------------------------------------------------------


class Flow:
    """The ``ctx`` every flow function receives. See ``docs/contributing/flows.md``."""

    def __init__(self, run: _Run, task: _Task) -> None:
        """Bind the handle to its run and its own task."""
        self._run = run
        self._task = task
        self.facts: RunFacts = run.facts
        """The run's facts, constant for the run."""

    def checkpoint(self) -> None:
        """Raise a pending stop before anything is issued, awaited or acquired (unless shielded)."""
        task = self._task
        if task.stop_pending and task.shield == 0:
            task.stop_pending, task.stopped = False, True
            raise Stopped

    # requests and waiting

    def start(self, request: Work, until: Within, *, reply: type[BaseModel] = BaseModel) -> Pending:
        """Start ``request`` now, bounded by ``until``; its outcome is the ``Pending``'s value."""
        self.checkpoint()
        stamped = self._run.stamp(self._task, request)
        return self._run.issue(
            self._task, Pending(stamped, reply), Wait(request=stamped, until=until)
        )

    async def wait(
        self, request: Work, until: Within, *, reply: type[BaseModel] = BaseModel
    ) -> Event:
        """Start ``request`` and return its outcome: the result event, or ``Failed``."""
        pending = self.start(request, until, reply=reply)
        try:
            return await pending
        except Stopped:
            self._run.forget(pending)  # the outcome, if it still comes, is dropped
            raise

    @overload
    async def first[A](self, a: Source[A], /) -> A: ...

    @overload
    async def first[A, B](self, a: Source[A], b: Source[B], /) -> A | B: ...

    @overload
    async def first[A, B, C](self, a: Source[A], b: Source[B], c: Source[C], /) -> A | B | C: ...

    @overload
    async def first[A, B, C, D](
        self, a: Source[A], b: Source[B], c: Source[C], d: Source[D], /
    ) -> A | B | C | D: ...

    async def first(self, *sources: Source[object]) -> object:
        """The next value of whichever source is ready first; ties go to the earliest."""
        self.checkpoint()
        return await First(sources)

    def timer(self, at: Seconds) -> Timer:
        """A source that fires once, when the run clock reaches ``at``."""
        return self._run.timer(at)

    @property
    def path(self) -> str:
        """Where this flow sits: empty for the root, ``"a/b/"`` for task ``b`` of task ``a``."""
        return self._task.path

    @property
    def stopped(self) -> StopSource:
        """The operator's stop as a source: yields the ``StopRequested`` event, once."""
        return self._task.stop

    @property
    def stop_requested(self) -> bool:
        """Whether a stop has arrived."""
        return self._run.stop is not None

    async def finish(self, finish: Finish) -> Failed:
        """End the run. Returns only if core refuses the ``Finish``; otherwise never resumes."""
        stamped = self._run.stamp(self._task, finish)
        refused = await self._run.issue(self._task, Pending(stamped, BaseModel), stamped)
        if not isinstance(refused, Failed):
            raise ContractError(("finish",), "a finish is answered only by a failure")
        return refused

    # structure

    def group[T](self, *, limit: int | None = None, stops: bool = True) -> Group[T]:
        """A scope for concurrent tasks; leaving it waits for all of them."""
        return Group(self._run, self, limit, stops=stops)

    @contextmanager
    def shielded(self) -> Iterator[None]:
        """A region a stop cannot interrupt: it is delivered at the first await after it."""
        self._task.shield += 1
        try:
            yield
        finally:
            self._task.shield -= 1

    async def retry[R, V](
        self, attempt: Callable[[Tries[R]], Awaitable[V | Again[R]]], policy: RetryPolicy
    ) -> V | GaveUp[R]:
        """Repeat ``attempt`` until it gives a value, at most ``policy.max_tries`` times."""
        reasons: list[R] = []
        for number in range(1, policy.max_tries + 1):
            outcome = await attempt(Tries(number=number, reasons=tuple(reasons)))
            if not isinstance(outcome, Again):
                return outcome
            reasons.append(outcome.reason)
            if policy.backoff is not None and number < policy.max_tries:
                await self.timer(self.now() + policy.backoff.delay(number - 1))
        return GaveUp(reasons=tuple(reasons))

    # reading and publishing

    def read[T](self, fn: Callable[[RunView], T], as_: type[T] | TypeAdapter[T]) -> T:
        """Read the view. The value is journaled, so live and replay see the same value."""
        return self._run.read(fn, as_)

    def now(self) -> Seconds:
        """The run clock (a journaled read)."""
        return self.read(lambda view: view.now_at, float)

    def publish(self, record: BaseModel) -> None:
        """Replace the flow's published record: what status views and tests read of a run."""
        self._run.published = record.model_dump_json()

    async def safe_point(self, snapshot: object) -> None:
        """Record ``snapshot``: where a code change may resume this flow (see ``flow``)."""
        definition = self._task.flow
        if definition is None:
            raise ContractError(("safe_point",), "only a registered flow has safe points")
        if not isinstance(snapshot, definition.snapshot):
            raise ContractError(
                ("safe_point",), f"{definition.name} snapshots are {definition.snapshot.__name__}"
            )
        self._task.snapshot = snapshot


class Group[T](Source[T | TaskStopped]):
    """A scope for concurrent tasks and the source of their results, one each, as they finish.

    Leaving the ``async with`` waits for every task still running. When the operator stops the
    run, tasks of a group with ``stops=True`` get ``Stopped`` at their next unshielded await.
    """

    def __init__(self, run: _Run, owner: Flow, limit: int | None, *, stops: bool) -> None:
        """Make the group; ``Flow.group`` is the way in."""
        self._run = run
        self._owner = owner
        self._limit = limit
        self.stops = stops
        self.results: deque[T | TaskStopped] = deque()
        self.running = 0

    @property
    def free(self) -> int:
        """How many more tasks the limit allows."""
        return UNBOUNDED if self._limit is None else self._limit - self.running

    def start[R, *Ts](
        self, key: str, fn: Callable[[Flow, *Ts], Coroutine[Any, Any, R]], *args: *Ts
    ) -> Task[R]:
        """Start ``fn(ctx, *args)`` as the task ``key``; it runs to its first await at once.

        A key that has a live snapshot (after a code change) resumes that flow instead.
        """
        if self.free <= 0:
            raise ContractError(("group", "start"), "the group is full: read `free` first")
        return self._run.begin_task(self._owner, self, key, (fn, args))

    async def __aenter__(self) -> Self:
        """Enter the scope."""
        return self

    async def __aexit__(self, kind: type[BaseException] | None, *_: object) -> None:
        """Wait for every task (unless the body crashed: a defect ends the run anyway)."""
        if kind is not None and not issubclass(kind, Stopped):
            return
        with self._owner.shielded():
            while self.running:
                await self

    def __aiter__(self) -> Self:
        """Iterate the results as tasks finish."""
        return self

    async def __anext__(self) -> T | TaskStopped:
        """The next result; the iteration ends when no task is running and no result is left."""
        if not self.alive():
            raise StopAsyncIteration
        return await self

    def ready(self) -> bool:
        return bool(self.results)

    def take(self) -> T | TaskStopped:
        return self.results.popleft()

    def alive(self) -> bool:
        return bool(self.results) or self.running > 0

    def describe(self) -> str:
        return f"group({self.running} running)"
