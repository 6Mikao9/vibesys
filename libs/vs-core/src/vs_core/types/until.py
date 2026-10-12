"""How long a request may take, what else ends it, and the typed failure that reports it.

A request carries no deadline. The one who asks for work states, next to the request,
``Within(limit)`` (a duration on the run clock) extended with the conditions that make the
work pointless before the limit: ``Within(60) | WhileOpen(workspace) | WhileAlive(job)``.
Core stores the accepted form (``Until``, an absolute time) on the request record. When the
limit passes or a watch fires, the record ends and its owner receives ``Failed(reason)``
with ``TIMED_OUT``, ``WORKSPACE_CLOSED`` or ``OWNER_GONE``.

A watch is not a bound: ``WhileOpen(ws)`` alone is not a valid ``until``, only ``Within``
is, so every wait has a limit.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field

from .common import (
    AttemptRef,
    DecisionId,
    RejectionCode,
    RequestId,
    ResourceId,
    Seconds,
    Value,
)


class FailureReason(StrEnum):
    """Why a requested piece of work ended without a result. Closed: no free-text reasons."""

    TIMED_OUT = "timed-out"
    """The limit of its ``Within`` passed."""

    WORKSPACE_CLOSED = "workspace-closed"
    """A ``WhileOpen`` watch fired: the workspace it names is no longer open."""

    OWNER_GONE = "owner-gone"
    """A ``WhileAlive`` watch fired: the job it names is no longer alive."""

    REFUSED = "refused"
    """Core rejected the decision; ``Failed.rejection`` says why."""

    LOST = "lost"
    """Reconciliation could not prove what happened to the request."""

    INVALID_REPLY = "invalid-reply"
    OUT_OF_BUDGET = "out-of-budget"
    MEASUREMENT_UNTRUSTED = "measurement-untrusted"


class WhileOpen(Value):
    """End the wait with ``WORKSPACE_CLOSED`` when this workspace stops being open."""

    kind: Literal["while_open"] = "while_open"
    workspace: AttemptRef


class WhileAlive(Value):
    """End the wait with ``OWNER_GONE`` when this job is no longer alive."""

    kind: Literal["while_alive"] = "while_alive"
    job: ResourceId


type Watch = Annotated[WhileOpen | WhileAlive, Field(discriminator="kind")]

type Limit = Annotated[float, Field(allow_inf_nan=False)]


class Within(Value):
    """A duration on the run clock, plus the watches that may end the wait earlier.

    ``limit`` is relative to the time core accepts the wait. A limit of zero or less is
    refused by core (``Failed(REFUSED)``); the type does not forbid it so that a bypassed
    value still reaches the runtime check instead of failing on construction.
    """

    limit: Limit
    watches: tuple[Watch, ...] = ()

    def __or__(self, watch: WhileOpen | WhileAlive) -> Within:
        """Extend with one more watch: ``Within(60) | WhileOpen(workspace)``."""
        return self.model_copy(update={"watches": (*self.watches, watch)})

    def __ror__(self, watch: WhileOpen | WhileAlive) -> Within:
        """Allow ``WhileOpen(workspace) | Within(60)`` as well."""
        return self.model_copy(update={"watches": (watch, *self.watches)})


class Inherited(Value):
    """Internal: this request ends when the decision that caused it ends (same ``Until``)."""

    kind: Literal["inherited"] = "inherited"


class Until(Value):
    """What core stored when it accepted a ``Within``: an absolute time and the watches."""

    at: Seconds
    watches: tuple[Watch, ...] = ()


class Failed(Value):
    """A request ended without a result. Every field is typed; nothing is free text."""

    kind: Literal["failed"] = "failed"
    reason: FailureReason
    decision: DecisionId | None = None
    request: RequestId | None = None
    watch: Watch | None = None
    """The watch that fired, for ``WORKSPACE_CLOSED`` and ``OWNER_GONE``."""
    limit: Limit | None = None
    """The limit that passed, for ``TIMED_OUT``."""
    rejection: RejectionCode | None = None
    """Why core refused the decision, for ``REFUSED``."""
