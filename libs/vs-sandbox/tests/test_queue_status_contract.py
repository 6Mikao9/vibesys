"""Every backend's queue status reader passes the one contract.

Slurm is read through the scheduler's read-only questions, answered by `vs_slurm`'s
in-memory `FakeQueue`; local and Modal backends have no shared queue. A Slurm reader also
tells the run's evaluations from its agents' GPU jobs by their name prefixes.
"""

from __future__ import annotations

from vs_evaluation.api import QueueItemKind, QueueItemState
from vs_evaluation.api.testing import QueueStatusHarness, QueueStatusReaderContract, QueueWorld
from vs_sandbox.api import LocalQueueStatus, ModalQueueStatus, SlurmQueueStatus
from vs_slurm.api import FakeJob, FakeQueue

EVALUATIONS = "vs-eval-"
AGENT_JOBS = "vs-agent-"


def _cluster(world: QueueWorld) -> FakeQueue:
    jobs = [
        FakeJob(100 + index, f"{EVALUATIONS}{index}", "me", "PENDING", "gpu")
        for index in range(world.queued)
    ]
    jobs += [
        FakeJob(200 + index, f"{AGENT_JOBS}{index}", "me", "RUNNING", "gpu")
        for index in range(world.running)
    ]
    jobs += [
        FakeJob(300 + index, f"theirs-{index}", "someone", "PENDING", "gpu")
        for index in range(world.others)
    ]
    return FakeQueue(jobs=jobs, nodes={"node0": ("gpu", 8)})


def _slurm(queue: FakeQueue) -> SlurmQueueStatus:
    return SlurmQueueStatus(
        queue, evaluation_prefix=EVALUATIONS, agent_gpu_prefix=AGENT_JOBS, partitions=("gpu",)
    )


class TestSlurmQueueStatus(QueueStatusReaderContract):
    def harness(self) -> QueueStatusHarness:
        return QueueStatusHarness(
            reporting=lambda world: _slurm(_cluster(world)),
            unreachable=lambda: _slurm(FakeQueue(silent=frozenset({"squeue"}))),
            queues=True,
        )


class TestLocalQueueStatus(QueueStatusReaderContract):
    def harness(self) -> QueueStatusHarness:
        return QueueStatusHarness(reporting=lambda _: LocalQueueStatus(gpus=2), queues=False)


class TestModalQueueStatus(QueueStatusReaderContract):
    def harness(self) -> QueueStatusHarness:
        return QueueStatusHarness(reporting=lambda _: ModalQueueStatus(), queues=False)


def test_slurm_tells_evaluations_from_agent_gpu_jobs_and_counts_other_work() -> None:
    status = _slurm(_cluster(QueueWorld(queued=2, running=1, others=3))).read()

    kinds = {(item.kind, item.state) for item in status.items}
    assert kinds == {
        (QueueItemKind.EVALUATION, QueueItemState.QUEUED),
        (QueueItemKind.AGENT_GPU_JOB, QueueItemState.RUNNING),
    }
    (load,) = status.loads
    assert (load.pool, load.pending, load.running, load.gpus_total) == ("gpu", 5, 1, 8)
