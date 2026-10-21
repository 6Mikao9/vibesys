# Generated from vibesys/core/v1/agent_facts.proto by protoc-gen-corevalue. DO NOT EDIT.
from __future__ import annotations

from typing import Callable, Literal, cast

from protobuf import Message, Oneof

import vs_core.api as values
from vs_proto.api import core_agent_facts as pb
from vs_runtime._core_values.vibesys.core.v1.common_boundary import (
    decode_ArtifactRef,
    decode_EvidenceId,
    decode_RequestId,
    decode_Scope,
    encode_ArtifactRef,
    encode_EvidenceId,
    encode_RequestId,
    encode_Scope,
)
from vs_runtime._core_values.vibesys.core.v1.failures_boundary import (
    decode_ReplyError,
    encode_ReplyError,
)
from vs_runtime._core_values.vibesys.core.v1.history_boundary import (
    decode_RepeatedFailureGuidance,
    encode_RepeatedFailureGuidance,
)
from vs_runtime._core_values.vibesys.core.v1.leaves_boundary import (
    decode_Comparison,
    decode_Observation,
    decode_Reading,
    decode_RevisionRef,
    decode_WaitUnrecorded,
    encode_Comparison,
    encode_Observation,
    encode_Reading,
    encode_RevisionRef,
    encode_WaitUnrecorded,
)
from vs_runtime._core_values.vibesys.core.v1.vocabulary_boundary import (
    decode_EvidenceKind,
    decode_ObservationStatus,
    decode_WaitEnd,
    encode_EvidenceKind,
    encode_ObservationStatus,
    encode_WaitEnd,
)
from vs_runtime._corevalue_wkt import decode_timestamp, encode_timestamp


def encode_EvidencePurpose(
    v: Literal["baseline", "local-validation", "official", "profile"] | None,
) -> pb.EvidencePurpose:
    return {
        "baseline": pb.EvidencePurpose.BASELINE,
        "local-validation": pb.EvidencePurpose.LOCAL_VALIDATION,
        "official": pb.EvidencePurpose.OFFICIAL,
        "profile": pb.EvidencePurpose.PROFILE,
    }.get(v, pb.EvidencePurpose.UNSPECIFIED)


def decode_EvidencePurpose(
    v: pb.EvidencePurpose,
) -> Literal["baseline", "local-validation", "official", "profile"]:
    return cast(
        Literal["baseline", "local-validation", "official", "profile"],
        {1: "baseline", 2: "local-validation", 3: "official", 4: "profile"}[v],
    )


def encode_EvidenceProvenance(v: Literal["trusted", "self-report"] | None) -> pb.EvidenceProvenance:
    return {
        "trusted": pb.EvidenceProvenance.TRUSTED,
        "self-report": pb.EvidenceProvenance.SELF_REPORT,
    }.get(v, pb.EvidenceProvenance.UNSPECIFIED)


def decode_EvidenceProvenance(v: pb.EvidenceProvenance) -> Literal["trusted", "self-report"]:
    return cast(Literal["trusted", "self-report"], {1: "trusted", 2: "self-report"}[v])


def _required[T](value: T | None) -> T:
    if value is None:
        raise ValueError("Required protobuf message is absent")
    return value


def encode_EvidenceAcceptanceReceipt(
    v: values.EvidenceAcceptanceReceipt,
) -> pb.EvidenceAcceptanceReceipt:
    return pb.EvidenceAcceptanceReceipt(observation=encode_Observation(v.observation))


def decode_EvidenceAcceptanceReceipt(
    m: pb.EvidenceAcceptanceReceipt,
) -> values.EvidenceAcceptanceReceipt:
    return values.EvidenceAcceptanceReceipt(
        observation=decode_Observation(_required(m.observation))
    )


def encode_EvidenceRef(v: values.EvidenceRef) -> pb.EvidenceRef:
    return pb.EvidenceRef(
        evidence_id=encode_EvidenceId(v.evidence_id),
        kind=encode_EvidenceKind(v.kind),
        purpose=encode_EvidencePurpose(v.purpose),
        scope=encode_Scope(v.scope),
        source_request=encode_RequestId(v.source_request),
        candidate=encode_RevisionRef(v.candidate),
        observation_sequence=str(v.observation_sequence),
        evaluator_digest=v.evaluator_digest,
        workload_digest=v.workload_digest,
        environment_digest=v.environment_digest,
        provenance=encode_EvidenceProvenance(v.provenance),
        status=encode_ObservationStatus(v.status),
        artifacts=[encode_ArtifactRef(x) for x in v.artifacts],
        acceptance_receipt=None
        if v.acceptance_receipt is None
        else encode_EvidenceAcceptanceReceipt(v.acceptance_receipt),
        reused_from=None if v.reused_from is None else encode_RequestId(v.reused_from),
        reading=None if v.reading is None else encode_Reading(v.reading),
        unmet_requirement=v.unmet_requirement,
    )


def decode_EvidenceRef(m: pb.EvidenceRef) -> values.EvidenceRef:
    return values.EvidenceRef(
        evidence_id=decode_EvidenceId(_required(m.evidence_id)),
        kind=decode_EvidenceKind(m.kind),
        purpose=decode_EvidencePurpose(m.purpose),
        scope=decode_Scope(_required(m.scope)),
        source_request=decode_RequestId(_required(m.source_request)),
        candidate=decode_RevisionRef(_required(m.candidate)),
        observation_sequence=int(m.observation_sequence),
        evaluator_digest=m.evaluator_digest,
        workload_digest=m.workload_digest,
        environment_digest=m.environment_digest,
        provenance=decode_EvidenceProvenance(m.provenance),
        status=decode_ObservationStatus(m.status),
        artifacts=tuple(decode_ArtifactRef(x) for x in m.artifacts),
        acceptance_receipt=None
        if m.acceptance_receipt is None
        else decode_EvidenceAcceptanceReceipt(m.acceptance_receipt),
        reused_from=None if m.reused_from is None else decode_RequestId(m.reused_from),
        reading=None if m.reading is None else decode_Reading(m.reading),
        unmet_requirement=m.unmet_requirement if m.has_field("unmet_requirement") else None,
    )


def encode_ResumeFacts(v: values.ResumeFacts) -> pb.ResumeFacts:
    return pb.ResumeFacts(
        kind=v.kind,
        agent=v.agent,
        retained=None if v.retained is None else encode_RevisionRef(v.retained),
        ended_by=encode_WaitEnd(v.ended_by),
        queue_budget_spent=v.queue_budget_spent,
        evidence=[encode_EvidenceRef(x) for x in v.evidence],
        repeated_failure=None
        if v.repeated_failure is None
        else encode_RepeatedFailureGuidance(v.repeated_failure),
        comparisons=[encode_Comparison(x) for x in v.comparisons],
        settling=[x for x in v.settling],
    )


def decode_ResumeFacts(m: pb.ResumeFacts) -> values.ResumeFacts:
    return values.ResumeFacts(
        kind=cast(Literal["resume"], m.kind),
        agent=m.agent,
        retained=None if m.retained is None else decode_RevisionRef(m.retained),
        ended_by=None if m.ended_by == 0 else decode_WaitEnd(m.ended_by),
        queue_budget_spent=m.queue_budget_spent,
        evidence=tuple(decode_EvidenceRef(x) for x in m.evidence),
        repeated_failure=None
        if m.repeated_failure is None
        else decode_RepeatedFailureGuidance(m.repeated_failure),
        comparisons=tuple(decode_Comparison(x) for x in m.comparisons),
        settling=tuple(x for x in m.settling),
    )


def encode_ReplyCorrection(v: values.ReplyCorrection) -> pb.ReplyCorrection:
    return pb.ReplyCorrection(
        kind=v.kind, role=v.role, errors=[encode_ReplyError(x) for x in v.errors]
    )


def decode_ReplyCorrection(m: pb.ReplyCorrection) -> values.ReplyCorrection:
    return values.ReplyCorrection(
        kind=cast(Literal["reply-correction"], m.kind),
        role=m.role,
        errors=tuple(decode_ReplyError(x) for x in m.errors),
    )


def encode_ProtocolContext(
    v: values.ReplyCorrection | values.ResumeFacts | values.WaitUnrecorded,
) -> pb.ProtocolContext:
    if type(v) is values.ReplyCorrection:
        return pb.ProtocolContext(value=Oneof("correction", encode_ReplyCorrection(v)))
    if type(v) is values.ResumeFacts:
        return pb.ProtocolContext(value=Oneof("resume", encode_ResumeFacts(v)))
    if type(v) is values.WaitUnrecorded:
        return pb.ProtocolContext(value=Oneof("unrecorded", encode_WaitUnrecorded(v)))
    raise TypeError("Invalid ProtocolContext member: " + type(v).__name__)


def decode_ProtocolContext(
    m: pb.ProtocolContext,
) -> values.ReplyCorrection | values.ResumeFacts | values.WaitUnrecorded:
    match m.value:
        case Oneof("correction", value):
            return decode_ReplyCorrection(value)
        case Oneof("resume", value):
            return decode_ResumeFacts(value)
        case Oneof("unrecorded", value):
            return decode_WaitUnrecorded(value)
        case _:
            raise ValueError("Missing ProtocolContext member")


ENCODERS: dict[type, Callable[..., Message]] = {
    values.EvidenceAcceptanceReceipt: encode_EvidenceAcceptanceReceipt,
    values.EvidenceRef: encode_EvidenceRef,
    values.ResumeFacts: encode_ResumeFacts,
    values.ReplyCorrection: encode_ReplyCorrection,
}
DECODERS: dict[type, Callable[..., object]] = {
    pb.EvidenceAcceptanceReceipt: decode_EvidenceAcceptanceReceipt,
    pb.EvidenceRef: decode_EvidenceRef,
    pb.ResumeFacts: decode_ResumeFacts,
    pb.ReplyCorrection: decode_ReplyCorrection,
    pb.ProtocolContext: decode_ProtocolContext,
}
