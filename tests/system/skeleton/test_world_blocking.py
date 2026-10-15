"""A skeleton world's blocking work shares the thread of the code that asked for it.

A ``SimThreads`` binds to the first real thread that uses it and refuses a second. A world
whose hosts hand blocking work to the asyncio executor reaches a Fake's ``SimThreads`` from
a worker thread, and which worker (a reused one or a new one) depends on how loaded the machine
is, so a test failed only in CI. The world runs that work inline by default, so no second real
thread exists; a world that must use workers says so, and the refusal is then certain.
"""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

import pytest
from tests.support.skeleton_world import open_skeleton_world

from vs_sim.api import ThreadBlockingRunner
from vs_sim.api.testing import SimMisuseError, SimThreads

if TYPE_CHECKING:
    from pathlib import Path


def _bound_to_this_thread() -> partial[bool]:
    """A try-acquire of a simulated lock the calling thread has used, so it is the one real thread bound to it."""
    lock = SimThreads().lock()
    with lock:
        pass
    return partial(lock.acquire, blocking=False)


async def test_a_world_runs_blocking_work_where_a_simulated_lock_can_serve_it(
    tmp_path: Path,
) -> None:
    try_acquire = _bound_to_this_thread()
    with open_skeleton_world(tmp_path) as world:
        assert await world.blocking.run(try_acquire)


async def test_a_world_with_worker_threads_refuses_a_simulated_lock_used_from_a_worker(
    tmp_path: Path,
) -> None:
    try_acquire = _bound_to_this_thread()
    with (
        open_skeleton_world(tmp_path, blocking=ThreadBlockingRunner()) as world,
        pytest.raises(SimMisuseError),
    ):
        await world.blocking.run(try_acquire)
