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
from vs_core._generated.vibesys.core.v1.common_cv import (
    ArtifactRef,
    OperationId,
    PoolId,
    RequestId,
    ResourceId,
    SchemaRef,
    Scope,
    SessionId,
)
from vs_core._generated.vibesys.core.v1.leaves_cv import (
    LifecycleCapability,
    RevisionRef,
    TaskObjective,
    TaskStage,
)
from vs_core._generated.vibesys.core.v1.vocabulary_cv import (
    EvidenceKind,
    LifecycleClass,
    OperationNormalizationKind,
    RevisionAuthority,
)
from vs_core._leaf_behavior import TaskRequirements

ReleaseKind = Literal["session", "job", "workspace", "operation", "request"]

ScopedAdmissionReopenOutcomeAdmission = Literal["reopened", "closed", "unknown"]

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
    "Immutable PureOption value."

    model_fields: ClassVar[tuple[str, ...]] = ("key", "value")
    key: Annotated[str, Field(min_length=1)]
    value: PureOptionValue

    __hash__ = CoreValue.cached_hash


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
    "The schema and lifecycle identity of one registered operation."

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
class OperationDescriptor(OperationSchemaRef):
    "Immutable OperationDescriptor value."

    model_fields: ClassVar[tuple[str, ...]] = (
        "kind",
        "request_schema",
        "outcome_schema",
        "lifecycle",
        "normalization",
        "inspect",
        "cancel",
        "watch",
        "resource_pool",
        "revision_authority",
    )
    kind: Annotated[str, Field(min_length=1)]
    request_schema: SchemaRef
    outcome_schema: SchemaRef
    lifecycle: LifecycleClass
    normalization: OperationNormalizationKind = OperationNormalizationKind("none")
    inspect: bool = False
    cancel: bool = False
    watch: bool = False
    resource_pool: PoolId | None = None
    revision_authority: RevisionAuthority = RevisionAuthority("none")

    def __post_init__(self) -> None:
        """Enforce the proto message rules at construction as well as decode."""
        if not (
            (
                {
                    "query": 1,
                    "idempotent_external_write": 2,
                    "owned_long_running_job": 3,
                    "session_turn": 4,
                }.get(self.lifecycle, 0)
                == 3
            )
            == (self.resource_pool is not None)
        ):
            raise ValueError(
                "resource_pool: only owned jobs declare a pool, and every owned job requires one"
            )
        if not (
            {"none": 1, "snapshot": 2, "retain": 3, "restore": 4, "discard": 5}.get(
                self.revision_authority, 0
            )
            == 1
            or {
                "query": 1,
                "idempotent_external_write": 2,
                "owned_long_running_job": 3,
                "session_turn": 4,
            }.get(self.lifecycle, 0)
            == 2
        ):
            raise ValueError("revision_authority: revision mutations require idempotent writes")
        if not (
            {"none": 1, "scope_reopen": 2}.get(self.normalization, 0) != 2
            or (
                {
                    "query": 1,
                    "idempotent_external_write": 2,
                    "owned_long_running_job": 3,
                    "session_turn": 4,
                }.get(self.lifecycle, 0)
                == 2
                and self.inspect
                and not (self.resource_pool is not None)
                and {"none": 1, "snapshot": 2, "retain": 3, "restore": 4, "discard": 5}.get(
                    self.revision_authority, 0
                )
                == 1
            )
        ):
            raise ValueError(
                "normalization: scope reopen requires inspectable non-revision idempotent write"
            )

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class Capabilities(CoreValue):
    "Immutable Capabilities value."

    model_fields: ClassVar[tuple[str, ...]] = ("lifecycle", "operations")
    lifecycle: frozenset[LifecycleCapability] = frozenset()
    operations: tuple[OperationDescriptor, ...] = ()

    @field_serializer("lifecycle", when_used="json")
    def _sorted_lifecycle(self, value: frozenset) -> list:
        """Canonical ordering for an immutable set."""
        return sorted(value)

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class TaskTerms(TaskRequirements, CoreValue):
    "Immutable TaskTerms value."

    model_fields: ClassVar[tuple[str, ...]] = (
        "recipe",
        "stages",
        "measured",
        "queue_allowance",
        "accuracy_stage",
        "benchmark_stage",
        "accuracy_gated",
        "operation_seconds",
        "objectives",
        "relative_noise",
        "base_staleness",
        "runaway_evaluations",
        "profile_fields",
    )
    recipe: ArtifactRef
    stages: tuple[TaskStage, ...]
    measured: tuple[str, ...]
    queue_allowance: Annotated[float, Field(ge=0.0, allow_inf_nan=False)]
    accuracy_stage: Annotated[str | None, Field(min_length=1)] = None
    benchmark_stage: Annotated[str | None, Field(min_length=1)] = None
    accuracy_gated: tuple[EvidenceKind, ...] = ()
    operation_seconds: Annotated[float, Field(gt=0.0, allow_inf_nan=False)]
    objectives: tuple[TaskObjective, ...] = ()
    relative_noise: Annotated[float, Field(ge=0.0, lt=1.0)] = 0.0
    base_staleness: Annotated[float, Field(gt=0.0, allow_inf_nan=False)] = 21600.0
    runaway_evaluations: Annotated[int, Field(ge=1)] = 40
    profile_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Enforce the proto message rules at construction as well as decode."""
        if not (all((any((s.stage_id == m) for s in self.stages)) for m in self.measured)):
            raise ValueError("measured: names stages the task lacks")
        if not (
            not (self.accuracy_stage is not None)
            or any((s.stage_id == self.accuracy_stage) for s in self.stages)
        ):
            raise ValueError("measured: names stages the task lacks")
        if not (
            not (self.benchmark_stage is not None)
            or any((s.stage_id == self.benchmark_stage) for s in self.stages)
        ):
            raise ValueError("measured: names stages the task lacks")

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class RunFacts(CoreValue):
    "Immutable RunFacts value."

    model_fields: ClassVar[tuple[str, ...]] = (
        "objective",
        "options",
        "constraints",
        "baseline",
        "evaluator_digest",
        "workload_digest",
        "environment_digest",
        "dedicated_measurement_resources",
        "artifacts",
        "task",
    )
    objective: str
    options: tuple[PureOption, ...] = ()
    constraints: tuple[PureOption, ...] = ()
    baseline: RevisionRef
    evaluator_digest: str
    workload_digest: str
    environment_digest: str
    dedicated_measurement_resources: Annotated[bool, Field(exclude_if=omit_unset)] = False
    artifacts: tuple[ArtifactRef, ...] = ()
    task: TaskTerms | None = None

    __hash__ = CoreValue.cached_hash


type ReleaseIdentity = SessionId | ResourceId | RequestId | OperationId


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class ReleaseDependency(CoreValue):
    "Immutable ReleaseDependency value."

    model_fields: ClassVar[tuple[str, ...]] = ("kind", "identity")
    kind: ReleaseKind
    identity: ReleaseIdentity

    def __post_init__(self) -> None:
        """Enforce the proto message rules at construction as well as decode."""
        if not (
            {"session": 1, "job": 2, "workspace": 3, "operation": 4, "request": 5}.get(self.kind, 0)
            != 1
            or isinstance(self.identity, SessionId)
        ):
            raise ValueError("identity: identity type mismatch")
        if not (
            {"session": 1, "job": 2, "workspace": 3, "operation": 4, "request": 5}.get(self.kind, 0)
            != 2
            or isinstance(self.identity, ResourceId)
        ):
            raise ValueError("identity: identity type mismatch")
        if not (
            {"session": 1, "job": 2, "workspace": 3, "operation": 4, "request": 5}.get(self.kind, 0)
            != 3
            or isinstance(self.identity, RequestId)
        ):
            raise ValueError("identity: identity type mismatch")
        if not (
            {"session": 1, "job": 2, "workspace": 3, "operation": 4, "request": 5}.get(self.kind, 0)
            != 4
            or isinstance(self.identity, OperationId)
        ):
            raise ValueError("identity: identity type mismatch")
        if not (
            {"session": 1, "job": 2, "workspace": 3, "operation": 4, "request": 5}.get(self.kind, 0)
            != 5
            or isinstance(self.identity, RequestId)
        ):
            raise ValueError("identity: identity type mismatch")

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class OperationWire(CoreValue):
    "Immutable OperationWire value."

    model_fields: ClassVar[tuple[str, ...]] = ("schema_ref", "payload_json")
    schema_ref: OperationSchemaRef
    payload_json: str

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class ScopedAdmissionReopenOutcome(CoreValue):
    "Immutable ScopedAdmissionReopenOutcome value."

    model_fields: ClassVar[tuple[str, ...]] = ("scope", "admission")
    scope: Scope
    admission: ScopedAdmissionReopenOutcomeAdmission

    __hash__ = CoreValue.cached_hash


resolve_value(PureOption)
resolve_value(OperationSchemaRef)
resolve_value(OperationDescriptor)
resolve_value(Capabilities)
resolve_value(TaskTerms)
resolve_value(RunFacts)
resolve_value(ReleaseDependency)
resolve_value(OperationWire)
resolve_value(ScopedAdmissionReopenOutcome)
