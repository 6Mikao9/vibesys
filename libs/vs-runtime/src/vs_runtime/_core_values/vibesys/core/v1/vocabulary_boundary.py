# Generated from vibesys/core/v1/vocabulary.proto by protoc-gen-corevalue. DO NOT EDIT.
from __future__ import annotations

from typing import Callable, Literal, cast

from protobuf import Message, Oneof

import vs_core.api as values
from vs_proto.api import core_vocabulary as pb
from vs_runtime._corevalue_wkt import decode_timestamp, encode_timestamp


def encode_CheckpointDecline(v: values.CheckpointDecline | None) -> pb.CheckpointDecline:
    return {
        "writer_active": pb.CheckpointDecline.WRITER_ACTIVE,
        "unauthorized": pb.CheckpointDecline.UNAUTHORIZED,
        "uncharged": pb.CheckpointDecline.UNCHARGED,
        "not_active": pb.CheckpointDecline.NOT_ACTIVE,
        "snapshot_failed": pb.CheckpointDecline.SNAPSHOT_FAILED,
    }.get(v, pb.CheckpointDecline.UNSPECIFIED)


def decode_CheckpointDecline(v: pb.CheckpointDecline) -> values.CheckpointDecline:
    return values.CheckpointDecline(
        {
            1: "writer_active",
            2: "unauthorized",
            3: "uncharged",
            4: "not_active",
            5: "snapshot_failed",
        }[v]
    )


def encode_TurnFailureKind(v: values.TurnFailureKind | None) -> pb.TurnFailureKind:
    return {
        "provider_failed": pb.TurnFailureKind.PROVIDER_FAILED,
        "transport_lost": pb.TurnFailureKind.TRANSPORT_LOST,
        "cancelled": pb.TurnFailureKind.CANCELLED,
        "provider_unavailable": pb.TurnFailureKind.PROVIDER_UNAVAILABLE,
    }.get(v, pb.TurnFailureKind.UNSPECIFIED)


def decode_TurnFailureKind(v: pb.TurnFailureKind) -> values.TurnFailureKind:
    return values.TurnFailureKind(
        {1: "provider_failed", 2: "transport_lost", 3: "cancelled", 4: "provider_unavailable"}[v]
    )


def encode_DigestScheme(v: values.DigestScheme | None) -> pb.DigestScheme:
    return {"git-commit": pb.DigestScheme.GIT_COMMIT}.get(v, pb.DigestScheme.UNSPECIFIED)


def decode_DigestScheme(v: pb.DigestScheme) -> values.DigestScheme:
    return values.DigestScheme({1: "git-commit"}[v])


def encode_LifecycleClass(v: values.LifecycleClass | None) -> pb.LifecycleClass:
    return {
        "query": pb.LifecycleClass.QUERY,
        "idempotent_external_write": pb.LifecycleClass.IDEMPOTENT_WRITE,
        "owned_long_running_job": pb.LifecycleClass.OWNED_JOB,
        "session_turn": pb.LifecycleClass.SESSION_TURN,
    }.get(v, pb.LifecycleClass.UNSPECIFIED)


def decode_LifecycleClass(v: pb.LifecycleClass) -> values.LifecycleClass:
    return values.LifecycleClass(
        {
            1: "query",
            2: "idempotent_external_write",
            3: "owned_long_running_job",
            4: "session_turn",
        }[v]
    )


def encode_EvidenceKind(v: values.EvidenceKind | None) -> pb.EvidenceKind:
    return {
        "correctness": pb.EvidenceKind.CORRECTNESS,
        "benchmark": pb.EvidenceKind.BENCHMARK,
        "profiling": pb.EvidenceKind.PROFILING,
    }.get(v, pb.EvidenceKind.UNSPECIFIED)


def decode_EvidenceKind(v: pb.EvidenceKind) -> values.EvidenceKind:
    return values.EvidenceKind({1: "correctness", 2: "benchmark", 3: "profiling"}[v])


def encode_AssessmentKind(v: values.AssessmentKind | None) -> pb.AssessmentKind:
    return {
        "correctness": pb.AssessmentKind.CORRECTNESS,
        "benchmark": pb.AssessmentKind.BENCHMARK,
        "profiling": pb.AssessmentKind.PROFILING,
        "local-validation": pb.AssessmentKind.LOCAL_VALIDATION,
    }.get(v, pb.AssessmentKind.UNSPECIFIED)


def decode_AssessmentKind(v: pb.AssessmentKind) -> values.AssessmentKind:
    return values.AssessmentKind(
        {1: "correctness", 2: "benchmark", 3: "profiling", 4: "local-validation"}[v]
    )


def encode_ObservationStatus(v: values.ObservationStatus | None) -> pb.ObservationStatus:
    return {
        "succeeded": pb.ObservationStatus.SUCCEEDED,
        "pending": pb.ObservationStatus.PENDING,
        "cancelled": pb.ObservationStatus.CANCELLED,
        "rejected": pb.ObservationStatus.REJECTED,
        "failed": pb.ObservationStatus.FAILED,
        "unknown": pb.ObservationStatus.UNKNOWN,
    }.get(v, pb.ObservationStatus.UNSPECIFIED)


def decode_ObservationStatus(v: pb.ObservationStatus) -> values.ObservationStatus:
    return values.ObservationStatus(
        {1: "succeeded", 2: "pending", 3: "cancelled", 4: "rejected", 5: "failed", 6: "unknown"}[v]
    )


def encode_RejectionCode(v: values.RejectionCode | None) -> pb.RejectionCode:
    return {
        "stale-view": pb.RejectionCode.STALE_VIEW,
        "identity-conflict": pb.RejectionCode.IDENTITY_CONFLICT,
        "unknown-kind": pb.RejectionCode.UNKNOWN_KIND,
        "unknown-schema": pb.RejectionCode.UNKNOWN_SCHEMA,
        "undeclared-operation": pb.RejectionCode.UNDECLARED_OPERATION,
        "capability": pb.RejectionCode.CAPABILITY,
        "ownership": pb.RejectionCode.OWNERSHIP,
        "generation": pb.RejectionCode.GENERATION,
        "closed-scope": pb.RejectionCode.CLOSED_SCOPE,
        "budget": pb.RejectionCode.BUDGET,
        "dependency": pb.RejectionCode.DEPENDENCY,
        "evidence": pb.RejectionCode.EVIDENCE,
        "already-settled": pb.RejectionCode.ALREADY_SETTLED,
        "not-implemented-in-kernel": pb.RejectionCode.NOT_IMPLEMENTED_IN_KERNEL,
    }.get(v, pb.RejectionCode.UNSPECIFIED)


def decode_RejectionCode(v: pb.RejectionCode) -> values.RejectionCode:
    return values.RejectionCode(
        {
            1: "stale-view",
            2: "identity-conflict",
            3: "unknown-kind",
            4: "unknown-schema",
            5: "undeclared-operation",
            6: "capability",
            7: "ownership",
            8: "generation",
            9: "closed-scope",
            10: "budget",
            11: "dependency",
            12: "evidence",
            13: "already-settled",
            14: "not-implemented-in-kernel",
        }[v]
    )


def encode_Area(v: values.Area | None) -> pb.Area:
    return {
        "scheduling": pb.Area.SCHEDULING,
        "attempts": pb.Area.ATTEMPTS,
        "sessions": pb.Area.SESSIONS,
        "evaluation": pb.Area.EVALUATION,
        "settlement": pb.Area.SETTLEMENT,
        "records": pb.Area.RECORDS,
    }.get(v, pb.Area.UNSPECIFIED)


def decode_Area(v: pb.Area) -> values.Area:
    return values.Area(
        {
            1: "scheduling",
            2: "attempts",
            3: "sessions",
            4: "evaluation",
            5: "settlement",
            6: "records",
        }[v]
    )


def encode_RevisionAuthority(v: values.RevisionAuthority | None) -> pb.RevisionAuthority:
    return {
        "none": pb.RevisionAuthority.NONE,
        "snapshot": pb.RevisionAuthority.SNAPSHOT,
        "retain": pb.RevisionAuthority.RETAIN,
        "restore": pb.RevisionAuthority.RESTORE,
        "discard": pb.RevisionAuthority.DISCARD,
    }.get(v, pb.RevisionAuthority.UNSPECIFIED)


def decode_RevisionAuthority(v: pb.RevisionAuthority) -> values.RevisionAuthority:
    return values.RevisionAuthority(
        {1: "none", 2: "snapshot", 3: "retain", 4: "restore", 5: "discard"}[v]
    )


def encode_OperationNormalizationKind(
    v: values.OperationNormalizationKind | None,
) -> pb.OperationNormalizationKind:
    return {
        "none": pb.OperationNormalizationKind.NONE,
        "scope_reopen": pb.OperationNormalizationKind.SCOPE_REOPEN,
    }.get(v, pb.OperationNormalizationKind.UNSPECIFIED)


def decode_OperationNormalizationKind(
    v: pb.OperationNormalizationKind,
) -> values.OperationNormalizationKind:
    return values.OperationNormalizationKind({1: "none", 2: "scope_reopen"}[v])


def encode_CompletionStatus(v: values.CompletionStatus | None) -> pb.CompletionStatus:
    return {
        "succeeded": pb.CompletionStatus.SUCCEEDED,
        "failed": pb.CompletionStatus.FAILED,
        "cancelled": pb.CompletionStatus.CANCELLED,
    }.get(v, pb.CompletionStatus.UNSPECIFIED)


def decode_CompletionStatus(v: pb.CompletionStatus) -> values.CompletionStatus:
    return values.CompletionStatus({1: "succeeded", 2: "failed", 3: "cancelled"}[v])


def encode_DependencyStatus(v: values.DependencyStatus | None) -> pb.DependencyStatus:
    return {
        "pending": pb.DependencyStatus.PENDING,
        "succeeded": pb.DependencyStatus.SUCCEEDED,
        "failed": pb.DependencyStatus.FAILED,
    }.get(v, pb.DependencyStatus.UNSPECIFIED)


def decode_DependencyStatus(v: pb.DependencyStatus) -> values.DependencyStatus:
    return values.DependencyStatus({1: "pending", 2: "succeeded", 3: "failed"}[v])


def encode_ChargeKind(v: values.ChargeKind | None) -> pb.ChargeKind:
    return {
        "admission": pb.ChargeKind.ADMISSION,
        "attempt": pb.ChargeKind.ATTEMPT,
        "turn": pb.ChargeKind.TURN,
    }.get(v, pb.ChargeKind.UNSPECIFIED)


def decode_ChargeKind(v: pb.ChargeKind) -> values.ChargeKind:
    return values.ChargeKind({1: "admission", 2: "attempt", 3: "turn"}[v])


def encode_SetupFailureKind(v: values.SetupFailureKind | None) -> pb.SetupFailureKind:
    return {
        "transient": pb.SetupFailureKind.TRANSIENT,
        "permanent": pb.SetupFailureKind.PERMANENT,
        "unsupported": pb.SetupFailureKind.UNSUPPORTED,
        "unknown": pb.SetupFailureKind.UNKNOWN,
    }.get(v, pb.SetupFailureKind.UNSPECIFIED)


def decode_SetupFailureKind(v: pb.SetupFailureKind) -> values.SetupFailureKind:
    return values.SetupFailureKind(
        {1: "transient", 2: "permanent", 3: "unsupported", 4: "unknown"}[v]
    )


def encode_WorkspaceMode(v: values.WorkspaceMode | None) -> pb.WorkspaceMode:
    return {
        "exclusive-root": pb.WorkspaceMode.EXCLUSIVE_ROOT,
        "isolated-child": pb.WorkspaceMode.ISOLATED_CHILD,
        "read-only-revision": pb.WorkspaceMode.READ_ONLY_REVISION,
    }.get(v, pb.WorkspaceMode.UNSPECIFIED)


def decode_WorkspaceMode(v: pb.WorkspaceMode) -> values.WorkspaceMode:
    return values.WorkspaceMode(
        {1: "exclusive-root", 2: "isolated-child", 3: "read-only-revision"}[v]
    )


def encode_WorkKind(v: values.WorkKind | None) -> pb.WorkKind:
    return {
        "request_turn": pb.WorkKind.REQUEST_TURN,
        "start_attempt": pb.WorkKind.START_ATTEMPT,
        "withdraw": pb.WorkKind.WITHDRAW,
        "measure": pb.WorkKind.MEASURE,
        "propose_winner": pb.WorkKind.PROPOSE_WINNER,
        "operation": pb.WorkKind.OPERATION,
        "wake_at": pb.WorkKind.WAKE_AT,
    }.get(v, pb.WorkKind.UNSPECIFIED)


def decode_WorkKind(v: pb.WorkKind) -> values.WorkKind:
    return values.WorkKind(
        {
            1: "request_turn",
            2: "start_attempt",
            3: "withdraw",
            4: "measure",
            5: "propose_winner",
            6: "operation",
            7: "wake_at",
        }[v]
    )


def encode_EventKind(v: values.EventKind | None) -> pb.EventKind:
    return {
        "workspace_opened": pb.EventKind.WORKSPACE_OPENED,
        "workspace_closed": pb.EventKind.WORKSPACE_CLOSED,
        "turn_finished": pb.EventKind.TURN_FINISHED,
        "measurement_finished": pb.EventKind.MEASUREMENT_FINISHED,
        "operation_finished": pb.EventKind.OPERATION_FINISHED,
        "adoption_finished": pb.EventKind.ADOPTION_FINISHED,
        "stop_requested": pb.EventKind.STOP_REQUESTED,
        "stop_lifted": pb.EventKind.STOP_LIFTED,
        "time_reached": pb.EventKind.TIME_REACHED,
        "failed": pb.EventKind.FAILED,
        "message_delivered": pb.EventKind.MESSAGE_DELIVERED,
        "message_dropped": pb.EventKind.MESSAGE_DROPPED,
        "interrupted": pb.EventKind.INTERRUPTED,
        "turn_ended": pb.EventKind.TURN_ENDED,
        "turn_yielded": pb.EventKind.TURN_YIELDED,
        "yield_failed": pb.EventKind.YIELD_FAILED,
        "resume_ready": pb.EventKind.RESUME_READY,
        "resume_failed": pb.EventKind.RESUME_FAILED,
        "checkpoint_known": pb.EventKind.CHECKPOINT_KNOWN,
        "comparison_settled": pb.EventKind.COMPARISON_SETTLED,
    }.get(v, pb.EventKind.UNSPECIFIED)


def decode_EventKind(v: pb.EventKind) -> values.EventKind:
    return values.EventKind(
        {
            1: "workspace_opened",
            2: "workspace_closed",
            3: "turn_finished",
            4: "measurement_finished",
            5: "operation_finished",
            6: "adoption_finished",
            7: "stop_requested",
            8: "stop_lifted",
            9: "time_reached",
            10: "failed",
            11: "message_delivered",
            12: "message_dropped",
            13: "interrupted",
            14: "turn_ended",
            15: "turn_yielded",
            16: "yield_failed",
            17: "resume_ready",
            18: "resume_failed",
            19: "checkpoint_known",
            20: "comparison_settled",
        }[v]
    )


def encode_ComparisonVerdict(v: values.ComparisonVerdict | None) -> pb.ComparisonVerdict:
    return {
        "better": pb.ComparisonVerdict.BETTER,
        "within_noise": pb.ComparisonVerdict.WITHIN_NOISE,
        "worse": pb.ComparisonVerdict.WORSE,
        "inconclusive": pb.ComparisonVerdict.INCONCLUSIVE,
    }.get(v, pb.ComparisonVerdict.UNSPECIFIED)


def decode_ComparisonVerdict(v: pb.ComparisonVerdict) -> values.ComparisonVerdict:
    return values.ComparisonVerdict(
        {1: "better", 2: "within_noise", 3: "worse", 4: "inconclusive"}[v]
    )


def encode_EffectVerdict(v: values.EffectVerdict | None) -> pb.EffectVerdict:
    return {
        "supported": pb.EffectVerdict.SUPPORTED,
        "within_noise": pb.EffectVerdict.WITHIN_NOISE,
        "disproven": pb.EffectVerdict.DISPROVEN,
        "inconclusive": pb.EffectVerdict.INCONCLUSIVE,
    }.get(v, pb.EffectVerdict.UNSPECIFIED)


def decode_EffectVerdict(v: pb.EffectVerdict) -> values.EffectVerdict:
    return values.EffectVerdict(
        {1: "supported", 2: "within_noise", 3: "disproven", 4: "inconclusive"}[v]
    )


def encode_WaitEnd(v: values.WaitEnd | None) -> pb.WaitEnd:
    return {
        "limit": pb.WaitEnd.LIMIT,
        "queue_budget": pb.WaitEnd.QUEUE_BUDGET,
        "run_end": pb.WaitEnd.RUN_END,
    }.get(v, pb.WaitEnd.UNSPECIFIED)


def decode_WaitEnd(v: pb.WaitEnd) -> values.WaitEnd:
    return values.WaitEnd({1: "limit", 2: "queue_budget", 3: "run_end"}[v])


def encode_MeasurementFailure(v: values.MeasurementFailure | None) -> pb.MeasurementFailure:
    return {
        "workload": pb.MeasurementFailure.WORKLOAD,
        "infrastructure": pb.MeasurementFailure.INFRASTRUCTURE,
        "ambiguous": pb.MeasurementFailure.AMBIGUOUS,
        "unknown": pb.MeasurementFailure.UNKNOWN,
        "run_environment_unavailable": pb.MeasurementFailure.RUN_ENVIRONMENT_UNAVAILABLE,
    }.get(v, pb.MeasurementFailure.UNSPECIFIED)


def decode_MeasurementFailure(v: pb.MeasurementFailure) -> values.MeasurementFailure:
    return values.MeasurementFailure(
        {
            1: "workload",
            2: "infrastructure",
            3: "ambiguous",
            4: "unknown",
            5: "run_environment_unavailable",
        }[v]
    )


def encode_WaitKind(v: values.WaitKind | None) -> pb.WaitKind:
    return {
        "session": pb.WaitKind.SESSION,
        "recovery-check": pb.WaitKind.RECOVERY_CHECK,
        "owned-job": pb.WaitKind.OWNED_JOB,
        "release-dependency": pb.WaitKind.RELEASE_DEPENDENCY,
        "yield-publication": pb.WaitKind.YIELD_PUBLICATION,
        "interrupt": pb.WaitKind.INTERRUPT,
        "refund": pb.WaitKind.REFUND,
        "dispatch": pb.WaitKind.DISPATCH,
        "retry": pb.WaitKind.RETRY,
        "turn-acquisition": pb.WaitKind.TURN_ACQUISITION,
        "blocked-record": pb.WaitKind.BLOCKED_RECORD,
    }.get(v, pb.WaitKind.UNSPECIFIED)


def decode_WaitKind(v: pb.WaitKind) -> values.WaitKind:
    return values.WaitKind(
        {
            1: "session",
            2: "recovery-check",
            3: "owned-job",
            4: "release-dependency",
            5: "yield-publication",
            6: "interrupt",
            7: "refund",
            8: "dispatch",
            9: "retry",
            10: "turn-acquisition",
            11: "blocked-record",
        }[v]
    )


def encode_Charge(v: values.Charge | None) -> pb.Charge:
    return {"paid": pb.Charge.ATTEMPT, "free": pb.Charge.TURN}.get(v, pb.Charge.UNSPECIFIED)


def decode_Charge(v: pb.Charge) -> values.Charge:
    return values.Charge({1: "paid", 2: "free"}[v])


def encode_Correctable(v: values.Correctable | None) -> pb.Correctable:
    return {
        "invalid-reply": pb.Correctable.INVALID_REPLY,
        "unrecorded-wait": pb.Correctable.UNRECORDED_WAIT,
    }.get(v, pb.Correctable.UNSPECIFIED)


def decode_Correctable(v: pb.Correctable) -> values.Correctable:
    return values.Correctable({1: "invalid-reply", 2: "unrecorded-wait"}[v])


def encode_FailureReason(v: values.FailureReason | None) -> pb.FailureReason:
    return {
        "timed-out": pb.FailureReason.TIMED_OUT,
        "workspace-closed": pb.FailureReason.WORKSPACE_CLOSED,
        "owner-gone": pb.FailureReason.OWNER_GONE,
        "refused": pb.FailureReason.REFUSED,
        "lost": pb.FailureReason.LOST,
        "invalid-reply": pb.FailureReason.INVALID_REPLY,
        "render-failed": pb.FailureReason.RENDER_FAILED,
        "turn-failed": pb.FailureReason.TURN_FAILED,
        "wait-refused": pb.FailureReason.WAIT_REFUSED,
        "queue-budget-spent": pb.FailureReason.QUEUE_BUDGET_SPENT,
        "out-of-budget": pb.FailureReason.OUT_OF_BUDGET,
        "measurement-untrusted": pb.FailureReason.MEASUREMENT_UNTRUSTED,
    }.get(v, pb.FailureReason.UNSPECIFIED)


def decode_FailureReason(v: pb.FailureReason) -> values.FailureReason:
    return values.FailureReason(
        {
            1: "timed-out",
            2: "workspace-closed",
            3: "owner-gone",
            4: "refused",
            5: "lost",
            6: "invalid-reply",
            7: "render-failed",
            8: "turn-failed",
            9: "wait-refused",
            10: "queue-budget-spent",
            11: "out-of-budget",
            12: "measurement-untrusted",
        }[v]
    )


def encode_WaitRefusal(v: values.WaitRefusal | None) -> pb.WaitRefusal:
    return {
        "not-offered": pb.WaitRefusal.NOT_OFFERED,
        "not-recorded": pb.WaitRefusal.NOT_RECORDED,
        "not-published": pb.WaitRefusal.NOT_PUBLISHED,
        "not-resumable": pb.WaitRefusal.NOT_RESUMABLE,
    }.get(v, pb.WaitRefusal.UNSPECIFIED)


def decode_WaitRefusal(v: pb.WaitRefusal) -> values.WaitRefusal:
    return values.WaitRefusal(
        {1: "not-offered", 2: "not-recorded", 3: "not-published", 4: "not-resumable"}[v]
    )


def encode_ReplyErrorCode(v: values.ReplyErrorCode | None) -> pb.ReplyErrorCode:
    return {
        "missing": pb.ReplyErrorCode.MISSING,
        "unexpected": pb.ReplyErrorCode.UNEXPECTED,
        "wrong-type": pb.ReplyErrorCode.WRONG_TYPE,
        "invalid-value": pb.ReplyErrorCode.INVALID_VALUE,
        "not-json": pb.ReplyErrorCode.NOT_JSON,
    }.get(v, pb.ReplyErrorCode.UNSPECIFIED)


def decode_ReplyErrorCode(v: pb.ReplyErrorCode) -> values.ReplyErrorCode:
    return values.ReplyErrorCode(
        {1: "missing", 2: "unexpected", 3: "wrong-type", 4: "invalid-value", 5: "not-json"}[v]
    )


def encode_EvaluationHistoryAvailability(
    v: values.EvaluationHistoryAvailability | None,
) -> pb.EvaluationHistoryAvailability:
    return {
        "complete": pb.EvaluationHistoryAvailability.COMPLETE,
        "unavailable": pb.EvaluationHistoryAvailability.UNAVAILABLE,
    }.get(v, pb.EvaluationHistoryAvailability.UNSPECIFIED)


def decode_EvaluationHistoryAvailability(
    v: pb.EvaluationHistoryAvailability,
) -> values.EvaluationHistoryAvailability:
    return values.EvaluationHistoryAvailability({1: "complete", 2: "unavailable"}[v])


def encode_EvaluationStageOutcome(
    v: values.EvaluationStageOutcome | None,
) -> pb.EvaluationStageOutcome:
    return {
        "passed": pb.EvaluationStageOutcome.PASSED,
        "failed": pb.EvaluationStageOutcome.FAILED,
        "unknown": pb.EvaluationStageOutcome.UNKNOWN,
    }.get(v, pb.EvaluationStageOutcome.UNSPECIFIED)


def decode_EvaluationStageOutcome(v: pb.EvaluationStageOutcome) -> values.EvaluationStageOutcome:
    return values.EvaluationStageOutcome({1: "passed", 2: "failed", 3: "unknown"}[v])


def encode_AttemptTerminalReason(
    v: values.AttemptTerminalReason | None,
) -> pb.AttemptTerminalReason:
    return {
        "repeated-traceback": pb.AttemptTerminalReason.REPEATED_TRACEBACK,
        "repeated-measurement": pb.AttemptTerminalReason.REPEATED_MEASUREMENT,
        "history-unavailable": pb.AttemptTerminalReason.HISTORY_UNAVAILABLE,
        "no-new-evaluation": pb.AttemptTerminalReason.NO_NEW_EVALUATION,
    }.get(v, pb.AttemptTerminalReason.UNSPECIFIED)


def decode_AttemptTerminalReason(v: pb.AttemptTerminalReason) -> values.AttemptTerminalReason:
    return values.AttemptTerminalReason(
        {
            1: "repeated-traceback",
            2: "repeated-measurement",
            3: "history-unavailable",
            4: "no-new-evaluation",
        }[v]
    )


def _required[T](value: T | None) -> T:
    if value is None:
        raise ValueError("Required protobuf message is absent")
    return value


ENCODERS: dict[type, Callable[..., Message]] = {}
DECODERS: dict[type, Callable[..., object]] = {}
