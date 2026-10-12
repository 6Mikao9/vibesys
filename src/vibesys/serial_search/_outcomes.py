"""Plain values every serial search keeps about its rounds, with no behavior of their own."""

from enum import StrEnum

from pydantic import Field

from vibesys.metrics import MetricRow
from vs_core.api.search import (
    FailureReason,
    ReplyError,
    RevisionRef,
    SettlementId,
    TurnFailureKind,
    Value,
)


class NoReplyKind(StrEnum):
    """How an implementer's last turn ended without a usable reply. Closed."""

    INVALID_REPLY = "invalid-reply"
    """The reply broke its schema and no correction was left; ``errors`` say where."""
    NO_REPLY = "no-reply"
    """The turn ended without a reply; ``ended`` or ``turn_failure`` say how."""


class NoReply(Value):
    """An attempt that produced no valid response, which the next attempt is told about."""

    kind: NoReplyKind
    errors: tuple[ReplyError, ...] = ()
    ended: FailureReason | None = None
    turn_failure: TurnFailureKind | None = None


class RoundOutcome(Value):
    """What core settled for one closed round, kept for winner choice."""

    round_number: int = Field(gt=0)
    hypothesis_id: str
    settlement: SettlementId
    candidate: RevisionRef | None = None
    eligible: bool = False
    metrics: tuple[MetricRow, ...] = ()
