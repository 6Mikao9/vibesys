# Generated from vibesys/core/v1/contracts.proto by protoc-gen-corevalue. DO NOT EDIT.
from __future__ import annotations

from typing import Callable, Literal, cast

from protobuf import Message, Oneof

import vs_core.api as values
from vs_proto.api import core_contracts as pb
from vs_runtime._core_values.vibesys.core.v1.common_boundary import (
    decode_ArtifactRef,
    decode_OperationId,
    decode_PoolId,
    decode_RequestId,
    decode_ResourceId,
    decode_SchemaRef,
    decode_Scope,
    decode_SessionId,
    encode_ArtifactRef,
    encode_OperationId,
    encode_PoolId,
    encode_RequestId,
    encode_ResourceId,
    encode_SchemaRef,
    encode_Scope,
    encode_SessionId,
)
from vs_runtime._core_values.vibesys.core.v1.leaves_boundary import (
    decode_LifecycleCapability,
    decode_RevisionRef,
    decode_TaskObjective,
    decode_TaskStage,
    encode_LifecycleCapability,
    encode_RevisionRef,
    encode_TaskObjective,
    encode_TaskStage,
)
from vs_runtime._core_values.vibesys.core.v1.vocabulary_boundary import (
    decode_EvidenceKind,
    decode_LifecycleClass,
    decode_OperationNormalizationKind,
    decode_RevisionAuthority,
    encode_EvidenceKind,
    encode_LifecycleClass,
    encode_OperationNormalizationKind,
    encode_RevisionAuthority,
)
from vs_runtime._corevalue_wkt import decode_timestamp, encode_timestamp


def encode_ReleaseKind(
    v: Literal["session", "job", "workspace", "operation", "request"] | None,
) -> pb.ReleaseKind:
    return {
        "session": pb.ReleaseKind.SESSION,
        "job": pb.ReleaseKind.JOB,
        "workspace": pb.ReleaseKind.WORKSPACE,
        "operation": pb.ReleaseKind.OPERATION,
        "request": pb.ReleaseKind.REQUEST,
    }.get(v, pb.ReleaseKind.UNSPECIFIED)


def decode_ReleaseKind(
    v: pb.ReleaseKind,
) -> Literal["session", "job", "workspace", "operation", "request"]:
    return cast(
        Literal["session", "job", "workspace", "operation", "request"],
        {1: "session", 2: "job", 3: "workspace", 4: "operation", 5: "request"}[v],
    )


def encode_ScopedAdmissionReopenOutcomeAdmission(
    v: Literal["reopened", "closed", "unknown"] | None,
) -> pb.ScopedAdmissionReopenOutcomeAdmission:
    return {
        "reopened": pb.ScopedAdmissionReopenOutcomeAdmission.REOPENED,
        "closed": pb.ScopedAdmissionReopenOutcomeAdmission.CLOSED,
        "unknown": pb.ScopedAdmissionReopenOutcomeAdmission.UNKNOWN,
    }.get(v, pb.ScopedAdmissionReopenOutcomeAdmission.UNSPECIFIED)


def decode_ScopedAdmissionReopenOutcomeAdmission(
    v: pb.ScopedAdmissionReopenOutcomeAdmission,
) -> Literal["reopened", "closed", "unknown"]:
    return cast(
        Literal["reopened", "closed", "unknown"], {1: "reopened", 2: "closed", 3: "unknown"}[v]
    )


def _required[T](value: T | None) -> T:
    if value is None:
        raise ValueError("Required protobuf message is absent")
    return value


def encode_NullValue(v: None) -> pb.NullValue:
    return pb.NullValue()


def decode_NullValue(m: pb.NullValue) -> None:
    return None


def encode_PureOptionValue(v: str | int | float | bool | None) -> pb.PureOptionValue:
    if type(v) is str:
        return pb.PureOptionValue(value=Oneof("text", v))
    if type(v) is int:
        return pb.PureOptionValue(value=Oneof("integer", str(v)))
    if type(v) is float:
        return pb.PureOptionValue(value=Oneof("real", v))
    if type(v) is bool:
        return pb.PureOptionValue(value=Oneof("flag", v))
    if type(v) is type(None):
        return pb.PureOptionValue(value=Oneof("null", encode_NullValue(v)))
    raise TypeError("Invalid PureOptionValue member: " + type(v).__name__)


def decode_PureOptionValue(m: pb.PureOptionValue) -> str | int | float | bool | None:
    match m.value:
        case Oneof("text", value):
            return value
        case Oneof("integer", value):
            return int(value)
        case Oneof("real", value):
            return value
        case Oneof("flag", value):
            return value
        case Oneof("null", value):
            return decode_NullValue(value)
        case _:
            raise ValueError("Missing PureOptionValue member")


def encode_PureOption(v: values.PureOption) -> pb.PureOption:
    return pb.PureOption(key=v.key, value=encode_PureOptionValue(v.value))


def decode_PureOption(m: pb.PureOption) -> values.PureOption:
    return values.PureOption(key=m.key, value=decode_PureOptionValue(_required(m.value)))


def encode_OperationSchemaRef(v: values.OperationSchemaRef) -> pb.OperationSchemaRef:
    return pb.OperationSchemaRef(
        kind=v.kind,
        request_schema=encode_SchemaRef(v.request_schema),
        outcome_schema=encode_SchemaRef(v.outcome_schema),
        lifecycle=encode_LifecycleClass(v.lifecycle),
    )


def decode_OperationSchemaRef(m: pb.OperationSchemaRef) -> values.OperationSchemaRef:
    return values.OperationSchemaRef(
        kind=m.kind,
        request_schema=decode_SchemaRef(_required(m.request_schema)),
        outcome_schema=decode_SchemaRef(_required(m.outcome_schema)),
        lifecycle=decode_LifecycleClass(m.lifecycle),
    )


def encode_OperationDescriptor(v: values.OperationDescriptor) -> pb.OperationDescriptor:
    return pb.OperationDescriptor(
        kind=v.kind,
        request_schema=encode_SchemaRef(v.request_schema),
        outcome_schema=encode_SchemaRef(v.outcome_schema),
        lifecycle=encode_LifecycleClass(v.lifecycle),
        normalization=encode_OperationNormalizationKind(v.normalization),
        inspect=v.inspect,
        cancel=v.cancel,
        watch=v.watch,
        resource_pool=None if v.resource_pool is None else encode_PoolId(v.resource_pool),
        revision_authority=encode_RevisionAuthority(v.revision_authority),
    )


def decode_OperationDescriptor(m: pb.OperationDescriptor) -> values.OperationDescriptor:
    return values.OperationDescriptor(
        kind=m.kind,
        request_schema=decode_SchemaRef(_required(m.request_schema)),
        outcome_schema=decode_SchemaRef(_required(m.outcome_schema)),
        lifecycle=decode_LifecycleClass(m.lifecycle),
        normalization=decode_OperationNormalizationKind(m.normalization),
        inspect=m.inspect,
        cancel=m.cancel,
        watch=m.watch,
        resource_pool=None if m.resource_pool is None else decode_PoolId(m.resource_pool),
        revision_authority=decode_RevisionAuthority(m.revision_authority),
    )


def encode_Capabilities(v: values.Capabilities) -> pb.Capabilities:
    return pb.Capabilities(
        lifecycle=[encode_LifecycleCapability(x) for x in sorted(v.lifecycle)],
        operations=[encode_OperationDescriptor(x) for x in v.operations],
    )


def decode_Capabilities(m: pb.Capabilities) -> values.Capabilities:
    return values.Capabilities(
        lifecycle=frozenset(decode_LifecycleCapability(x) for x in m.lifecycle),
        operations=tuple(decode_OperationDescriptor(x) for x in m.operations),
    )


def encode_TaskTerms(v: values.TaskTerms) -> pb.TaskTerms:
    return pb.TaskTerms(
        recipe=encode_ArtifactRef(v.recipe),
        stages=[encode_TaskStage(x) for x in v.stages],
        measured=[x for x in v.measured],
        queue_allowance=v.queue_allowance,
        accuracy_stage=v.accuracy_stage,
        benchmark_stage=v.benchmark_stage,
        accuracy_gated=[encode_EvidenceKind(x) for x in v.accuracy_gated],
        operation_seconds=v.operation_seconds,
        objectives=[encode_TaskObjective(x) for x in v.objectives],
        relative_noise=v.relative_noise,
        base_staleness=v.base_staleness,
        runaway_evaluations=str(v.runaway_evaluations),
        profile_fields=[x for x in v.profile_fields],
    )


def decode_TaskTerms(m: pb.TaskTerms) -> values.TaskTerms:
    return values.TaskTerms(
        recipe=decode_ArtifactRef(_required(m.recipe)),
        stages=tuple(decode_TaskStage(x) for x in m.stages),
        measured=tuple(x for x in m.measured),
        queue_allowance=m.queue_allowance,
        accuracy_stage=m.accuracy_stage if m.has_field("accuracy_stage") else None,
        benchmark_stage=m.benchmark_stage if m.has_field("benchmark_stage") else None,
        accuracy_gated=tuple(decode_EvidenceKind(x) for x in m.accuracy_gated),
        operation_seconds=m.operation_seconds,
        objectives=tuple(decode_TaskObjective(x) for x in m.objectives),
        relative_noise=m.relative_noise,
        base_staleness=m.base_staleness,
        runaway_evaluations=int(m.runaway_evaluations),
        profile_fields=tuple(x for x in m.profile_fields),
    )


def encode_RunFacts(v: values.RunFacts) -> pb.RunFacts:
    return pb.RunFacts(
        objective=v.objective,
        options=[encode_PureOption(x) for x in v.options],
        constraints=[encode_PureOption(x) for x in v.constraints],
        baseline=encode_RevisionRef(v.baseline),
        evaluator_digest=v.evaluator_digest,
        workload_digest=v.workload_digest,
        environment_digest=v.environment_digest,
        dedicated_measurement_resources=v.dedicated_measurement_resources,
        artifacts=[encode_ArtifactRef(x) for x in v.artifacts],
        task=None if v.task is None else encode_TaskTerms(v.task),
    )


def decode_RunFacts(m: pb.RunFacts) -> values.RunFacts:
    return values.RunFacts(
        objective=m.objective,
        options=tuple(decode_PureOption(x) for x in m.options),
        constraints=tuple(decode_PureOption(x) for x in m.constraints),
        baseline=decode_RevisionRef(_required(m.baseline)),
        evaluator_digest=m.evaluator_digest,
        workload_digest=m.workload_digest,
        environment_digest=m.environment_digest,
        dedicated_measurement_resources=m.dedicated_measurement_resources,
        artifacts=tuple(decode_ArtifactRef(x) for x in m.artifacts),
        task=None if m.task is None else decode_TaskTerms(m.task),
    )


def encode_ReleaseIdentity(
    v: values.SessionId | values.ResourceId | values.RequestId | values.OperationId,
) -> pb.ReleaseIdentity:
    if type(v) is values.SessionId:
        return pb.ReleaseIdentity(value=Oneof("session", encode_SessionId(v)))
    if type(v) is values.ResourceId:
        return pb.ReleaseIdentity(value=Oneof("resource", encode_ResourceId(v)))
    if type(v) is values.RequestId:
        return pb.ReleaseIdentity(value=Oneof("request", encode_RequestId(v)))
    if type(v) is values.OperationId:
        return pb.ReleaseIdentity(value=Oneof("operation", encode_OperationId(v)))
    raise TypeError("Invalid ReleaseIdentity member: " + type(v).__name__)


def decode_ReleaseIdentity(
    m: pb.ReleaseIdentity,
) -> values.SessionId | values.ResourceId | values.RequestId | values.OperationId:
    match m.value:
        case Oneof("session", value):
            return decode_SessionId(value)
        case Oneof("resource", value):
            return decode_ResourceId(value)
        case Oneof("request", value):
            return decode_RequestId(value)
        case Oneof("operation", value):
            return decode_OperationId(value)
        case _:
            raise ValueError("Missing ReleaseIdentity member")


def encode_ReleaseDependency(v: values.ReleaseDependency) -> pb.ReleaseDependency:
    return pb.ReleaseDependency(
        kind=encode_ReleaseKind(v.kind), identity=encode_ReleaseIdentity(v.identity)
    )


def decode_ReleaseDependency(m: pb.ReleaseDependency) -> values.ReleaseDependency:
    return values.ReleaseDependency(
        kind=decode_ReleaseKind(m.kind), identity=decode_ReleaseIdentity(_required(m.identity))
    )


def encode_OperationWire(v: values.OperationWire) -> pb.OperationWire:
    return pb.OperationWire(
        schema_ref=encode_OperationSchemaRef(v.schema_ref), payload_json=v.payload_json
    )


def decode_OperationWire(m: pb.OperationWire) -> values.OperationWire:
    return values.OperationWire(
        schema_ref=decode_OperationSchemaRef(_required(m.schema_ref)), payload_json=m.payload_json
    )


def encode_ScopedAdmissionReopenOutcome(
    v: values.ScopedAdmissionReopenOutcome,
) -> pb.ScopedAdmissionReopenOutcome:
    return pb.ScopedAdmissionReopenOutcome(
        scope=encode_Scope(v.scope),
        admission=encode_ScopedAdmissionReopenOutcomeAdmission(v.admission),
    )


def decode_ScopedAdmissionReopenOutcome(
    m: pb.ScopedAdmissionReopenOutcome,
) -> values.ScopedAdmissionReopenOutcome:
    return values.ScopedAdmissionReopenOutcome(
        scope=decode_Scope(_required(m.scope)),
        admission=decode_ScopedAdmissionReopenOutcomeAdmission(m.admission),
    )


ENCODERS: dict[type, Callable[..., Message]] = {
    values.PureOption: encode_PureOption,
    values.OperationSchemaRef: encode_OperationSchemaRef,
    values.OperationDescriptor: encode_OperationDescriptor,
    values.Capabilities: encode_Capabilities,
    values.TaskTerms: encode_TaskTerms,
    values.RunFacts: encode_RunFacts,
    values.ReleaseDependency: encode_ReleaseDependency,
    values.OperationWire: encode_OperationWire,
    values.ScopedAdmissionReopenOutcome: encode_ScopedAdmissionReopenOutcome,
}
DECODERS: dict[type, Callable[..., object]] = {
    pb.NullValue: decode_NullValue,
    pb.PureOptionValue: decode_PureOptionValue,
    pb.PureOption: decode_PureOption,
    pb.OperationSchemaRef: decode_OperationSchemaRef,
    pb.OperationDescriptor: decode_OperationDescriptor,
    pb.Capabilities: decode_Capabilities,
    pb.TaskTerms: decode_TaskTerms,
    pb.RunFacts: decode_RunFacts,
    pb.ReleaseIdentity: decode_ReleaseIdentity,
    pb.ReleaseDependency: decode_ReleaseDependency,
    pb.OperationWire: decode_OperationWire,
    pb.ScopedAdmissionReopenOutcome: decode_ScopedAdmissionReopenOutcome,
}
