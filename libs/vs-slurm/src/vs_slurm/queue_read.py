"""Read the scheduler queue as one run sees it: its own jobs, and how busy its partitions are.

``read_queue`` asks only read-only questions (``date``, ``squeue``, ``sinfo``) through a
`SchedulerReads`, whose answers arrive whole or not at all (`scheduler_read`). The run's own
jobs are the user's queued and running jobs whose names start with one of the run's prefixes
(an evaluation job is named by `remote_operations.operation_job_name`, under
`operation_job_prefix`). Other jobs are only counted: no other job's name, user or id leaves
this module. A job's place in line is its rank among the partition's pending jobs by
scheduler priority, highest first; a partition's busy GPUs are the GPUs its running jobs
asked for. A question the scheduler did not answer leaves what it would have told unknown
(None); the read itself never fails.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

from vs_slurm.runner import SlurmError

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

_PENDING = "PENDING"
_RUNNING = "RUNNING"
_TIME = "%Y-%m-%dT%H:%M:%S"
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
_GPUS = re.compile(r"gpu(?::[A-Za-z0-9_.-]+)*[:=](\d+)")
_OWN_FIELDS = 7
_LOAD_FIELDS = 5
_NODE_FIELDS = 2

_NOW = "date +%Y-%m-%dT%H:%M:%S"
_OWN = "squeue -h --me -t PENDING,RUNNING -o '%i|%j|%T|%P|%Q|%V|%S'"
"""The user's queued and running jobs: id, name, state, partition, priority, submitted, start."""


class SchedulerReads(Protocol):
    """Asks the scheduler one read-only question; `SlurmJobRunner` is the real one."""

    def read_scheduler(self, command: str) -> str:
        """The whole answer to ``command``; raises `SlurmError` when it did not arrive whole."""
        ...


@dataclass(frozen=True)
class OwnJob:
    """One of the run's jobs that is queued or running."""

    job_id: str
    name: str
    running: bool
    partition: str
    waited_s: float | None
    """Seconds it has waited in line: from submission to its start, or to now while it waits;
    None when the scheduler's times could not be read."""
    position: int | None = None
    """Its place in line among its partition's pending jobs (1 starts next); None if running."""
    start_in_s: float | None = None
    """Seconds until the scheduler expects it to start, when it says."""


@dataclass(frozen=True)
class PartitionLoad:
    """How busy one partition is, counted over every user's jobs."""

    partition: str
    pending: int | None
    running: int | None
    gpus_total: int | None
    gpus_busy: int | None


@dataclass(frozen=True)
class QueueReading:
    """The run's own jobs and its partitions' load; ``answered`` is False when nothing was read."""

    jobs: tuple[OwnJob, ...]
    partitions: tuple[PartitionLoad, ...]
    answered: bool


@dataclass(frozen=True)
class _Pending:
    job_id: str
    priority: int


def checked_name(value: str, what: str) -> str:
    """``value`` when it is a plain scheduler name (safe in a shell word); else a ValueError."""
    if _NAME.fullmatch(value) is None:
        message = f"{what} {value!r} is not a plain scheduler name"
        raise ValueError(message)
    return value


def read_queue(
    reads: SchedulerReads, *, prefixes: Sequence[str], partitions: Sequence[str] = ()
) -> QueueReading:
    """What the scheduler says now of the jobs named under ``prefixes`` and their partitions.

    ``partitions`` are read even when the run has no job in them (where its next job goes).
    """
    for prefix in prefixes:
        checked_name(prefix, "job name prefix")
    for partition in partitions:
        checked_name(partition, "partition")
    own = _answer(reads, f"{_NOW} && {_OWN}")
    if own is None:
        return QueueReading(jobs=(), partitions=(), answered=False)
    now_line, _, rows = own.partition("\n")
    now = _time(now_line)
    mine = [job for job in _own_rows(rows, now) if job.name.startswith(tuple(prefixes))]
    loads = []
    for partition in sorted({*partitions, *(job.partition for job in mine)}):
        if _NAME.fullmatch(partition) is None:
            continue  # a job pending in several partitions lists them all; not one to read
        rows = _answer(reads, f"squeue -h -t PENDING,RUNNING -p {partition} -o '%i|%T|%Q|%b|%D'")
        nodes = _answer(reads, f"sinfo -h -N -p {partition} -o '%n|%G'")
        mine = [_placed(job, rows) if job.partition == partition else job for job in mine]
        loads.append(_load(partition, rows, nodes))
    return QueueReading(jobs=tuple(mine), partitions=tuple(loads), answered=True)


def _answer(reads: SchedulerReads, command: str) -> str | None:
    try:
        return reads.read_scheduler(command)
    except SlurmError:
        return None


def _time(text: str) -> datetime | None:
    """A time the cluster printed in its local zone, which it does not name.

    It is read as UTC: every time here is only subtracted from the cluster's own ``date``, read
    in the same command and zone, so the zone cancels out.
    """
    try:
        return datetime.strptime(text.strip(), _TIME).replace(tzinfo=UTC)
    except ValueError:
        return None


def _seconds(later: datetime | None, earlier: datetime | None) -> float | None:
    if later is None or earlier is None:
        return None
    return (later - earlier).total_seconds()


def _own_rows(rows: str, now: datetime | None) -> Iterable[OwnJob]:
    for line in rows.splitlines():
        fields = [field.strip() for field in line.split("|")]
        if len(fields) != _OWN_FIELDS or fields[2] not in {_PENDING, _RUNNING}:
            continue
        job_id, name, state, partition, _priority, submitted, start = fields
        running = state == _RUNNING
        started = _time(start)
        ahead = None if running else _seconds(started, now)
        waited = _seconds(started if running else now, _time(submitted))
        yield OwnJob(
            job_id=job_id,
            name=name,
            running=running,
            partition=partition,
            waited_s=None if waited is None else max(waited, 0.0),
            start_in_s=ahead if ahead is not None and ahead > 0 else None,
        )


def _pending(rows: str) -> list[_Pending]:
    found = []
    for line in rows.splitlines():
        fields = [field.strip() for field in line.split("|")]
        if len(fields) == _LOAD_FIELDS and fields[1] == _PENDING and fields[2].isdigit():
            found.append(_Pending(fields[0], int(fields[2])))
    return sorted(found, key=lambda item: (-item.priority, _job_order(item.job_id)))


def _job_order(job_id: str) -> tuple[int, str]:
    return (int(job_id), "") if job_id.isdigit() else (0, job_id)


def _placed(job: OwnJob, rows: str | None) -> OwnJob:
    if job.running or rows is None:
        return job
    line = [item.job_id for item in _pending(rows)]
    if job.job_id not in line:
        return job
    return OwnJob(
        job_id=job.job_id,
        name=job.name,
        running=False,
        partition=job.partition,
        waited_s=job.waited_s,
        position=line.index(job.job_id) + 1,
        start_in_s=job.start_in_s,
    )


def _load(partition: str, rows: str | None, nodes: str | None) -> PartitionLoad:
    pending = running = busy = None
    if rows is not None:
        fields = [
            [field.strip() for field in line.split("|")]
            for line in rows.splitlines()
            if line.strip()
        ]
        whole = [row for row in fields if len(row) == _LOAD_FIELDS]
        pending = sum(row[1] == _PENDING for row in whole)
        running = sum(row[1] == _RUNNING for row in whole)
        busy = _busy_gpus(row for row in whole if row[1] == _RUNNING)
    return PartitionLoad(
        partition=partition,
        pending=pending,
        running=running,
        gpus_total=None if nodes is None else _total_gpus(nodes),
        gpus_busy=busy,
    )


def _gpus(gres: str) -> int:
    return sum(int(count) for count in _GPUS.findall(gres))


def _busy_gpus(running: Iterable[list[str]]) -> int | None:
    total = 0
    for row in running:
        per_node, nodes = row[3], row[4]
        if not nodes.isdigit():
            return None
        total += _gpus(per_node) * int(nodes)
    return total


def _total_gpus(nodes: str) -> int | None:
    seen: dict[str, int] = {}
    for line in nodes.splitlines():
        fields = [field.strip() for field in line.split("|")]
        if len(fields) == _NODE_FIELDS and fields[0]:
            seen[fields[0]] = _gpus(fields[1])
    return sum(seen.values()) if seen else None
