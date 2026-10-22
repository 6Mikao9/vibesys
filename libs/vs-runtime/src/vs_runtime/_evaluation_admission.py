"""FIFO admission shared by complete measurements and other evaluation-resource jobs."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable


class EvaluationAdmission:
    """Fair leases for the evaluation resource, one per admitted gate.

    The next command starts only when a slot is free. Time spent queued is
    not time the command has been running. ``on_counts`` is told ``(running,
    waiting)`` after every change and must not call back into the admission.
    """

    def __init__(
        self, slots: int = 1, *, on_counts: Callable[[int, int], None] | None = None
    ) -> None:
        if slots < 1:
            message = "evaluation slots must be positive"
            raise ValueError(message)
        self.capacity = slots
        self._on_counts = on_counts
        self._active: set[str] = set()
        self._idle: set[str] = set()
        self._queued: list[str] = []
        self._condition = asyncio.Condition()

    @asynccontextmanager
    async def lease(self, key: str) -> AsyncIterator[None]:
        """Hold one shared evaluation slot, releasing on every exit, including cancellation.

        Queue outside the caller's execution timeout. A profile capture or another
        GPU gate can use the same admission as the measurement executor.
        """
        await self.acquire(key)
        try:
            yield
        finally:
            await self.release(key)

    async def counts(self) -> tuple[int, int]:
        """Return leases held and jobs waiting for one."""
        async with self._condition:
            return len(self._active), len(self._queued)

    async def acquire(self, handle_id: str) -> None:
        """Register and wait for a lease in FIFO order."""
        await self.enqueue(handle_id)
        try:
            await self.wait(handle_id)
        except BaseException:
            await self.release(handle_id)
            raise

    async def enqueue(self, handle_id: str) -> None:
        """Register before starting asynchronous work, without waiting for capacity.

        The order of these calls fixes FIFO, independently of task scheduling.
        An uncontended idle reservation is reactivated for its next member.
        """
        async with self._condition:
            if handle_id in self._idle:
                self._idle.remove(handle_id)
                return
            if handle_id in self._active or handle_id in self._queued:
                message = f"evaluation handle {handle_id!r} already holds a slot"
                raise ValueError(message)
            self._queued.append(handle_id)
            self._yield_idle()
            self._notify()
            self._condition.notify_all()

    async def wait(self, handle_id: str) -> None:
        """Wait for a previously enqueued lease; cancellation removes its queue entry."""
        async with self._condition:
            if handle_id in self._active:
                return
            if handle_id not in self._queued:
                message = f"evaluation handle {handle_id!r} has not been enqueued"
                raise ValueError(message)
            try:
                await self._condition.wait_for(
                    lambda: (
                        bool(self._queued)
                        and self._queued[0] == handle_id
                        and len(self._active) < self.capacity
                    )
                )
            except BaseException:
                if handle_id in self._queued:
                    self._queued.remove(handle_id)
                    self._notify()
                    self._condition.notify_all()
                raise
            self._queued.pop(0)
            self._active.add(handle_id)
            self._notify()
            self._condition.notify_all()

    async def park(self, handle_id: str) -> None:
        """Retain a completed member's slot only while no job waits for it.

        A subsequent acquire may reclaim this idle reservation. The sequence's
        next member calls acquire again and joins the same FIFO if it yielded.
        """
        async with self._condition:
            if handle_id in self._active:
                self._idle.add(handle_id)
                self._yield_idle()
                self._notify()
                self._condition.notify_all()

    def _yield_idle(self) -> None:
        if self._queued:
            self._active.difference_update(self._idle)
            self._idle.clear()

    async def release(self, handle_id: str) -> None:
        """Return ``handle_id``'s lease, if it still holds one."""
        async with self._condition:
            self._active.discard(handle_id)
            self._idle.discard(handle_id)
            if handle_id in self._queued:
                self._queued.remove(handle_id)
            self._notify()
            self._condition.notify_all()

    def _notify(self) -> None:
        if self._on_counts is not None:
            self._on_counts(len(self._active), len(self._queued))


__all__ = ["EvaluationAdmission"]
