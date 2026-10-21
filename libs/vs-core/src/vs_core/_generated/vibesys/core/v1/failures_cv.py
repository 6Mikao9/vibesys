# Generated from vibesys/core/v1/failures.proto by protoc-gen-corevalue. DO NOT EDIT.
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
from vs_core._generated.vibesys.core.v1.common_cv import DecisionId, InvocationRef
from vs_core._generated.vibesys.core.v1.leaves_cv import Watch
from vs_core._generated.vibesys.core.v1.vocabulary_cv import (
    FailureReason,
    RejectionCode,
    ReplyErrorCode,
    TurnFailureKind,
    WaitRefusal,
)

REPLY_ERROR_MESSAGE_LIMIT = 500

type ReplyPathPart = str | int


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class ReplyError(CoreValue):
    "Immutable ReplyError value."

    model_fields: ClassVar[tuple[str, ...]] = ("path", "code", "message")
    path: tuple[ReplyPathPart, ...]
    code: ReplyErrorCode
    message: Annotated[str, Field(max_length=500)] = ""

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class Failed(CoreValue):
    "A request ended without a result, carrying exactly the fields its reason allows."

    model_fields: ClassVar[tuple[str, ...]] = (
        "kind",
        "reason",
        "decision",
        "watch",
        "limit_at",
        "cause",
        "errors",
        "turn",
        "turn_failure",
        "digest",
        "refusal",
    )
    kind: Literal["failed"] = "failed"
    reason: FailureReason
    decision: DecisionId | None = None
    watch: Watch | None = None
    limit_at: Annotated[float | None, Field(ge=0.0, allow_inf_nan=False)] = None
    cause: RejectionCode | None = None
    errors: tuple[ReplyError, ...] = ()
    turn: InvocationRef | None = None
    turn_failure: TurnFailureKind | None = None
    digest: str | None = None
    refusal: WaitRefusal | None = None

    def __post_init__(self) -> None:
        """Enforce the proto message rules at construction as well as decode."""
        if not (
            (
                {
                    "timed-out": 1,
                    "workspace-closed": 2,
                    "owner-gone": 3,
                    "refused": 4,
                    "lost": 5,
                    "invalid-reply": 6,
                    "render-failed": 7,
                    "turn-failed": 8,
                    "wait-refused": 9,
                    "queue-budget-spent": 10,
                    "out-of-budget": 11,
                    "measurement-untrusted": 12,
                }.get(self.reason, 0)
                == 2
                or {
                    "timed-out": 1,
                    "workspace-closed": 2,
                    "owner-gone": 3,
                    "refused": 4,
                    "lost": 5,
                    "invalid-reply": 6,
                    "render-failed": 7,
                    "turn-failed": 8,
                    "wait-refused": 9,
                    "queue-budget-spent": 10,
                    "out-of-budget": 11,
                    "measurement-untrusted": 12,
                }.get(self.reason, 0)
                == 3
            )
            == (self.watch is not None)
        ):
            raise ValueError("watch: must match reason")
        if not (
            (
                {
                    "timed-out": 1,
                    "workspace-closed": 2,
                    "owner-gone": 3,
                    "refused": 4,
                    "lost": 5,
                    "invalid-reply": 6,
                    "render-failed": 7,
                    "turn-failed": 8,
                    "wait-refused": 9,
                    "queue-budget-spent": 10,
                    "out-of-budget": 11,
                    "measurement-untrusted": 12,
                }.get(self.reason, 0)
                == 1
                or {
                    "timed-out": 1,
                    "workspace-closed": 2,
                    "owner-gone": 3,
                    "refused": 4,
                    "lost": 5,
                    "invalid-reply": 6,
                    "render-failed": 7,
                    "turn-failed": 8,
                    "wait-refused": 9,
                    "queue-budget-spent": 10,
                    "out-of-budget": 11,
                    "measurement-untrusted": 12,
                }.get(self.reason, 0)
                == 10
            )
            == (self.limit_at is not None)
        ):
            raise ValueError("limit_at: must match reason")
        if not (
            (
                {
                    "timed-out": 1,
                    "workspace-closed": 2,
                    "owner-gone": 3,
                    "refused": 4,
                    "lost": 5,
                    "invalid-reply": 6,
                    "render-failed": 7,
                    "turn-failed": 8,
                    "wait-refused": 9,
                    "queue-budget-spent": 10,
                    "out-of-budget": 11,
                    "measurement-untrusted": 12,
                }.get(self.reason, 0)
                == 4
            )
            == (
                {
                    "stale-view": 1,
                    "identity-conflict": 2,
                    "unknown-kind": 3,
                    "unknown-schema": 4,
                    "undeclared-operation": 5,
                    "capability": 6,
                    "ownership": 7,
                    "generation": 8,
                    "closed-scope": 9,
                    "budget": 10,
                    "dependency": 11,
                    "evidence": 12,
                    "already-settled": 13,
                    "not-implemented-in-kernel": 14,
                }.get(self.cause, 0)
                != 0
            )
        ):
            raise ValueError("cause: must match reason")
        if not (
            (
                {
                    "timed-out": 1,
                    "workspace-closed": 2,
                    "owner-gone": 3,
                    "refused": 4,
                    "lost": 5,
                    "invalid-reply": 6,
                    "render-failed": 7,
                    "turn-failed": 8,
                    "wait-refused": 9,
                    "queue-budget-spent": 10,
                    "out-of-budget": 11,
                    "measurement-untrusted": 12,
                }.get(self.reason, 0)
                == 6
            )
            == (len(self.errors) > 0)
        ):
            raise ValueError("errors: must match reason")
        if not (
            (
                {
                    "timed-out": 1,
                    "workspace-closed": 2,
                    "owner-gone": 3,
                    "refused": 4,
                    "lost": 5,
                    "invalid-reply": 6,
                    "render-failed": 7,
                    "turn-failed": 8,
                    "wait-refused": 9,
                    "queue-budget-spent": 10,
                    "out-of-budget": 11,
                    "measurement-untrusted": 12,
                }.get(self.reason, 0)
                == 8
            )
            == (
                {
                    "provider_failed": 1,
                    "transport_lost": 2,
                    "cancelled": 3,
                    "provider_unavailable": 4,
                }.get(self.turn_failure, 0)
                != 0
            )
        ):
            raise ValueError("turn_failure: must match reason")
        if not (
            (
                {
                    "timed-out": 1,
                    "workspace-closed": 2,
                    "owner-gone": 3,
                    "refused": 4,
                    "lost": 5,
                    "invalid-reply": 6,
                    "render-failed": 7,
                    "turn-failed": 8,
                    "wait-refused": 9,
                    "queue-budget-spent": 10,
                    "out-of-budget": 11,
                    "measurement-untrusted": 12,
                }.get(self.reason, 0)
                == 9
            )
            == (
                {"not-offered": 1, "not-recorded": 2, "not-published": 3, "not-resumable": 4}.get(
                    self.refusal, 0
                )
                != 0
            )
        ):
            raise ValueError("refusal: must match reason")
        if not (
            not (self.turn is not None)
            or {
                "timed-out": 1,
                "workspace-closed": 2,
                "owner-gone": 3,
                "refused": 4,
                "lost": 5,
                "invalid-reply": 6,
                "render-failed": 7,
                "turn-failed": 8,
                "wait-refused": 9,
                "queue-budget-spent": 10,
                "out-of-budget": 11,
                "measurement-untrusted": 12,
            }.get(self.reason, 0)
            == 1
            or {
                "timed-out": 1,
                "workspace-closed": 2,
                "owner-gone": 3,
                "refused": 4,
                "lost": 5,
                "invalid-reply": 6,
                "render-failed": 7,
                "turn-failed": 8,
                "wait-refused": 9,
                "queue-budget-spent": 10,
                "out-of-budget": 11,
                "measurement-untrusted": 12,
            }.get(self.reason, 0)
            == 5
            or {
                "timed-out": 1,
                "workspace-closed": 2,
                "owner-gone": 3,
                "refused": 4,
                "lost": 5,
                "invalid-reply": 6,
                "render-failed": 7,
                "turn-failed": 8,
                "wait-refused": 9,
                "queue-budget-spent": 10,
                "out-of-budget": 11,
                "measurement-untrusted": 12,
            }.get(self.reason, 0)
            == 6
            or {
                "timed-out": 1,
                "workspace-closed": 2,
                "owner-gone": 3,
                "refused": 4,
                "lost": 5,
                "invalid-reply": 6,
                "render-failed": 7,
                "turn-failed": 8,
                "wait-refused": 9,
                "queue-budget-spent": 10,
                "out-of-budget": 11,
                "measurement-untrusted": 12,
            }.get(self.reason, 0)
            == 8
            or {
                "timed-out": 1,
                "workspace-closed": 2,
                "owner-gone": 3,
                "refused": 4,
                "lost": 5,
                "invalid-reply": 6,
                "render-failed": 7,
                "turn-failed": 8,
                "wait-refused": 9,
                "queue-budget-spent": 10,
                "out-of-budget": 11,
                "measurement-untrusted": 12,
            }.get(self.reason, 0)
            == 9
        ):
            raise ValueError("turn: does not belong to reason")
        if not (
            not (self.digest is not None)
            or {
                "timed-out": 1,
                "workspace-closed": 2,
                "owner-gone": 3,
                "refused": 4,
                "lost": 5,
                "invalid-reply": 6,
                "render-failed": 7,
                "turn-failed": 8,
                "wait-refused": 9,
                "queue-budget-spent": 10,
                "out-of-budget": 11,
                "measurement-untrusted": 12,
            }.get(self.reason, 0)
            == 8
        ):
            raise ValueError("digest: does not belong to reason")

    __hash__ = CoreValue.cached_hash


resolve_value(ReplyError)
resolve_value(Failed)
