"""Contract suite for :class:`~vs_evaluation.queue_status.QueueStatusReader`.

Subclass :class:`QueueStatusReaderContract`, name the subclass ``Test<Variant>`` and implement
:meth:`~QueueStatusReaderContract.harness`. Every implementation (Slurm over a fake queue,
local, Modal, and the static Fake) passes the same cases. A backend without a shared queue
(``queues=False``) never reports a queued job; what it runs at once it may report as running.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from vs_evaluation.queue_status import QueueItemState, QueueStatus

if TYPE_CHECKING:
    from collections.abc import Callable

    from vs_evaluation.queue_status import QueueStatusReader


@dataclass(frozen=True)
class QueueWorld:
    """A backend's queue: the run's jobs, and other work that competes with them."""

    queued: int = 0
    """The run's own jobs waiting in line."""
    running: int = 0
    """The run's own jobs running."""
    others: int = 0
    """Other users' and other runs' jobs, queued and running."""


@dataclass(frozen=True)
class QueueStatusHarness:
    """How one implementation is put in each situation the contract describes."""

    reporting: Callable[[QueueWorld], QueueStatusReader]
    """A reader of a backend whose queue holds ``world``."""
    queues: bool
    """Whether the backend has a shared queue its jobs wait in."""
    unreachable: Callable[[], QueueStatusReader] | None = None
    """A reader of a backend that cannot be asked; None when the backend is always at hand."""


_WORLDS = (
    QueueWorld(),
    QueueWorld(queued=2),
    QueueWorld(running=1, others=3),
    QueueWorld(queued=1, running=2, others=5),
)


class QueueStatusReaderContract:
    """Cases for :class:`~vs_evaluation.queue_status.QueueStatusReader`."""

    def harness(self) -> QueueStatusHarness:
        """A fresh harness for one case."""
        raise NotImplementedError

    def test_a_reading_survives_the_wire(self) -> None:
        """What a reader returns is the typed status, and its JSON reads back as itself."""
        for world in _WORLDS:
            status = self.harness().reporting(world).read()
            assert QueueStatus.model_validate_json(status.model_dump_json()) == status

    def test_reading_changes_nothing(self) -> None:
        """Reading again, with nothing changed in the backend, says the same."""
        for world in _WORLDS:
            reader = self.harness().reporting(world)
            assert reader.read() == reader.read()

    def test_only_the_runs_own_jobs_are_items(self) -> None:
        """Other work is counted in loads, never listed: no more items than the run's jobs."""
        harness = self.harness()
        for world in _WORLDS:
            status = harness.reporting(world).read()
            assert status.readable
            queued = [item for item in status.items if item.state is QueueItemState.QUEUED]
            running = [item for item in status.items if item.state is QueueItemState.RUNNING]
            assert len(queued) == (world.queued if harness.queues else 0)
            assert len(running) <= world.running + (0 if harness.queues else world.queued)
            assert len({item.job for item in status.items}) == len(status.items)

    def test_a_running_job_has_no_place_in_line(self) -> None:
        """Places in line and expected starts belong to queued jobs only."""
        for world in _WORLDS:
            for item in self.harness().reporting(world).read().items:
                if item.state is QueueItemState.RUNNING:
                    assert item.position is None
                    assert item.starts_in_s is None

    def test_a_backend_that_cannot_be_asked_reads_as_unreadable(self) -> None:
        """No exception: the status says it is unreadable and knows nothing."""
        unreachable = self.harness().unreachable
        if unreachable is not None:
            assert unreachable().read() == QueueStatus.unreadable()
