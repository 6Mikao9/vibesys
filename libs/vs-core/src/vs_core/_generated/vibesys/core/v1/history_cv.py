# Generated from vibesys/core/v1/history.proto by protoc-gen-corevalue. DO NOT EDIT.
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
from vs_core._generated.vibesys.core.v1.common_cv import RequestId, Scope
from vs_core._generated.vibesys.core.v1.leaves_cv import (
    BenchmarkFailure,
    EvaluationHistoryCursor,
    EvaluationStageResult,
    Observation,
)
from vs_core._generated.vibesys.core.v1.vocabulary_cv import (
    AttemptTerminalReason,
    EvaluationHistoryAvailability,
)
from vs_core._history_behavior import HistoryCursor, TerminalFacts


@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(
        extra="forbid",
        strict=True,
        validate_default=True,
        revalidate_instances="subclass-instances",
    ),
)
class EvaluationTerminalFacts(TerminalFacts, CoreValue):
    "Owner-normalized scientific facts from one conclusive terminal observation."

    model_fields: ClassVar[tuple[str, ...]] = (
        "stages",
        "traceback_signature",
        "failed_benchmark",
        "accuracy_passed",
    )
    stages: tuple[EvaluationStageResult, ...] = ()
    traceback_signature: Annotated[str | None, Field(min_length=1)] = None
    failed_benchmark: BenchmarkFailure | None = None
    accuracy_passed: bool = False

    def __post_init__(self) -> None:
        """Enforce the proto message rules at construction as well as decode."""
        if not (len(set(p.stage_id for p in self.stages)) == len(self.stages)):
            raise ValueError("stages: duplicate stage ID")

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class AttemptEvaluationRecord(EvaluationTerminalFacts):
    "An ordered submission and the unchanged observation establishing its terminal facts."

    model_fields: ClassVar[tuple[str, ...]] = (
        "stages",
        "traceback_signature",
        "failed_benchmark",
        "accuracy_passed",
        "ordinal",
        "submission_id",
        "scope",
        "terminal_observation",
    )
    stages: tuple[EvaluationStageResult, ...] = ()
    traceback_signature: Annotated[str | None, Field(min_length=1)] = None
    failed_benchmark: BenchmarkFailure | None = None
    accuracy_passed: bool = False
    ordinal: Annotated[int, Field(ge=1)]
    submission_id: RequestId
    scope: Scope
    terminal_observation: Observation

    def __post_init__(self) -> None:
        """Enforce the proto message rules at construction as well as decode."""
        if not (len(set(p.stage_id for p in self.stages)) == len(self.stages)):
            raise ValueError("stages: duplicate stage ID")
        if not (
            self.terminal_observation.request_id == self.submission_id
            and self.terminal_observation.scope == self.scope
        ):
            raise ValueError("terminal_observation: submission/scope mismatch")
        if not (
            self.terminal_observation.terminal
            and {
                "succeeded": 1,
                "pending": 2,
                "cancelled": 3,
                "rejected": 4,
                "failed": 5,
                "unknown": 6,
            }.get(self.terminal_observation.status, 0)
            != 2
            and {
                "succeeded": 1,
                "pending": 2,
                "cancelled": 3,
                "rejected": 4,
                "failed": 5,
                "unknown": 6,
            }.get(self.terminal_observation.status, 0)
            != 6
        ):
            raise ValueError("terminal_observation: requires conclusive terminal fact")
        if not (
            self.terminal_observation.accepted
            or (
                (
                    {
                        "succeeded": 1,
                        "pending": 2,
                        "cancelled": 3,
                        "rejected": 4,
                        "failed": 5,
                        "unknown": 6,
                    }.get(self.terminal_observation.status, 0)
                    == 3
                    or {
                        "succeeded": 1,
                        "pending": 2,
                        "cancelled": 3,
                        "rejected": 4,
                        "failed": 5,
                        "unknown": 6,
                    }.get(self.terminal_observation.status, 0)
                    == 4
                    or {
                        "succeeded": 1,
                        "pending": 2,
                        "cancelled": 3,
                        "rejected": 4,
                        "failed": 5,
                        "unknown": 6,
                    }.get(self.terminal_observation.status, 0)
                    == 5
                )
                and len(self.stages) == 0
                and not (self.traceback_signature is not None)
                and not (self.failed_benchmark is not None)
                and not self.accuracy_passed
            )
        ):
            raise ValueError(
                "terminal_observation: nonacceptance cannot prove scientific execution facts"
            )

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class AttemptEvaluationHistory(HistoryCursor, CoreValue):
    "Ordered durable records with the exact prepared-submission coverage manifest."

    model_fields: ClassVar[tuple[str, ...]] = ("availability", "covered_submissions", "records")
    availability: EvaluationHistoryAvailability = EvaluationHistoryAvailability("unavailable")
    covered_submissions: tuple[RequestId, ...] = ()
    records: tuple[AttemptEvaluationRecord, ...] = ()

    def __post_init__(self) -> None:
        """Enforce the proto message rules at construction as well as decode."""
        if not (len(set(p.submission_id.root for p in self.records)) == len(self.records)):
            raise ValueError("records: duplicate submission ID")
        if not (
            len(set(p.root for p in self.covered_submissions)) == len(self.covered_submissions)
        ):
            raise ValueError("covered_submissions: duplicate submission ID")
        if not (
            all(
                (
                    len(str(r.ordinal)) < len(str(len(self.covered_submissions)))
                    or (
                        len(str(r.ordinal)) == len(str(len(self.covered_submissions)))
                        and str(r.ordinal) <= str(len(self.covered_submissions))
                    )
                )
                for r in self.records
            )
            and all(
                (self.covered_submissions[cel_integer(r.ordinal) - 1] == r.submission_id)
                for r in self.records
            )
        ):
            raise ValueError("records: submission differs from coverage ordinal")
        if not (
            tuple((r.submission_id) for r in self.records)
            == tuple(
                s
                for s in self.covered_submissions
                if (any((r.submission_id == s) for r in self.records))
            )
        ):
            raise ValueError("records: ordinals must be unique and increasing")
        if not (
            {"complete": 1, "unavailable": 2}.get(self.availability, 0) != 1
            or tuple((r.submission_id) for r in self.records) == self.covered_submissions
        ):
            raise ValueError("covered_submissions: complete history differs from records")

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class RepeatedFailureGuidance(CoreValue):
    "Durable failure evidence passed to a continuation prompt; no dispatch authority."

    model_fields: ClassVar[tuple[str, ...]] = (
        "reason",
        "consecutive_failures",
        "limit",
        "cursor",
        "traceback_signature",
        "failed_benchmark",
    )
    reason: Literal[
        AttemptTerminalReason.REPEATED_TRACEBACK, AttemptTerminalReason.REPEATED_MEASUREMENT
    ]
    consecutive_failures: Annotated[int, Field(ge=1)]
    limit: Annotated[int, Field(ge=1)]
    cursor: EvaluationHistoryCursor
    traceback_signature: Annotated[str | None, Field(min_length=1)] = None
    failed_benchmark: BenchmarkFailure | None = None

    def __post_init__(self) -> None:
        """Enforce the proto message rules at construction as well as decode."""
        if not (
            len(str(self.consecutive_failures)) < len(str(self.cursor.ordinal))
            or (
                len(str(self.consecutive_failures)) == len(str(self.cursor.ordinal))
                and str(self.consecutive_failures) <= str(self.cursor.ordinal)
            )
        ):
            raise ValueError("consecutive_failures: exceeds preceding submissions")
        if not (
            {
                "repeated-traceback": 1,
                "repeated-measurement": 2,
                "history-unavailable": 3,
                "no-new-evaluation": 4,
            }.get(self.reason, 0)
            != 1
            or (self.traceback_signature is not None)
        ):
            raise ValueError("traceback_signature: required for repeated traceback")
        if not (
            {
                "repeated-traceback": 1,
                "repeated-measurement": 2,
                "history-unavailable": 3,
                "no-new-evaluation": 4,
            }.get(self.reason, 0)
            != 2
            or (self.failed_benchmark is not None)
        ):
            raise ValueError("failed_benchmark: required for repeated measurement")

    __hash__ = CoreValue.cached_hash


resolve_value(EvaluationTerminalFacts)
resolve_value(AttemptEvaluationRecord)
resolve_value(AttemptEvaluationHistory)
resolve_value(RepeatedFailureGuidance)
