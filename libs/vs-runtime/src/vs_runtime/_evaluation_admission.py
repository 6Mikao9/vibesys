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
        """Wait until ``handle_id`` is the next job and a slot is free."""
        async with self._condition:
            if handle_id in self._active or handle_id in self._queued:
                message = f"evaluation handle {handle_id!r} already holds a slot"
                raise ValueError(message)
            self._queued.append(handle_id)
            self._notify()
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

    async def release(self, handle_id: str) -> None:
        """Return ``handle_id``'s lease, if it still holds one."""
        async with self._condition:
            self._active.discard(handle_id)
            self._notify()
            self._condition.notify_all()

    def _notify(self) -> None:
        if self._on_counts is not None:
            self._on_counts(len(self._active), len(self._queued))


__all__ = ["EvaluationAdmission"]
