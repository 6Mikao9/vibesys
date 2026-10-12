"""Why a serial round ended, as typed data.

A round that ends without a measured candidate says why with a `Failure`: a closed kind and
only the facts that kind has. The only free text a failure holds is what an evaluator printed.
A template or `describe` words it; the search never writes the sentence.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import model_validator

from vs_core.api.search import (
    CheckpointDecline,
    Failed,
    FailureReason,
    ObservationStatus,
    RejectionCode,
    TurnFailureKind,
    Value,
    WaitRefusal,
)


class Role(StrEnum):
    """Agent roles; values match the `<search>-<role>` role ids."""

    PLANNER = "orchestrator"
    IMPLEMENTER = "implementer"
    JUDGE = "judge"
    PROFILER = "profiler"
    PREROUND = "preround"


class FailureKind(StrEnum):
    """Why a round ended without a measured candidate. Closed."""

    STOPPED = "stopped"
    """The operator stopped the run."""
    REFUSED = "refused"
    """Core refused a decision of the round; ``cause`` says why."""
    ENDED = "ended"
    """A wait of the round ended without a result; ``ended`` says why."""
    OUT_OF_BUDGET = "out-of-budget"
    TURN_FAILED = "turn-failed"
    """An agent's turn ended without a reply; ``role`` and ``turn_failure`` say how."""
    INVALID_REPLY = "invalid-reply"
    RENDER_FAILED = "render-failed"
    WAIT_REFUSED = "wait-refused"
    """An agent asked to wait for evaluations and the run could not honor it."""
    CHECKPOINT_DECLINED = "checkpoint-declined"
    NO_CHANGED_CANDIDATE = "no-changed-candidate"
    EVIDENCE_REFUSED = "evidence-refused"
    EVIDENCE_UNDECODABLE = "evidence-undecodable"


_FIELDS: dict[FailureKind, tuple[frozenset[str], frozenset[str]]] = {
    FailureKind.REFUSED: (frozenset({"cause"}), frozenset()),
    FailureKind.ENDED: (frozenset({"ended"}), frozenset({"role"})),
    FailureKind.TURN_FAILED: (frozenset({"role"}), frozenset({"turn_failure", "ended"})),
    FailureKind.INVALID_REPLY: (frozenset({"role"}), frozenset()),
    FailureKind.RENDER_FAILED: (frozenset({"role"}), frozenset()),
    FailureKind.WAIT_REFUSED: (frozenset({"role", "refusal"}), frozenset()),
    FailureKind.CHECKPOINT_DECLINED: (frozenset({"declined"}), frozenset()),
}


class Failure(Value):
    """A typed account of why a round ended unmeasured, with only the fields its kind has."""

    kind: FailureKind
    role: Role | None = None
    cause: RejectionCode | None = None
    ended: FailureReason | None = None
    turn_failure: TurnFailureKind | None = None
    refusal: WaitRefusal | None = None
    declined: CheckpointDecline | None = None

    @model_validator(mode="after")
    def fields_match_kind(self) -> Failure:
        """Refuse a field that does not belong to the kind, or a kind missing its field."""
        required, optional = _FIELDS.get(self.kind, (frozenset(), frozenset()))
        present = {
            name
            for name in ("role", "cause", "ended", "turn_failure", "refusal", "declined")
            if getattr(self, name) is not None
        }
        if missing := required - present:
            message = f"{self.kind.value} needs {sorted(missing)[0]}"
            raise ValueError(message)
        if extra := present - required - optional:
            message = f"{sorted(extra)[0]} does not belong to {self.kind.value}"
            raise ValueError(message)
        return self


class Unmeasured(Value):
    """The machinery could not measure a candidate, which says nothing against the candidate.

    ``output`` is what the evaluator printed, kept as captured data.
    """

    status: ObservationStatus
    output: str = ""


def failure_of(event: Failed, role: Role | None) -> Failure:  # noqa: PLR0911  # LW-220501 [PLR0911]; one return per wait-failure reason keeps the mapping to the typed failure exhaustive and visible.
    """The typed account of a wait that ended without a result."""
    match event.reason:
        case FailureReason.REFUSED:
            return Failure(kind=FailureKind.REFUSED, cause=event.cause)
        case FailureReason.OUT_OF_BUDGET:
            return Failure(kind=FailureKind.OUT_OF_BUDGET)
        case FailureReason.INVALID_REPLY:
            return Failure(kind=FailureKind.INVALID_REPLY, role=role)
        case FailureReason.RENDER_FAILED:
            return Failure(kind=FailureKind.RENDER_FAILED, role=role)
        case FailureReason.WAIT_REFUSED:
            return Failure(kind=FailureKind.WAIT_REFUSED, role=role, refusal=event.refusal)
        case FailureReason.TURN_FAILED:
            return Failure(kind=FailureKind.TURN_FAILED, role=role, turn_failure=event.turn_failure)
        case (
            FailureReason.TIMED_OUT
            | FailureReason.WORKSPACE_CLOSED
            | FailureReason.OWNER_GONE
            | FailureReason.LOST
            | FailureReason.MEASUREMENT_UNTRUSTED
        ):
            return Failure(kind=FailureKind.ENDED, ended=event.reason, role=role)
