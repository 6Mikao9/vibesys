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

from typing import Literal

from pydantic import Field, model_validator

from vs_core._generated.vibesys.core.v1.leaves_cv import (
    FromDispatch,
    Inherited,
    Limit,
    Until,
    Watch,
    WhileAlive,
    WhileOpen,
    Within,
)
from vs_core._generated.vibesys.core.v1.vocabulary_cv import (
    FailureReason,
    ReplyErrorCode,
    WaitRefusal,
)

from .common import (
    DecisionId,
    InvocationRef,
    RejectionCode,
    Seconds,
    TurnFailureKind,
    Value,
)

REPLY_ERROR_MESSAGE_LIMIT = 500
"""Characters kept of one validator message, so an unbounded reply cannot grow the state."""


class ReplyError(Value):
    """One typed problem in a reply: where it is and what is wrong.

    ``message`` is the validator's own wording when it reported one (a provider's schema
    check does); the reply model's check leaves it empty and ``code`` says what is wrong.
    """

    path: tuple[str | int, ...]
    code: ReplyErrorCode
    message: str = Field(default="", max_length=REPLY_ERROR_MESSAGE_LIMIT)


_TURN_REASONS = frozenset(
    {
        FailureReason.INVALID_REPLY,
        FailureReason.LOST,
        FailureReason.TURN_FAILED,
        FailureReason.TIMED_OUT,
        FailureReason.WAIT_REFUSED,
    }
)


class Failed(Value):
    """A request ended without a result. Every field is typed; nothing is free text.

    Each reason carries its own fields, and only those: ``TIMED_OUT`` and
    ``QUEUE_BUDGET_SPENT`` the ``limit_at`` (the run-clock time of the bound that passed), ``WORKSPACE_CLOSED`` / ``OWNER_GONE`` the
    ``watch`` that fired, ``REFUSED`` the ``cause`` core gave, ``INVALID_REPLY`` the reply
    ``errors``.

    ``decision`` names the search's own decision that ended; it is None for a request core
    made for itself, and core keeps such a failure to itself. A failure never names a request
    id: requests are core's, and a search knows only its decisions.
    """

    kind: Literal["failed"] = "failed"
    reason: FailureReason
    decision: DecisionId | None = None
    watch: Watch | None = None
    limit_at: Seconds | None = None
    cause: RejectionCode | None = None
    errors: tuple[ReplyError, ...] = ()
    turn: InvocationRef | None = None
    """The turn that failed, for the reasons that end one (and for a lost turn)."""
    turn_failure: TurnFailureKind | None = None
    digest: str | None = None
    refusal: WaitRefusal | None = None

    @model_validator(mode="after")
    def fields_match_reason(self) -> Failed:
        """Refuse a field that does not belong to the reason, or a reason missing its field."""
        expected = {
            "watch": self.reason in (FailureReason.WORKSPACE_CLOSED, FailureReason.OWNER_GONE),
            "limit_at": self.reason in (FailureReason.TIMED_OUT, FailureReason.QUEUE_BUDGET_SPENT),
            "cause": self.reason == FailureReason.REFUSED,
            "errors": self.reason == FailureReason.INVALID_REPLY,
            "turn_failure": self.reason == FailureReason.TURN_FAILED,
            "refusal": self.reason == FailureReason.WAIT_REFUSED,
        }
        if self.turn is not None and self.reason not in _TURN_REASONS:
            message = f"turn does not belong to reason {self.reason.value}"
            raise ValueError(message)
        if self.digest is not None and self.reason != FailureReason.TURN_FAILED:
            message = f"digest does not belong to reason {self.reason.value}"
            raise ValueError(message)
        for name, allowed in expected.items():
            present = (
                bool(getattr(self, name)) if name == "errors" else getattr(self, name) is not None
            )
            if present and not allowed:
                message = f"{name} does not belong to reason {self.reason.value}"
                raise ValueError(message)
            if allowed and not present:
                message = f"reason {self.reason.value} needs {name}"
                raise ValueError(message)
        return self


__all__ = [
    "Failed",
    "FailureReason",
    "FromDispatch",
    "Inherited",
    "Limit",
    "ReplyError",
    "ReplyErrorCode",
    "Until",
    "WaitRefusal",
    "Watch",
    "WhileAlive",
    "WhileOpen",
    "Within",
]
