"""The plain values the multi search keeps and passes around, with no behavior of their own.

Every value is a strict, frozen `Value` with closed fields. Reasons are enums with the typed
facts that go with them; the only free text is text an agent or an evaluator wrote (a judge's
feedback, what a failed check printed), carried as data for a later prompt and never written by
the search.
"""

from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from vibesys.serial_search.api import Failure, NoReply
from vs_core.api.search import EvidenceKind, Value

NOTE_LIMIT = 20
"""How many advisory notes the state keeps for an operator to read (the newest)."""

DOMINATED = "the live Pareto archive dominates the claimed frontier row"


class UnreachableReason(StrEnum):
    """Typed reason a decision the search wants cannot be made."""

    RESUME_CAPABILITY_NOT_OFFERED = "resume-capability-not-offered"
    VALIDATE_LOCAL_NOT_OFFERED = "validate-local-not-offered"


class Unreachable(Value):
    """A decision not made, with the missing core contract named."""

    reason: UnreachableReason
    subject: str


class NoteKind(StrEnum):
    """What an advisory note says did not happen. Closed."""

    PRE_ROUND_SKIPPED = "pre-round-skipped"
    """The routing turn gave no answer; the plan went ahead without a profile decision."""
    PROFILE_SKIPPED = "profile-skipped"
    """The profiling turn gave no answer; the plan went ahead without a fresh profile."""
    JUDGE_LOST = "judge-lost"
    """The review gave no verdict; the round settled unreviewed."""
    VALIDATION_UNRUN = "validation-unrun"
    """The validation recipes could not be run; the candidate was approved on the review."""


class Note(Value):
    """One advisory failure, kept for an operator to read; it changes no decision."""

    kind: NoteKind
    failure: Failure | None = None
    no_reply: NoReply | None = None
    detail: str = ""
    """What an evaluator printed, as captured."""


class ConflictMetric(Value):
    """One objective value of a point on the live archive."""

    name: str
    value: float
    direction: Literal["max", "min"]


class Dominator(Value):
    """A trusted archive point that dominates a claimed frontier row."""

    round_number: int = Field(gt=0)
    metrics: tuple[ConflictMetric, ...] = ()


class ArchiveConflictView(Value):
    """A `pareto_frontier` claim that the live archive dominates."""

    dominators: tuple[Dominator, ...] = Field(min_length=1)


class ValidationFailure(Value):
    """How the trusted run of the implementer's validation recipes ended, when it did not pass."""

    recipe: str = ""
    error: str = ""
    output: str = ""
    recipe_unusable: bool = False


class RepairKind(StrEnum):
    """What the implementer is asked to repair on its next turn. Closed."""

    REVIEW = "review"
    """The judge failed the candidate; ``text`` is the feedback it wrote."""
    ARCHIVE = "archive"
    """The judge passed it but the live archive dominates the claimed frontier row."""
    GATE = "gate"
    """A trusted gate rejected the candidate; ``gate`` names it, ``text`` is what it printed."""
    VALIDATION = "validation"
    """The validation recipes the implementer wrote failed or could not be used."""
    CARRIED = "carried"
    """The previous round of the hypothesis ended with this feedback in the ledger."""


class Repair(Value):
    """The failure of the last attempt, which the next attempt is told to repair.

    ``text`` is agent- or evaluator-authored; when it is empty the template says only what failed.
    """

    kind: RepairKind
    text: str = ""
    gate: EvidenceKind | None = None
    conflict: ArchiveConflictView | None = None
    validation: ValidationFailure | None = None

    @model_validator(mode="after")
    def facts_belong_to_kind(self) -> "Repair":
        """Only a kind that has a fact carries it."""
        has = {
            RepairKind.GATE: self.gate is not None,
            RepairKind.ARCHIVE: self.conflict is not None,
            RepairKind.VALIDATION: self.validation is not None,
        }
        for kind, present in has.items():
            if present != (self.kind is kind):
                message = f"a {kind.value} repair has its {kind.value} fact, and no other has it"
                raise ValueError(message)
        return self

    @property
    def sentence(self) -> str:
        """What the ledger records of the failed attempt: the author's text, else what failed."""
        if self.kind is RepairKind.VALIDATION and self.validation is not None:
            return (
                "validation recipe unusable"
                if self.validation.recipe_unusable
                else "local validation failed"
            )
        if self.text:
            return self.text
        match self.kind:
            case RepairKind.GATE if self.gate is not None:
                return f"{self.gate.value} gate failed"
            case RepairKind.ARCHIVE:
                return DOMINATED
            case RepairKind.REVIEW:
                return "review failed"
            case _:
                return ""
