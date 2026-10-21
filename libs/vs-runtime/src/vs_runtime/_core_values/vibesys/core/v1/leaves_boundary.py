# Generated from vibesys/core/v1/leaves.proto by protoc-gen-corevalue. DO NOT EDIT.
from __future__ import annotations

from typing import Callable, Literal, cast

from protobuf import Message, Oneof

import vs_core.api as values
from vs_proto.api import core_leaves as pb
from vs_runtime._core_values.vibesys.core.v1.common_boundary import (
    decode_ArtifactRef,
    decode_AttemptId,
    decode_ChargeId,
    decode_ContinuationId,
    decode_DecisionId,
    decode_EventId,
    decode_EvidenceId,
    decode_HostId,
    decode_InputId,
    decode_InvocationId,
    decode_OperationId,
    decode_RequestId,
    decode_ResourceId,
    decode_RevisionId,
    decode_Scope,
    encode_ArtifactRef,
    encode_AttemptId,
    encode_ChargeId,
    encode_ContinuationId,
    encode_DecisionId,
    encode_EventId,
    encode_EvidenceId,
    encode_HostId,
    encode_InputId,
    encode_InvocationId,
    encode_OperationId,
    encode_RequestId,
    encode_ResourceId,
    encode_RevisionId,
    encode_Scope,
)
from vs_runtime._core_values.vibesys.core.v1.vocabulary_boundary import (
    decode_ChargeKind,
    decode_CheckpointDecline,
    decode_ComparisonVerdict,
    decode_EvaluationStageOutcome,
    decode_ObservationStatus,
    decode_WaitEnd,
    decode_WaitKind,
    decode_WorkspaceMode,
    encode_ChargeKind,
    encode_CheckpointDecline,
    encode_ComparisonVerdict,
    encode_EvaluationStageOutcome,
    encode_ObservationStatus,
    encode_WaitEnd,
    encode_WaitKind,
    encode_WorkspaceMode,
)
from vs_runtime._corevalue_wkt import decode_timestamp, encode_timestamp


def encode_Direction(v: Literal["max", "min"] | None) -> pb.Direction:
    return {"max": pb.Direction.MAX, "min": pb.Direction.MIN}.get(v, pb.Direction.UNSPECIFIED)


def decode_Direction(v: pb.Direction) -> Literal["max", "min"]:
    return cast(Literal["max", "min"], {1: "max", 2: "min"}[v])


def encode_ChargeClass(v: Literal["free", "paid", "correction", "resume"] | None) -> pb.ChargeClass:
    return {
        "free": pb.ChargeClass.FREE,
        "paid": pb.ChargeClass.PAID,
        "correction": pb.ChargeClass.CORRECTION,
        "resume": pb.ChargeClass.RESUME,
    }.get(v, pb.ChargeClass.UNSPECIFIED)


def decode_ChargeClass(v: pb.ChargeClass) -> Literal["free", "paid", "correction", "resume"]:
    return cast(
        Literal["free", "paid", "correction", "resume"],
        {1: "free", 2: "paid", 3: "correction", 4: "resume"}[v],
    )


def encode_LifecycleCapability(
    v: Literal["park", "interrupt", "steer", "suspend", "profile-capture"] | None,
) -> pb.LifecycleCapability:
    return {
        "park": pb.LifecycleCapability.PARK,
        "interrupt": pb.LifecycleCapability.INTERRUPT,
        "steer": pb.LifecycleCapability.STEER,
        "suspend": pb.LifecycleCapability.SUSPEND,
        "profile-capture": pb.LifecycleCapability.PROFILE_CAPTURE,
    }.get(v, pb.LifecycleCapability.UNSPECIFIED)


def decode_LifecycleCapability(
    v: pb.LifecycleCapability,
) -> Literal["park", "interrupt", "steer", "suspend", "profile-capture"]:
    return cast(
        Literal["park", "interrupt", "steer", "suspend", "profile-capture"],
        {1: "park", 2: "interrupt", 3: "steer", 4: "suspend", 5: "profile-capture"}[v],
    )


def encode_TaskObjectiveDirection(v: Literal["max", "min"] | None) -> pb.TaskObjectiveDirection:
    return {"max": pb.TaskObjectiveDirection.MAX, "min": pb.TaskObjectiveDirection.MIN}.get(
        v, pb.TaskObjectiveDirection.UNSPECIFIED
    )


def decode_TaskObjectiveDirection(v: pb.TaskObjectiveDirection) -> Literal["max", "min"]:
    return cast(Literal["max", "min"], {1: "max", 2: "min"}[v])


def encode_ChildManifestBasis(
    v: Literal["enumerated", "lifecycle-closed"] | None,
) -> pb.ChildManifestBasis:
    return {
        "enumerated": pb.ChildManifestBasis.ENUMERATED,
        "lifecycle-closed": pb.ChildManifestBasis.LIFECYCLE_CLOSED,
    }.get(v, pb.ChildManifestBasis.UNSPECIFIED)


def decode_ChildManifestBasis(
    v: pb.ChildManifestBasis,
) -> Literal["enumerated", "lifecycle-closed"]:
    return cast(
        Literal["enumerated", "lifecycle-closed"], {1: "enumerated", 2: "lifecycle-closed"}[v]
    )


def encode_JobProgressState(
    v: Literal["pending", "running", "unknown"] | None,
) -> pb.JobProgressState:
    return {
        "pending": pb.JobProgressState.PENDING,
        "running": pb.JobProgressState.RUNNING,
        "unknown": pb.JobProgressState.UNKNOWN,
    }.get(v, pb.JobProgressState.UNSPECIFIED)


def decode_JobProgressState(v: pb.JobProgressState) -> Literal["pending", "running", "unknown"]:
    return cast(
        Literal["pending", "running", "unknown"], {1: "pending", 2: "running", 3: "unknown"}[v]
    )


def _required[T](value: T | None) -> T:
    if value is None:
        raise ValueError("Required protobuf message is absent")
    return value


def encode_EvidenceKey(v: values.EvidenceKey) -> pb.EvidenceKey:
    return pb.EvidenceKey(
        kind=v.kind,
        source_request=encode_RequestId(v.source_request),
        evidence_id=encode_EvidenceId(v.evidence_id),
    )


def decode_EvidenceKey(m: pb.EvidenceKey) -> values.EvidenceKey:
    return values.EvidenceKey(
        kind=cast(Literal["evidence_key"], m.kind),
        source_request=decode_RequestId(_required(m.source_request)),
        evidence_id=decode_EvidenceId(_required(m.evidence_id)),
    )


def encode_RevisionRef(v: values.RevisionRef) -> pb.RevisionRef:
    return pb.RevisionRef(revision_id=encode_RevisionId(v.revision_id), digest=v.digest)


def decode_RevisionRef(m: pb.RevisionRef) -> values.RevisionRef:
    return values.RevisionRef(
        revision_id=decode_RevisionId(_required(m.revision_id)), digest=m.digest
    )


def encode_AttemptRef(v: values.AttemptRef) -> pb.AttemptRef:
    return pb.AttemptRef(attempt_id=encode_AttemptId(v.attempt_id), generation=str(v.generation))


def decode_AttemptRef(m: pb.AttemptRef) -> values.AttemptRef:
    return values.AttemptRef(
        attempt_id=decode_AttemptId(_required(m.attempt_id)), generation=int(m.generation)
    )


def encode_OperationRef(v: values.OperationRef) -> pb.OperationRef:
    return pb.OperationRef(
        operation_id=encode_OperationId(v.operation_id), generation=str(v.generation)
    )


def decode_OperationRef(m: pb.OperationRef) -> values.OperationRef:
    return values.OperationRef(
        operation_id=decode_OperationId(_required(m.operation_id)), generation=int(m.generation)
    )


def encode_DependencyRef(v: values.DependencyRef) -> pb.DependencyRef:
    return pb.DependencyRef(
        decision_id=None if v.decision_id is None else encode_DecisionId(v.decision_id),
        request_id=None if v.request_id is None else encode_RequestId(v.request_id),
    )


def decode_DependencyRef(m: pb.DependencyRef) -> values.DependencyRef:
    return values.DependencyRef(
        decision_id=None if m.decision_id is None else decode_DecisionId(m.decision_id),
        request_id=None if m.request_id is None else decode_RequestId(m.request_id),
    )


def encode_ScopeReopenNormalization(
    v: values.ScopeReopenNormalization,
) -> pb.ScopeReopenNormalization:
    return pb.ScopeReopenNormalization(
        kind=v.kind,
        attempt=encode_AttemptRef(v.attempt),
        continuation_id=encode_ContinuationId(v.continuation_id),
        park_authority=encode_RequestId(v.park_authority),
        resolved_cancelled_jobs=[encode_ResourceId(x) for x in v.resolved_cancelled_jobs],
    )


def decode_ScopeReopenNormalization(
    m: pb.ScopeReopenNormalization,
) -> values.ScopeReopenNormalization:
    return values.ScopeReopenNormalization(
        kind=cast(Literal["scope_reopen"], m.kind),
        attempt=decode_AttemptRef(_required(m.attempt)),
        continuation_id=decode_ContinuationId(_required(m.continuation_id)),
        park_authority=decode_RequestId(_required(m.park_authority)),
        resolved_cancelled_jobs=tuple(decode_ResourceId(x) for x in m.resolved_cancelled_jobs),
    )


def encode_TaskStage(v: values.TaskStage) -> pb.TaskStage:
    return pb.TaskStage(stage_id=v.stage_id, execution_seconds=v.execution_seconds)


def decode_TaskStage(m: pb.TaskStage) -> values.TaskStage:
    return values.TaskStage(stage_id=m.stage_id, execution_seconds=m.execution_seconds)


def encode_TaskObjective(v: values.TaskObjective) -> pb.TaskObjective:
    return pb.TaskObjective(name=v.name, direction=encode_TaskObjectiveDirection(v.direction))


def decode_TaskObjective(m: pb.TaskObjective) -> values.TaskObjective:
    return values.TaskObjective(name=m.name, direction=decode_TaskObjectiveDirection(m.direction))


def encode_ChildManifest(v: values.ChildManifest) -> pb.ChildManifest:
    return pb.ChildManifest(
        members=[encode_ResourceId(x) for x in v.members], basis=encode_ChildManifestBasis(v.basis)
    )


def decode_ChildManifest(m: pb.ChildManifest) -> values.ChildManifest:
    return values.ChildManifest(
        members=tuple(decode_ResourceId(x) for x in m.members),
        basis=decode_ChildManifestBasis(m.basis),
    )


def encode_Observation(v: values.Observation) -> pb.Observation:
    return pb.Observation(
        event_id=encode_EventId(v.event_id),
        request_id=encode_RequestId(v.request_id),
        scope=encode_Scope(v.scope),
        sequence=str(v.sequence),
        observed_at=v.observed_at,
        status=encode_ObservationStatus(v.status),
        resource_id=None if v.resource_id is None else encode_ResourceId(v.resource_id),
        revision=None if v.revision is None else encode_RevisionRef(v.revision),
        measured_input=v.measured_input,
        accepted=v.accepted,
        terminal=v.terminal,
        released=v.released,
        children=[encode_ResourceId(x) for x in v.children],
        children_complete=v.children_complete,
        child_manifest=None if v.child_manifest is None else encode_ChildManifest(v.child_manifest),
        admission_id=None if v.admission_id is None else encode_DecisionId(v.admission_id),
        diagnostic=v.diagnostic,
    )


def decode_Observation(m: pb.Observation) -> values.Observation:
    return values.Observation(
        event_id=decode_EventId(_required(m.event_id)),
        request_id=decode_RequestId(_required(m.request_id)),
        scope=decode_Scope(_required(m.scope)),
        sequence=int(m.sequence),
        observed_at=m.observed_at,
        status=decode_ObservationStatus(m.status),
        resource_id=None if m.resource_id is None else decode_ResourceId(m.resource_id),
        revision=None if m.revision is None else decode_RevisionRef(m.revision),
        measured_input=m.measured_input if m.has_field("measured_input") else None,
        accepted=m.accepted,
        terminal=m.terminal,
        released=m.released,
        children=tuple(decode_ResourceId(x) for x in m.children),
        children_complete=m.children_complete,
        child_manifest=None if m.child_manifest is None else decode_ChildManifest(m.child_manifest),
        admission_id=None if m.admission_id is None else decode_DecisionId(m.admission_id),
        diagnostic=m.diagnostic,
    )


def encode_ChargeReceipt(v: values.ChargeReceipt) -> pb.ChargeReceipt:
    return pb.ChargeReceipt(
        charge_id=encode_ChargeId(v.charge_id),
        kind=encode_ChargeKind(v.kind),
        invocation_id=None if v.invocation_id is None else encode_InvocationId(v.invocation_id),
        source_request=None if v.source_request is None else encode_RequestId(v.source_request),
        charged=str(v.charged),
        refunded=str(v.refunded),
        refund_sources=[encode_RequestId(x) for x in v.refund_sources],
        historical_proof=None
        if v.historical_proof is None
        else encode_ArtifactRef(v.historical_proof),
    )


def decode_ChargeReceipt(m: pb.ChargeReceipt) -> values.ChargeReceipt:
    return values.ChargeReceipt(
        charge_id=decode_ChargeId(_required(m.charge_id)),
        kind=decode_ChargeKind(m.kind),
        invocation_id=None if m.invocation_id is None else decode_InvocationId(m.invocation_id),
        source_request=None if m.source_request is None else decode_RequestId(m.source_request),
        charged=int(m.charged),
        refunded=int(m.refunded),
        refund_sources=tuple(decode_RequestId(x) for x in m.refund_sources),
        historical_proof=None
        if m.historical_proof is None
        else decode_ArtifactRef(m.historical_proof),
    )


def encode_HostFence(v: values.HostFence) -> pb.HostFence:
    return pb.HostFence(host_id=encode_HostId(v.host_id), epoch=str(v.epoch))


def decode_HostFence(m: pb.HostFence) -> values.HostFence:
    return values.HostFence(host_id=decode_HostId(_required(m.host_id)), epoch=int(m.epoch))


def encode_EventCursor(v: values.EventCursor) -> pb.EventCursor:
    return pb.EventCursor(sequence=str(v.sequence))


def decode_EventCursor(m: pb.EventCursor) -> values.EventCursor:
    return values.EventCursor(sequence=int(m.sequence))


def encode_WorkspaceRef(v: values.WorkspaceRef) -> pb.WorkspaceRef:
    return pb.WorkspaceRef(
        scope=encode_Scope(v.scope),
        revision=encode_RevisionRef(v.revision),
        mode=encode_WorkspaceMode(v.mode),
    )


def decode_WorkspaceRef(m: pb.WorkspaceRef) -> values.WorkspaceRef:
    return values.WorkspaceRef(
        scope=decode_Scope(_required(m.scope)),
        revision=decode_RevisionRef(_required(m.revision)),
        mode=decode_WorkspaceMode(m.mode),
    )


def encode_ReservedInputOccurrence(v: values.ReservedInputOccurrence) -> pb.ReservedInputOccurrence:
    return pb.ReservedInputOccurrence(
        input_id=encode_InputId(v.input_id), artifact=encode_ArtifactRef(v.artifact)
    )


def decode_ReservedInputOccurrence(m: pb.ReservedInputOccurrence) -> values.ReservedInputOccurrence:
    return values.ReservedInputOccurrence(
        input_id=decode_InputId(_required(m.input_id)),
        artifact=decode_ArtifactRef(_required(m.artifact)),
    )


def encode_Reading(v: values.Reading) -> pb.Reading:
    return pb.Reading(
        metric=v.metric,
        value=v.value,
        direction=encode_Direction(v.direction),
        unit=v.unit,
        repetitions=str(v.repetitions),
    )


def decode_Reading(m: pb.Reading) -> values.Reading:
    return values.Reading(
        metric=m.metric,
        value=m.value,
        direction=None if m.direction == 0 else decode_Direction(m.direction),
        unit=m.unit if m.has_field("unit") else None,
        repetitions=int(m.repetitions),
    )


def encode_BaseRef(v: values.BaseRef) -> pb.BaseRef:
    return pb.BaseRef(commit=encode_RevisionRef(v.commit), measured_input=v.measured_input)


def decode_BaseRef(m: pb.BaseRef) -> values.BaseRef:
    return values.BaseRef(
        commit=decode_RevisionRef(_required(m.commit)), measured_input=m.measured_input
    )


def encode_Sample(v: values.Sample) -> pb.Sample:
    return pb.Sample(
        run=encode_RequestId(v.run),
        scope=encode_Scope(v.scope),
        commit=encode_RevisionRef(v.commit),
        measured_input=v.measured_input,
        evaluation=v.evaluation,
        gates=[x for x in v.gates],
        host=v.host,
        dedicated_resources=v.dedicated_resources,
        reading=encode_Reading(v.reading),
        observed_at=v.observed_at,
        base=None if v.base is None else encode_BaseRef(v.base),
    )


def decode_Sample(m: pb.Sample) -> values.Sample:
    return values.Sample(
        run=decode_RequestId(_required(m.run)),
        scope=decode_Scope(_required(m.scope)),
        commit=decode_RevisionRef(_required(m.commit)),
        measured_input=m.measured_input,
        evaluation=m.evaluation,
        gates=tuple(x for x in m.gates),
        host=m.host,
        dedicated_resources=m.dedicated_resources,
        reading=decode_Reading(_required(m.reading)),
        observed_at=m.observed_at,
        base=None if m.base is None else decode_BaseRef(m.base),
    )


def encode_CoreRun(v: values.CoreRun) -> pb.CoreRun:
    return pb.CoreRun(
        request_id=encode_RequestId(v.request_id),
        scope=encode_Scope(v.scope),
        candidate=encode_RequestId(v.candidate),
        candidate_input=v.candidate_input,
        fresh_when_asked=str(v.fresh_when_asked),
    )


def decode_CoreRun(m: pb.CoreRun) -> values.CoreRun:
    return values.CoreRun(
        request_id=decode_RequestId(_required(m.request_id)),
        scope=decode_Scope(_required(m.scope)),
        candidate=decode_RequestId(_required(m.candidate)),
        candidate_input=m.candidate_input,
        fresh_when_asked=int(m.fresh_when_asked),
    )


def encode_BaseClaim(v: values.BaseClaim) -> pb.BaseClaim:
    return pb.BaseClaim(request_id=encode_RequestId(v.request_id), base=encode_BaseRef(v.base))


def decode_BaseClaim(m: pb.BaseClaim) -> values.BaseClaim:
    return values.BaseClaim(
        request_id=decode_RequestId(_required(m.request_id)), base=decode_BaseRef(_required(m.base))
    )


def encode_Runaway(v: values.Runaway) -> pb.Runaway:
    return pb.Runaway(
        scope=encode_Scope(v.scope), evaluations=str(v.evaluations), limit=str(v.limit)
    )


def decode_Runaway(m: pb.Runaway) -> values.Runaway:
    return values.Runaway(
        scope=decode_Scope(_required(m.scope)), evaluations=int(m.evaluations), limit=int(m.limit)
    )


def encode_Comparison(v: values.Comparison) -> pb.Comparison:
    return pb.Comparison(
        scope=encode_Scope(v.scope),
        metric=v.metric,
        unit=v.unit,
        direction=encode_Direction(v.direction),
        candidate=encode_RevisionRef(v.candidate),
        candidate_input=v.candidate_input,
        base=encode_RevisionRef(v.base),
        base_input=v.base_input,
        candidate_value=v.candidate_value,
        base_value=v.base_value,
        ratio=v.ratio,
        noise=v.noise,
        verdict=encode_ComparisonVerdict(v.verdict),
        candidate_samples=str(v.candidate_samples),
        base_samples=str(v.base_samples),
    )


def decode_Comparison(m: pb.Comparison) -> values.Comparison:
    return values.Comparison(
        scope=decode_Scope(_required(m.scope)),
        metric=m.metric,
        unit=m.unit if m.has_field("unit") else None,
        direction=decode_Direction(m.direction),
        candidate=decode_RevisionRef(_required(m.candidate)),
        candidate_input=m.candidate_input,
        base=decode_RevisionRef(_required(m.base)),
        base_input=m.base_input,
        candidate_value=m.candidate_value,
        base_value=m.base_value,
        ratio=m.ratio,
        noise=m.noise,
        verdict=decode_ComparisonVerdict(m.verdict),
        candidate_samples=int(m.candidate_samples),
        base_samples=int(m.base_samples),
    )


def encode_StatedComparison(v: values.StatedComparison) -> pb.StatedComparison:
    return pb.StatedComparison(
        comparison=encode_Comparison(v.comparison), settling=v.settling, answer_until=v.answer_until
    )


def decode_StatedComparison(m: pb.StatedComparison) -> values.StatedComparison:
    return values.StatedComparison(
        comparison=decode_Comparison(_required(m.comparison)),
        settling=m.settling,
        answer_until=m.answer_until if m.has_field("answer_until") else None,
    )


def encode_ComparisonSettled(v: values.ComparisonSettled) -> pb.ComparisonSettled:
    return pb.ComparisonSettled(
        kind=v.kind,
        scope=encode_Scope(v.scope),
        candidate_input=v.candidate_input,
        base_input=v.base_input,
    )


def decode_ComparisonSettled(m: pb.ComparisonSettled) -> values.ComparisonSettled:
    return values.ComparisonSettled(
        kind=cast(Literal["comparison_settled"], m.kind),
        scope=decode_Scope(_required(m.scope)),
        candidate_input=m.candidate_input,
        base_input=m.base_input,
    )


def encode_JobProgress(v: values.JobProgress) -> pb.JobProgress:
    return pb.JobProgress(
        observation_sequence=str(v.observation_sequence),
        observed_at=v.observed_at,
        state=encode_JobProgressState(v.state),
        stage_id=v.stage_id,
        queued_s=v.queued_s,
        ran_s=v.ran_s,
        pending_reason=v.pending_reason,
        estimated_start_at=v.estimated_start_at,
    )


def decode_JobProgress(m: pb.JobProgress) -> values.JobProgress:
    return values.JobProgress(
        observation_sequence=int(m.observation_sequence),
        observed_at=m.observed_at,
        state=decode_JobProgressState(m.state),
        stage_id=m.stage_id if m.has_field("stage_id") else None,
        queued_s=m.queued_s if m.has_field("queued_s") else None,
        ran_s=m.ran_s if m.has_field("ran_s") else None,
        pending_reason=m.pending_reason if m.has_field("pending_reason") else None,
        estimated_start_at=m.estimated_start_at if m.has_field("estimated_start_at") else None,
    )


def encode_JobTimeout(v: values.JobTimeout) -> pb.JobTimeout:
    return pb.JobTimeout(
        resource_id=encode_ResourceId(v.resource_id),
        progress=None if v.progress is None else encode_JobProgress(v.progress),
        by=encode_WaitEnd(v.by),
        at=v.at,
    )


def decode_JobTimeout(m: pb.JobTimeout) -> values.JobTimeout:
    return values.JobTimeout(
        resource_id=decode_ResourceId(_required(m.resource_id)),
        progress=None if m.progress is None else decode_JobProgress(m.progress),
        by=decode_WaitEnd(m.by),
        at=m.at,
    )


def encode_TimedOut(v: values.TimedOut) -> pb.TimedOut:
    return pb.TimedOut(
        kind=v.kind,
        deadline_at=v.deadline_at,
        reached_at=v.reached_at,
        by=encode_WaitEnd(v.by),
        unfinished=[encode_JobTimeout(x) for x in v.unfinished],
    )


def decode_TimedOut(m: pb.TimedOut) -> values.TimedOut:
    return values.TimedOut(
        kind=cast(Literal["timed_out"], m.kind),
        deadline_at=m.deadline_at,
        reached_at=m.reached_at,
        by=decode_WaitEnd(m.by),
        unfinished=tuple(decode_JobTimeout(x) for x in m.unfinished),
    )


def encode_WaitSubject(v: values.WaitSubject) -> pb.WaitSubject:
    return pb.WaitSubject(kind=encode_WaitKind(v.kind), identity=v.identity)


def decode_WaitSubject(m: pb.WaitSubject) -> values.WaitSubject:
    return values.WaitSubject(kind=decode_WaitKind(m.kind), identity=m.identity)


def encode_WaitOn(v: values.WaitOn) -> pb.WaitOn:
    return pb.WaitOn(
        requests=[encode_RequestId(x) for x in v.requests],
        subjects=[encode_WaitSubject(x) for x in v.subjects],
        timer=v.timer,
    )


def decode_WaitOn(m: pb.WaitOn) -> values.WaitOn:
    return values.WaitOn(
        requests=tuple(decode_RequestId(x) for x in m.requests),
        subjects=tuple(decode_WaitSubject(x) for x in m.subjects),
        timer=m.timer if m.has_field("timer") else None,
    )


def encode_Backoff(v: values.Backoff) -> pb.Backoff:
    return pb.Backoff(first=v.first, cap=v.cap)


def decode_Backoff(m: pb.Backoff) -> values.Backoff:
    return values.Backoff(first=m.first, cap=m.cap)


def encode_Exchanges(v: values.Exchanges) -> pb.Exchanges:
    return pb.Exchanges(correction=v.correction, resume=v.resume, wait_unrecorded=v.wait_unrecorded)


def decode_Exchanges(m: pb.Exchanges) -> values.Exchanges:
    return values.Exchanges(
        correction=m.correction if m.has_field("correction") else None,
        resume=m.resume if m.has_field("resume") else None,
        wait_unrecorded=m.wait_unrecorded if m.has_field("wait_unrecorded") else None,
    )


def encode_WaitUnrecorded(v: values.WaitUnrecorded) -> pb.WaitUnrecorded:
    return pb.WaitUnrecorded(kind=v.kind, role=v.role)


def decode_WaitUnrecorded(m: pb.WaitUnrecorded) -> values.WaitUnrecorded:
    return values.WaitUnrecorded(kind=cast(Literal["wait-unrecorded"], m.kind), role=m.role)


def encode_Retained(v: values.Retained) -> pb.Retained:
    return pb.Retained(kind=v.kind, revision=encode_RevisionRef(v.revision))


def decode_Retained(m: pb.Retained) -> values.Retained:
    return values.Retained(
        kind=cast(Literal["retained"], m.kind), revision=decode_RevisionRef(_required(m.revision))
    )


def encode_Declined(v: values.Declined) -> pb.Declined:
    return pb.Declined(kind=v.kind, reason=encode_CheckpointDecline(v.reason))


def decode_Declined(m: pb.Declined) -> values.Declined:
    return values.Declined(
        kind=cast(Literal["declined"], m.kind), reason=decode_CheckpointDecline(m.reason)
    )


def encode_WhileOpen(v: values.WhileOpen) -> pb.WhileOpen:
    return pb.WhileOpen(kind=v.kind, workspace=encode_AttemptRef(v.workspace))


def decode_WhileOpen(m: pb.WhileOpen) -> values.WhileOpen:
    return values.WhileOpen(
        kind=cast(Literal["while_open"], m.kind),
        workspace=decode_AttemptRef(_required(m.workspace)),
    )


def encode_WhileAlive(v: values.WhileAlive) -> pb.WhileAlive:
    return pb.WhileAlive(kind=v.kind, job=encode_ResourceId(v.job))


def decode_WhileAlive(m: pb.WhileAlive) -> values.WhileAlive:
    return values.WhileAlive(
        kind=cast(Literal["while_alive"], m.kind), job=decode_ResourceId(_required(m.job))
    )


def encode_Within(v: values.Within) -> pb.Within:
    return pb.Within(limit=v.limit, watches=[encode_Watch(x) for x in v.watches])


def decode_Within(m: pb.Within) -> values.Within:
    return values.Within(limit=m.limit, watches=tuple(decode_Watch(x) for x in m.watches))


def encode_FromDispatch(v: values.FromDispatch) -> pb.FromDispatch:
    return pb.FromDispatch(kind=v.kind, limit=v.limit, watches=[encode_Watch(x) for x in v.watches])


def decode_FromDispatch(m: pb.FromDispatch) -> values.FromDispatch:
    return values.FromDispatch(
        kind=cast(Literal["from_dispatch"], m.kind),
        limit=m.limit,
        watches=tuple(decode_Watch(x) for x in m.watches),
    )


def encode_Inherited(v: values.Inherited) -> pb.Inherited:
    return pb.Inherited(kind=v.kind)


def decode_Inherited(m: pb.Inherited) -> values.Inherited:
    return values.Inherited(kind=cast(Literal["inherited"], m.kind))


def encode_Until(v: values.Until) -> pb.Until:
    return pb.Until(at=v.at, watches=[encode_Watch(x) for x in v.watches])


def decode_Until(m: pb.Until) -> values.Until:
    return values.Until(at=m.at, watches=tuple(decode_Watch(x) for x in m.watches))


def encode_EvaluationStageResult(v: values.EvaluationStageResult) -> pb.EvaluationStageResult:
    return pb.EvaluationStageResult(
        stage_id=v.stage_id, outcome=encode_EvaluationStageOutcome(v.outcome)
    )


def decode_EvaluationStageResult(m: pb.EvaluationStageResult) -> values.EvaluationStageResult:
    return values.EvaluationStageResult(
        stage_id=m.stage_id, outcome=decode_EvaluationStageOutcome(m.outcome)
    )


def encode_BenchmarkFailure(v: values.BenchmarkFailure) -> pb.BenchmarkFailure:
    return pb.BenchmarkFailure(
        partial_rate=v.partial_rate, rate_lower=v.rate_lower, rate_upper=v.rate_upper
    )


def decode_BenchmarkFailure(m: pb.BenchmarkFailure) -> values.BenchmarkFailure:
    return values.BenchmarkFailure(
        partial_rate=m.partial_rate, rate_lower=m.rate_lower, rate_upper=m.rate_upper
    )


def encode_EvaluationHistoryCursor(v: values.EvaluationHistoryCursor) -> pb.EvaluationHistoryCursor:
    return pb.EvaluationHistoryCursor(
        ordinal=str(v.ordinal),
        submission_id=None if v.submission_id is None else encode_RequestId(v.submission_id),
    )


def decode_EvaluationHistoryCursor(m: pb.EvaluationHistoryCursor) -> values.EvaluationHistoryCursor:
    return values.EvaluationHistoryCursor(
        ordinal=int(m.ordinal),
        submission_id=None if m.submission_id is None else decode_RequestId(m.submission_id),
    )


def encode_Watch(v: values.WhileOpen | values.WhileAlive) -> pb.Watch:
    if type(v) is values.WhileOpen:
        return pb.Watch(value=Oneof("whileopen", encode_WhileOpen(v)))
    if type(v) is values.WhileAlive:
        return pb.Watch(value=Oneof("whilealive", encode_WhileAlive(v)))
    raise TypeError("Invalid Watch member: " + type(v).__name__)


def decode_Watch(m: pb.Watch) -> values.WhileOpen | values.WhileAlive:
    match m.value:
        case Oneof("whileopen", value):
            return decode_WhileOpen(value)
        case Oneof("whilealive", value):
            return decode_WhileAlive(value)
        case _:
            raise ValueError("Missing Watch member")


def encode_Checkpoint(v: values.Retained | values.Declined) -> pb.Checkpoint:
    if type(v) is values.Retained:
        return pb.Checkpoint(value=Oneof("retained", encode_Retained(v)))
    if type(v) is values.Declined:
        return pb.Checkpoint(value=Oneof("declined", encode_Declined(v)))
    raise TypeError("Invalid Checkpoint member: " + type(v).__name__)


def decode_Checkpoint(m: pb.Checkpoint) -> values.Retained | values.Declined:
    match m.value:
        case Oneof("retained", value):
            return decode_Retained(value)
        case Oneof("declined", value):
            return decode_Declined(value)
        case _:
            raise ValueError("Missing Checkpoint member")


ENCODERS: dict[type, Callable[..., Message]] = {
    values.EvidenceKey: encode_EvidenceKey,
    values.RevisionRef: encode_RevisionRef,
    values.AttemptRef: encode_AttemptRef,
    values.OperationRef: encode_OperationRef,
    values.DependencyRef: encode_DependencyRef,
    values.ScopeReopenNormalization: encode_ScopeReopenNormalization,
    values.TaskStage: encode_TaskStage,
    values.TaskObjective: encode_TaskObjective,
    values.ChildManifest: encode_ChildManifest,
    values.Observation: encode_Observation,
    values.ChargeReceipt: encode_ChargeReceipt,
    values.HostFence: encode_HostFence,
    values.EventCursor: encode_EventCursor,
    values.WorkspaceRef: encode_WorkspaceRef,
    values.ReservedInputOccurrence: encode_ReservedInputOccurrence,
    values.Reading: encode_Reading,
    values.BaseRef: encode_BaseRef,
    values.Sample: encode_Sample,
    values.CoreRun: encode_CoreRun,
    values.BaseClaim: encode_BaseClaim,
    values.Runaway: encode_Runaway,
    values.Comparison: encode_Comparison,
    values.StatedComparison: encode_StatedComparison,
    values.ComparisonSettled: encode_ComparisonSettled,
    values.JobProgress: encode_JobProgress,
    values.JobTimeout: encode_JobTimeout,
    values.TimedOut: encode_TimedOut,
    values.WaitSubject: encode_WaitSubject,
    values.WaitOn: encode_WaitOn,
    values.Backoff: encode_Backoff,
    values.Exchanges: encode_Exchanges,
    values.WaitUnrecorded: encode_WaitUnrecorded,
    values.Retained: encode_Retained,
    values.Declined: encode_Declined,
    values.WhileOpen: encode_WhileOpen,
    values.WhileAlive: encode_WhileAlive,
    values.Within: encode_Within,
    values.FromDispatch: encode_FromDispatch,
    values.Inherited: encode_Inherited,
    values.Until: encode_Until,
    values.EvaluationStageResult: encode_EvaluationStageResult,
    values.BenchmarkFailure: encode_BenchmarkFailure,
    values.EvaluationHistoryCursor: encode_EvaluationHistoryCursor,
}
DECODERS: dict[type, Callable[..., object]] = {
    pb.EvidenceKey: decode_EvidenceKey,
    pb.RevisionRef: decode_RevisionRef,
    pb.AttemptRef: decode_AttemptRef,
    pb.OperationRef: decode_OperationRef,
    pb.DependencyRef: decode_DependencyRef,
    pb.ScopeReopenNormalization: decode_ScopeReopenNormalization,
    pb.TaskStage: decode_TaskStage,
    pb.TaskObjective: decode_TaskObjective,
    pb.ChildManifest: decode_ChildManifest,
    pb.Observation: decode_Observation,
    pb.ChargeReceipt: decode_ChargeReceipt,
    pb.HostFence: decode_HostFence,
    pb.EventCursor: decode_EventCursor,
    pb.WorkspaceRef: decode_WorkspaceRef,
    pb.ReservedInputOccurrence: decode_ReservedInputOccurrence,
    pb.Reading: decode_Reading,
    pb.BaseRef: decode_BaseRef,
    pb.Sample: decode_Sample,
    pb.CoreRun: decode_CoreRun,
    pb.BaseClaim: decode_BaseClaim,
    pb.Runaway: decode_Runaway,
    pb.Comparison: decode_Comparison,
    pb.StatedComparison: decode_StatedComparison,
    pb.ComparisonSettled: decode_ComparisonSettled,
    pb.JobProgress: decode_JobProgress,
    pb.JobTimeout: decode_JobTimeout,
    pb.TimedOut: decode_TimedOut,
    pb.WaitSubject: decode_WaitSubject,
    pb.WaitOn: decode_WaitOn,
    pb.Backoff: decode_Backoff,
    pb.Exchanges: decode_Exchanges,
    pb.WaitUnrecorded: decode_WaitUnrecorded,
    pb.Retained: decode_Retained,
    pb.Declined: decode_Declined,
    pb.WhileOpen: decode_WhileOpen,
    pb.WhileAlive: decode_WhileAlive,
    pb.Within: decode_Within,
    pb.FromDispatch: decode_FromDispatch,
    pb.Inherited: decode_Inherited,
    pb.Until: decode_Until,
    pb.EvaluationStageResult: decode_EvaluationStageResult,
    pb.BenchmarkFailure: decode_BenchmarkFailure,
    pb.EvaluationHistoryCursor: decode_EvaluationHistoryCursor,
    pb.Watch: decode_Watch,
    pb.Checkpoint: decode_Checkpoint,
}
