"""Whether the executor of a request can outlive the host instance that dispatched it.

Recovery asks the executors what became of the requests a dead host left open. That inquiry has
an answer only when what the request did is still there to be read: a workspace and the git
history behind it, a job in a cluster or container, a sealed receipt. An agent session is not
like that. Its provider process, its open turn and the session handle are owned by the host
instance that started them; when that instance dies (a stop, a crash, a restart) they die with
it, and nothing is left that could say what the turn did. A restarted executor can only report
that the turn began and has no recorded result, and it can say that for ever.

Core therefore declares, once and for every request kind, which of the two a request is:

* ``DURABLE``: the effect outlives the host (files, git, a remote job, a durable receipt).
  Recovery keeps asking its executor, and an effect it cannot account for stays unknown.
* ``INSTANCE``: the executor is the host instance itself. Once that instance is replaced the
  request holds nothing a new executor could find or release, so its outcome follows from the
  fact that the instance is gone (`_recovery_unanswered`), not from an answer nobody can give.

The shell makes the premise true: a resumed run holds its exclusive host lock and ends the
agents the dead host left (`RunEnvironment.reap_orphans`) before the new host dispatches
anything. The classification is a property of the request kind, never of a backend, and it is
exhaustive: a new request kind does not type-check until it is classified here.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, assert_never

from .types.attempts import (
    CloseAttemptScope,
    DiscardWorkspace,
    EnsureWorkspace,
    RestoreRevision,
    RetainRevision,
    SnapshotAndRetain,
)
from .types.common import ExecuteRegisteredOperation, LifecycleClass
from .types.evaluation import (
    CancelOwnedJob,
    CollectEvidence,
    InspectOwnedJob,
    ObserveOwnedJob,
    SubmitMeasurement,
)
from .types.records import BlockRequest, CancelOwnedResource, InspectRequest
from .types.sessions import (
    CancelTurn,
    CloseSession,
    DispatchTurn,
    EnsureSession,
    InspectTurn,
    ResumeSessionTurn,
    SnapshotAndRetainRun,
)
from .types.settlement import AdoptRevision, VerifyAdoption

if TYPE_CHECKING:
    from .types.records import Request


class ExecutorLifetime(StrEnum):
    """How long the executor of a request lives, relative to the host that dispatched it."""

    INSTANCE = "instance"
    """It is the dispatching host instance, and dies with it (an agent session and its turns)."""
    DURABLE = "durable"
    """What it did outlives the host: durable files, git, jobs and receipts."""


def executor_lifetime(request: Request) -> ExecutorLifetime:
    """The lifetime of the executor of *request*, from its kind alone."""
    match request:
        case (
            EnsureSession()
            | DispatchTurn()
            | ResumeSessionTurn()
            | InspectTurn()
            | CancelTurn()
            | CloseSession()
        ):
            return ExecutorLifetime.INSTANCE
        case ExecuteRegisteredOperation():
            # A registered operation is durable unless it is an agent turn run as one.
            return (
                ExecutorLifetime.INSTANCE
                if request.operation.schema_ref.lifecycle == LifecycleClass.SESSION_TURN
                else ExecutorLifetime.DURABLE
            )
        case (
            EnsureWorkspace()
            | RestoreRevision()
            | SnapshotAndRetain()
            | SnapshotAndRetainRun()
            | RetainRevision()
            | DiscardWorkspace()
            | AdoptRevision()
            | VerifyAdoption()
            | SubmitMeasurement()
            | ObserveOwnedJob()
            | InspectOwnedJob()
            | CancelOwnedJob()
            | CollectEvidence()
            | CloseAttemptScope()
            | InspectRequest()
            | CancelOwnedResource()
            | BlockRequest()
        ):
            return ExecutorLifetime.DURABLE
        case _:
            assert_never(request)


__all__ = ["ExecutorLifetime", "executor_lifetime"]
