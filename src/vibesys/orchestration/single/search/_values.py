"""The plain values the single search keeps and passes around, with no behavior of their own.

Every value is a strict, frozen `Value` with closed fields. Reasons are enums with the typed
facts that go with them; the only free text is text an agent or an evaluator wrote (a
self-review's feedback, what a failed check printed), carried as data for a later prompt and
never written by the search.
"""

from enum import StrEnum

from pydantic import Field, model_validator

from vibesys.metrics import MetricRow
from vs_core.api.search import (
    EvidenceKind,
    FailureReason,
    ReplyError,
    RevisionRef,
    SettlementId,
    TurnFailureKind,
    Value,
)


class UnreachableReason(StrEnum):
    """Typed reason a decision the search wants cannot be made."""

    RESUME_CAPABILITY_NOT_OFFERED = "resume-capability-not-offered"


class Unreachable(Value):
    """A decision not made, with the missing core contract named."""

    reason: UnreachableReason
    subject: str


class RepairKind(StrEnum):
    """What the implementer is asked to repair on its next turn. Closed."""

    SELF_REVIEW = "self-review"
    """Its own review failed the candidate; ``text`` is the feedback it wrote."""
    GATE = "gate"
    """A trusted gate rejected the candidate; ``gate`` names it, ``text`` is what it printed."""
    CARRIED = "carried"
    """The previous round of the hypothesis ended with this feedback in the ledger."""


class Repair(Value):
    """The failure of the last attempt, which the next attempt is told to repair.

    ``text`` is agent- or evaluator-authored; when it is empty the template says only what failed.
    """

    kind: RepairKind
    text: str = ""
    gate: EvidenceKind | None = None

    @model_validator(mode="after")
    def gate_belongs_to_gates(self) -> "Repair":
        """Only a gate repair names a gate."""
        if (self.gate is not None) != (self.kind is RepairKind.GATE):
            message = "a repair names a gate exactly when a gate rejected the candidate"
            raise ValueError(message)
        return self

    @property
    def sentence(self) -> str:
        """What the ledger records of the failed attempt: the author's text, else what failed."""
        if self.text:
            return self.text
        if self.kind is RepairKind.GATE and self.gate is not None:
            return f"{self.gate.value} gate failed"
        return "self-review failed"


class NoReplyKind(StrEnum):
    """How the implementer's last turn ended without a usable reply. Closed."""

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
