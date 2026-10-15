"""What a run can see of the queue its jobs wait in, in terms no backend owns.

On a shared cluster a trusted evaluation, or an agent's GPU command, may wait hours before it
runs. ``QueueStatus`` is what the run's agents and its strategy may know of that wait: the
run's own jobs (queued or running, how long each waited, its place in line and when it is
expected to start, when the backend can tell) and how busy each pool of the backend is (jobs
pending and running, GPUs in total and in use). A field the backend cannot tell is None.

``QueueStatusReader`` is the role that reads it. It is read-only by construction: it has no
method that changes anything, and an implementation reports other users' work only as counts.
Implementations live with their backends; every one passes the contract suite in
``vs_evaluation.api.testing``.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Protocol

from pydantic import BaseModel, ConfigDict, Field

_Seconds = Annotated[float, Field(ge=0)]
_Count = Annotated[int, Field(ge=0)]


class QueueItemKind(StrEnum):
    """What one of the run's jobs is for."""

    EVALUATION = "evaluation"
    """A trusted evaluation of a candidate (or of the input)."""
    AGENT_GPU_JOB = "agent-gpu-job"
    """A GPU command an agent ran itself (profiling, a test)."""


class QueueItemState(StrEnum):
    """Where one of the run's jobs is: waiting for resources, or running on them."""

    QUEUED = "queued"
    RUNNING = "running"


class QueueItem(BaseModel):
    """One of the run's own jobs that is queued or running."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    job: str = Field(description="The backend's id of the job.")
    kind: QueueItemKind
    state: QueueItemState
    pool: str | None = Field(
        default=None, description="The pool (a Slurm partition) the job waits or runs in."
    )
    waited_s: _Seconds | None = Field(
        default=None,
        description="Seconds the job waited in line: until it started, or until now if queued.",
    )
    position: Annotated[int, Field(ge=1)] | None = Field(
        default=None, description="A queued job's place in its pool's line; 1 starts next."
    )
    starts_in_s: _Seconds | None = Field(
        default=None, description="Seconds until the backend expects a queued job to start."
    )


class QueueLoad(BaseModel):
    """How busy one pool of the backend is, over every user's work."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    pool: str
    pending: _Count | None = Field(default=None, description="Jobs waiting in the pool.")
    running: _Count | None = Field(default=None, description="Jobs running in the pool.")
    gpus_total: _Count | None = None
    gpus_busy: _Count | None = Field(default=None, description="GPUs the pool's running jobs hold.")


class QueueStatus(BaseModel):
    """The run's own queued and running jobs, and the load of the pools they use."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    readable: bool = Field(
        description="False when the backend could not be asked; nothing else is then known."
    )
    items: tuple[QueueItem, ...] = ()
    loads: tuple[QueueLoad, ...] = ()

    @classmethod
    def unreadable(cls) -> QueueStatus:
        """The status of a backend that could not be asked."""
        return cls(readable=False)


class QueueStatusReader(Protocol):
    """Reads the queue status of the run's backend; it changes nothing."""

    def read(self) -> QueueStatus:
        """What the backend says now; never raises for a backend that cannot be asked."""
        ...
