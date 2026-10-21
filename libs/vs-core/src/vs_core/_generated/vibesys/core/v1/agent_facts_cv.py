# Generated from vibesys/core/v1/agent_facts.proto by protoc-gen-corevalue. DO NOT EDIT.
from __future__ import annotations

from enum import StrEnum
from functools import partial
from typing import Annotated, ClassVar, Literal, final

from pydantic import AfterValidator, ConfigDict, Field, field_serializer
from pydantic.dataclasses import dataclass

from vs_core._corevalue import (
    CoreValue,
    FrozenMap,
    TimestampValue,
    bounded_integer,
    cel_integer,
    omit_unset,
    resolve_value,
)
from vs_core._evidence_behavior import EvidenceIdentity
from vs_core._generated.vibesys.core.v1.common_cv import ArtifactRef, EvidenceId, RequestId, Scope
from vs_core._generated.vibesys.core.v1.failures_cv import ReplyError
from vs_core._generated.vibesys.core.v1.history_cv import RepeatedFailureGuidance
from vs_core._generated.vibesys.core.v1.leaves_cv import (
    Comparison,
    Observation,
    Reading,
    RevisionRef,
    WaitUnrecorded,
)
from vs_core._generated.vibesys.core.v1.vocabulary_cv import (
    EvidenceKind,
    ObservationStatus,
    WaitEnd,
)

EvidencePurpose = Literal["baseline", "local-validation", "official", "profile"]

EvidenceProvenance = Literal["trusted", "self-report"]


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class EvidenceAcceptanceReceipt(CoreValue):
    "Positive source acceptance, retained unchanged when evidence enters the ledger."

    model_fields: ClassVar[tuple[str, ...]] = ("observation",)
    observation: Observation

    def __post_init__(self) -> None:
        """Enforce the proto message rules at construction as well as decode."""
        if not (
            self.observation.accepted
            and {
                "succeeded": 1,
                "pending": 2,
                "cancelled": 3,
                "rejected": 4,
                "failed": 5,
                "unknown": 6,
            }.get(self.observation.status, 0)
            != 6
        ):
            raise ValueError("observation: positive source acceptance required")

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class EvidenceRef(EvidenceIdentity, CoreValue):
    "Evidence identity and its original source proof, independent of later job updates."

    model_fields: ClassVar[tuple[str, ...]] = (
        "evidence_id",
        "kind",
        "purpose",
        "scope",
        "source_request",
        "candidate",
        "observation_sequence",
        "evaluator_digest",
        "workload_digest",
        "environment_digest",
        "provenance",
        "status",
        "artifacts",
        "acceptance_receipt",
        "reused_from",
        "reading",
        "unmet_requirement",
    )
    evidence_id: EvidenceId
    kind: EvidenceKind
    purpose: EvidencePurpose
    scope: Scope
    source_request: RequestId
    candidate: RevisionRef
    observation_sequence: Annotated[int, Field(ge=0)]
    evaluator_digest: str
    workload_digest: str
    environment_digest: str
    provenance: EvidenceProvenance
    status: ObservationStatus
    artifacts: tuple[ArtifactRef, ...] = ()
    acceptance_receipt: EvidenceAcceptanceReceipt | None = None
    reused_from: Annotated[RequestId | None, Field(exclude_if=omit_unset)] = None
    reading: Annotated[Reading | None, Field(exclude_if=omit_unset)] = None
    unmet_requirement: Annotated[str | None, Field(exclude_if=omit_unset)] = None

    def __post_init__(self) -> None:
        """Enforce the proto message rules at construction as well as decode."""
        if not (
            not (self.acceptance_receipt is not None)
            or (
                self.acceptance_receipt.observation.request_id == self.source_request
                and self.acceptance_receipt.observation.scope == self.scope
                and str(self.acceptance_receipt.observation.sequence)
                == str(self.observation_sequence)
                and {
                    "succeeded": 1,
                    "pending": 2,
                    "cancelled": 3,
                    "rejected": 4,
                    "failed": 5,
                    "unknown": 6,
                }.get(self.acceptance_receipt.observation.status, 0)
                == {
                    "succeeded": 1,
                    "pending": 2,
                    "cancelled": 3,
                    "rejected": 4,
                    "failed": 5,
                    "unknown": 6,
                }.get(self.status, 0)
            )
        ):
            raise ValueError(
                "acceptance_receipt: source, scope, sequence and status must match evidence"
            )

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class ResumeFacts(CoreValue):
    "Data for continuing a suspended turn, without prompt text or dispatch authority."

    model_fields: ClassVar[tuple[str, ...]] = (
        "kind",
        "agent",
        "retained",
        "ended_by",
        "queue_budget_spent",
        "evidence",
        "repeated_failure",
        "comparisons",
        "settling",
    )
    kind: Literal["resume"] = "resume"
    agent: str
    retained: RevisionRef | None
    ended_by: WaitEnd | None
    queue_budget_spent: bool = False
    evidence: tuple[EvidenceRef, ...] = ()
    repeated_failure: RepeatedFailureGuidance | None = None
    comparisons: tuple[Comparison, ...] = ()
    settling: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Enforce the proto message rules at construction as well as decode."""
        if not (
            (
                {"limit": 1, "queue_budget": 2, "run_end": 3}.get(self.ended_by, 0) != 2
                or self.queue_budget_spent
            )
            and (
                {"limit": 1, "queue_budget": 2, "run_end": 3}.get(self.ended_by, 0) != 0
                or not self.queue_budget_spent
            )
        ):
            raise ValueError("queue_budget_spent: must agree with what ended the wait")
        if not (len(set(self.settling)) == len(self.settling)):
            raise ValueError("settling: names an input of no comparison, or twice")
        if not (
            all((any((c.candidate_input == s) for c in self.comparisons)) for s in self.settling)
        ):
            raise ValueError("settling: names an input of no comparison, or twice")

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class ReplyCorrection(CoreValue):
    "Immutable ReplyCorrection value."

    model_fields: ClassVar[tuple[str, ...]] = ("kind", "role", "errors")
    kind: Literal["reply-correction"] = "reply-correction"
    role: str
    errors: tuple[ReplyError, ...]

    __hash__ = CoreValue.cached_hash


type ProtocolContext = ReplyCorrection | ResumeFacts | WaitUnrecorded

resolve_value(EvidenceAcceptanceReceipt)
resolve_value(EvidenceRef)
resolve_value(ResumeFacts)
resolve_value(ReplyCorrection)
