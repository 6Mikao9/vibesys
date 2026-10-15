"""A scheduler queue held in memory that answers the read-only questions `queue_read` asks.

`FakeQueue` holds every user's jobs and the partitions' nodes, and answers ``date``,
``squeue`` and ``sinfo`` in the formats ``read_queue`` asks for, as slurmctld would for the
user ``me``. It records every command it was asked, so a test can see that a reader only
reads, and a program named in ``silent`` answers nothing whole (its reply was lost).
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

from vs_slurm.runner import SlurmError

_TIME = "%Y-%m-%dT%H:%M:%S"
_READ_ONLY = frozenset({"date", "squeue", "sinfo"})


@dataclass(frozen=True)
class FakeJob:
    """One job in the fake queue."""

    job_id: int
    name: str
    user: str
    state: Literal["PENDING", "RUNNING"]
    partition: str
    priority: int = 1000
    gpus: int = 1
    nodes: int = 1
    submitted_s_ago: float = 60.0
    starts_in_s: float | None = None
    """For a pending job, when the scheduler expects it to start; for a running one, ignored."""


@dataclass
class FakeQueue:
    """Every user's jobs, the partitions' nodes and the clock, as one cluster's scheduler sees them."""

    jobs: list[FakeJob] = field(default_factory=list)
    nodes: dict[str, tuple[str, int]] = field(default_factory=dict)
    """By node name: its partition and its GPU count."""
    me: str = "me"
    now: datetime = field(default_factory=lambda: datetime(2026, 1, 1, 12, tzinfo=UTC))
    silent: frozenset[str] = frozenset()
    """Programs whose answers never arrive whole."""
    asked: list[str] = field(default_factory=list)

    def read_scheduler(self, command: str) -> str:
        """Answer ``command``, a read-only question or several joined by ``&&``."""
        self.asked.append(command)
        answers = []
        for part in command.split("&&"):
            words = shlex.split(part)
            program = words[0] if words else ""
            if program not in _READ_ONLY:
                message = f"the fake queue answers only read-only questions, not {program!r}"
                raise AssertionError(message)
            if program in self.silent:
                raise SlurmError.truncated_output()
            answers.append(self._answer(program, words[1:]))
        return "\n".join(answer for answer in answers if answer)

    def _answer(self, program: str, words: list[str]) -> str:
        if program == "date":
            return self.now.strftime(_TIME)
        if program == "sinfo":
            partition = _option(words, "-p")
            return "\n".join(
                f"{node}|gpu:h100:{gpus}(S:0-1)"
                for node, (where, gpus) in sorted(self.nodes.items())
                if where == partition
            )
        if "--me" in words:
            return "\n".join(self._own_row(job) for job in self.jobs if job.user == self.me)
        partition = _option(words, "-p")
        return "\n".join(
            f"{job.job_id}|{job.state}|{job.priority}|gres/gpu:{job.gpus}|{job.nodes}"
            for job in self.jobs
            if job.partition == partition
        )

    def _own_row(self, job: FakeJob) -> str:
        submitted = self.now - timedelta(seconds=job.submitted_s_ago)
        if job.state == "RUNNING":
            start = (submitted + timedelta(seconds=job.submitted_s_ago / 2)).strftime(_TIME)
        elif job.starts_in_s is None:
            start = "N/A"
        else:
            start = (self.now + timedelta(seconds=job.starts_in_s)).strftime(_TIME)
        return (
            f"{job.job_id}|{job.name}|{job.state}|{job.partition}|{job.priority}|"
            f"{submitted.strftime(_TIME)}|{start}"
        )


def _option(words: list[str], flag: str) -> str:
    return words[words.index(flag) + 1] if flag in words else ""
