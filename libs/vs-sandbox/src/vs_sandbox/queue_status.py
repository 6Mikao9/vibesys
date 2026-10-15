"""Each backend's ``QueueStatusReader``: what the run can see of the queue its jobs wait in.

``SlurmQueueStatus`` reads the scheduler through read-only questions (`vs_slurm.read_queue`):
the run's own jobs by their name prefixes (its evaluations, and its agents' GPU commands when
they share the cluster) and the load of their partitions. ``LocalQueueStatus`` and
``ModalQueueStatus`` serve backends without a shared queue: a job starts as soon as the run's
own capacity allows, so nothing is ever queued, and what the backend does not report is None.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from vs_evaluation.api import (
    QueueItem,
    QueueItemKind,
    QueueItemState,
    QueueLoad,
    QueueStatus,
)
from vs_slurm.api import checked_name, read_queue

if TYPE_CHECKING:
    from collections.abc import Sequence

    from vs_slurm.api import OwnJob, PartitionLoad, SchedulerReads

LOCAL_POOL = "local"


class SlurmQueueStatus:
    """The run's Slurm jobs and their partitions' load, read with ``squeue`` and ``sinfo``."""

    def __init__(
        self,
        reads: SchedulerReads,
        *,
        evaluation_prefix: str,
        agent_gpu_prefix: str | None = None,
        partitions: Sequence[str] = (),
    ) -> None:
        """Read through ``reads``; the run's jobs are those named under its prefixes.

        ``partitions`` are read even while the run has no job in them. A prefix or partition
        that is not a plain scheduler name is a ValueError here, not at the first read.
        """
        self._reads = reads
        self._kinds = {checked_name(evaluation_prefix, "prefix"): QueueItemKind.EVALUATION}
        if agent_gpu_prefix is not None:
            self._kinds[checked_name(agent_gpu_prefix, "prefix")] = QueueItemKind.AGENT_GPU_JOB
        self._partitions = tuple(checked_name(item, "partition") for item in partitions)

    def read(self) -> QueueStatus:
        """What the scheduler says now; unreadable when it could not be asked."""
        reading = read_queue(self._reads, prefixes=tuple(self._kinds), partitions=self._partitions)
        if not reading.answered:
            return QueueStatus.unreadable()
        return QueueStatus(
            readable=True,
            items=tuple(self._item(job) for job in reading.jobs),
            loads=tuple(_load(partition) for partition in reading.partitions),
        )

    def _item(self, job: OwnJob) -> QueueItem:
        kind = next(kind for prefix, kind in self._kinds.items() if job.name.startswith(prefix))
        return QueueItem(
            job=job.job_id,
            kind=kind,
            state=QueueItemState.RUNNING if job.running else QueueItemState.QUEUED,
            pool=job.partition,
            waited_s=job.waited_s,
            position=job.position,
            starts_in_s=job.start_in_s,
        )


def _load(partition: PartitionLoad) -> QueueLoad:
    return QueueLoad(
        pool=partition.partition,
        pending=partition.pending,
        running=partition.running,
        gpus_total=partition.gpus_total,
        gpus_busy=partition.gpus_busy,
    )


class LocalQueueStatus:
    """A local backend: the run's jobs start on its own devices, so nothing waits in line."""

    def __init__(self, *, gpus: int | None = None) -> None:
        """Report ``gpus`` devices; None when the run does not know how many it has."""
        self._gpus = gpus

    def read(self) -> QueueStatus:
        """No queue: one local pool with nothing pending."""
        return QueueStatus(
            readable=True, loads=(QueueLoad(pool=LOCAL_POOL, pending=0, gpus_total=self._gpus),)
        )


class ModalQueueStatus:
    """Modal starts each evaluation in its own container and reports no queue to wait in."""

    def read(self) -> QueueStatus:
        """No queue and no pool load Modal reports: everything is unknown but readable."""
        return QueueStatus(readable=True)
