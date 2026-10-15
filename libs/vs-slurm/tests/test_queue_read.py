"""Reading the queue as one run sees it: only its own jobs by name, everyone else only counted.

A generated cluster holds the run's jobs, other runs' jobs of the same user and other users'
jobs across partitions. Whatever it holds, the reading lists exactly the run's queued and
running jobs, places each pending one by scheduler priority, counts every user's jobs and the
partitions' GPUs, never shows another job's name or id, asks nothing but read-only questions,
and turns a lost answer into unknown fields rather than an error.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st

from vs_slurm.api import FakeJob, FakeQueue, SlurmError, read_queue

PREFIX = "vs-run-"
PARTITIONS = ("gpu", "debug")
ME = "me"


@st.composite
def queues(draw: st.DrawFn) -> FakeQueue:
    """A cluster with a few partitions, nodes and every kind of job in them."""
    count = draw(st.integers(0, 12))
    jobs = []
    for job_id in range(100, 100 + count):
        owner = draw(st.sampled_from(["run", "other-run", "other-user"]))
        state = draw(st.sampled_from(["PENDING", "RUNNING"]))
        jobs.append(
            FakeJob(
                job_id=job_id,
                name=f"{PREFIX}{job_id}" if owner == "run" else f"theirs-{job_id}",
                user=ME if owner != "other-user" else "someone",
                state=state,
                partition=draw(st.sampled_from(PARTITIONS)),
                priority=draw(st.integers(1, 5)),
                gpus=draw(st.integers(0, 8)),
                nodes=draw(st.integers(1, 2)),
                submitted_s_ago=draw(st.floats(0, 10_000).map(round)),
                starts_in_s=draw(st.none() | st.floats(1, 10_000).map(round)),
            )
        )
    nodes = {
        f"node{index}": (draw(st.sampled_from(PARTITIONS)), draw(st.integers(0, 8)))
        for index in range(draw(st.integers(0, 4)))
    }
    return FakeQueue(jobs=jobs, nodes=nodes, me=ME)


@given(queue=queues())
def test_the_reading_lists_exactly_the_runs_queued_and_running_jobs(queue: FakeQueue) -> None:
    reading = read_queue(queue, prefixes=(PREFIX,), partitions=("gpu",))

    mine = {str(job.job_id): job for job in queue.jobs if job.name.startswith(PREFIX)}
    assert reading.answered
    assert {job.job_id for job in reading.jobs} == set(mine)
    for job in reading.jobs:
        known = mine[job.job_id]
        assert job.running == (known.state == "RUNNING")
        assert job.partition == known.partition
        assert job.waited_s is not None
        assert job.waited_s >= 0
        assert (job.position is None) == job.running
        if not job.running:
            assert job.start_in_s == known.starts_in_s
    assert all(f"theirs-{job.job_id}" not in repr(reading) for job in queue.jobs)
    assert all(command.split()[0] in {"date", "squeue", "sinfo"} for command in queue.asked)


@given(queue=queues())
def test_a_pending_jobs_place_counts_the_higher_priority_jobs_of_every_user(
    queue: FakeQueue,
) -> None:
    reading = read_queue(queue, prefixes=(PREFIX,))

    for job in reading.jobs:
        if job.running:
            continue
        ours = next(item for item in queue.jobs if str(item.job_id) == job.job_id)
        ahead = [
            item
            for item in queue.jobs
            if item.state == "PENDING"
            and item.partition == ours.partition
            and (item.priority, -item.job_id) > (ours.priority, -ours.job_id)
        ]
        assert job.position == len(ahead) + 1


@given(queue=queues())
def test_each_partition_read_counts_every_users_jobs_and_gpus(queue: FakeQueue) -> None:
    reading = read_queue(queue, prefixes=(PREFIX,), partitions=PARTITIONS)

    assert [load.partition for load in reading.partitions] == sorted(PARTITIONS)
    for load in reading.partitions:
        there = [job for job in queue.jobs if job.partition == load.partition]
        assert load.pending == sum(job.state == "PENDING" for job in there)
        assert load.running == sum(job.state == "RUNNING" for job in there)
        assert load.gpus_busy == sum(
            job.gpus * job.nodes for job in there if job.state == "RUNNING"
        )
        gpus = [count for where, count in queue.nodes.values() if where == load.partition]
        assert load.gpus_total == (sum(gpus) if gpus else None)


@given(queue=queues(), silent=st.sampled_from(["date", "squeue", "sinfo"]))
def test_a_lost_answer_leaves_what_it_would_tell_unknown(queue: FakeQueue, silent: str) -> None:
    queue.silent = frozenset({silent})

    reading = read_queue(queue, prefixes=(PREFIX,), partitions=PARTITIONS)

    if silent in {"date", "squeue"}:
        assert not reading.answered
        assert reading.jobs == reading.partitions == ()
    else:
        assert reading.answered
        assert all(load.gpus_total is None for load in reading.partitions)


def test_the_scheduler_times_are_read_against_the_clusters_own_clock() -> None:
    queue = FakeQueue(
        jobs=[FakeJob(7, f"{PREFIX}a", ME, "PENDING", "gpu", submitted_s_ago=90, starts_in_s=600)],
        now=datetime(2026, 3, 1, 23, 59, 30, tzinfo=UTC),
    )

    (job,) = read_queue(queue, prefixes=(PREFIX,)).jobs

    assert (job.waited_s, job.start_in_s, job.position) == (90.0, 600.0, 1)


def test_unknown_scheduler_fields_are_read_as_unknown() -> None:
    class Printed:
        """What a real squeue prints for a job with no start estimate and odd GRES."""

        def read_scheduler(self, command: str) -> str:
            if command.startswith("date"):
                return (
                    "2026-03-01T10:00:00\n"
                    "41|vs-run-x|PENDING|gpu|4294|Unknown|N/A\n"
                    "42|vs-run-y|RUNNING|gpu|10|2026-03-01T09:00:00|2026-03-01T09:30:00\n"
                    "garbage line"
                )
            if command.startswith("squeue"):
                return "41|PENDING|4294|N/A|1\n42|RUNNING|10|gres/gpu:h100:4|2\n43|RUNNING|9|N/A|1"
            raise SlurmError.truncated_output()

    reading = read_queue(Printed(), prefixes=(PREFIX,))

    pending, running = reading.jobs
    assert (pending.waited_s, pending.start_in_s, pending.position) == (None, None, 1)
    assert (running.waited_s, running.position) == (1800.0, None)
    (load,) = reading.partitions
    assert (load.pending, load.running, load.gpus_busy, load.gpus_total) == (1, 2, 8, None)


@pytest.mark.parametrize("unsafe", ["", "gpu;rm -rf /", "a b", "$(x)", "-p"])
def test_a_name_that_is_not_a_plain_scheduler_name_is_refused(unsafe: str) -> None:
    with pytest.raises(ValueError, match="plain scheduler name"):
        read_queue(FakeQueue(), prefixes=(unsafe,))
    with pytest.raises(ValueError, match="plain scheduler name"):
        read_queue(FakeQueue(), prefixes=(PREFIX,), partitions=(unsafe,))
