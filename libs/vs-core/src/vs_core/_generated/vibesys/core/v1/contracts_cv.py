# Generated from vibesys/core/v1/contracts.proto by protoc-gen-corevalue. DO NOT EDIT.
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
from vs_core._generated.vibesys.core.v1.common_cv import SchemaRef
from vs_core._generated.vibesys.core.v1.vocabulary_cv import CheckoutStatus, LifecycleClass

type NullValue = None

type PureOptionValue = str | int | Annotated[float, Field(allow_inf_nan=False)] | bool | NullValue


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class PureOption(CoreValue):
    "Pure option lifecycle contract."

    model_fields: ClassVar[tuple[str, ...]] = ("key", "value")
    key: Annotated[str, Field(min_length=1)]
    value: PureOptionValue

    __hash__ = CoreValue.cached_hash


@final
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
class OperationSchemaRef(CoreValue):
    "Operation schema ref lifecycle contract."

    model_fields: ClassVar[tuple[str, ...]] = (
        "kind",
        "request_schema",
        "outcome_schema",
        "lifecycle",
    )
    kind: Annotated[str, Field(min_length=1)]
    request_schema: SchemaRef
    outcome_schema: SchemaRef
    lifecycle: LifecycleClass

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class AdoptionPublication(CoreValue):
    "A retained revision published without changing the operator checkout."

    model_fields: ClassVar[tuple[str, ...]] = ("revision", "commit", "branch")
    revision: Annotated[str, Field(min_length=1)]
    commit: Annotated[str, Field(pattern="^[0-9a-f]{40}$")]
    branch: Annotated[str, Field(min_length=1)]

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class CheckoutUpdate(CoreValue):
    "The independent, optional checkout step never changes publication success."

    model_fields: ClassVar[tuple[str, ...]] = ("status",)
    status: CheckoutStatus

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class EvaluationLease(CoreValue):
    "A sequence of measurement jobs holds one slot until its final member ends.\nScoped by the submitting owner and generation. Closing that scope cancels\nevery member and returns the slot, including between members or after a crash."

    model_fields: ClassVar[tuple[str, ...]] = ("key", "retain")
    key: Annotated[str, Field(min_length=1)]
    retain: bool = False

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class EvaluationTiming(CoreValue):
    "Exact in-process admission times on the run clock. A queued gate spends only\nthe queue hang budget; its execution limit begins at admitted_at."

    model_fields: ClassVar[tuple[str, ...]] = ("queued_at", "admitted_at")
    queued_at: Annotated[float, Field(ge=0.0, allow_inf_nan=False)]
    admitted_at: Annotated[float | None, Field(ge=0.0, allow_inf_nan=False)] = None

    def __post_init__(self) -> None:
        """Enforce the proto message rules at construction as well as decode."""
        if not (not (self.admitted_at is not None) or self.admitted_at >= self.queued_at):
            raise ValueError("admitted_at precedes queued_at")

    __hash__ = CoreValue.cached_hash


resolve_value(PureOption)
resolve_value(OperationSchemaRef)
resolve_value(AdoptionPublication)
resolve_value(CheckoutUpdate)
resolve_value(EvaluationLease)
resolve_value(EvaluationTiming)
