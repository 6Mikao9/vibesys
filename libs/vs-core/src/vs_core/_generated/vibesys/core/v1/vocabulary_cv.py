# Generated from vibesys/core/v1/vocabulary.proto by protoc-gen-corevalue. DO NOT EDIT.
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
from vs_core._retry_policy import MeasurementFailurePolicy


class CheckpointDecline(StrEnum):
    "Why an invocation checkpoint was not taken, so a decline is never silent."

    WRITER_ACTIVE = "writer_active"
    UNAUTHORIZED = "unauthorized"
    UNCHARGED = "uncharged"
    NOT_ACTIVE = "not_active"
    SNAPSHOT_FAILED = "snapshot_failed"


class TurnFailureKind(StrEnum):
    "Why a turn ended without a usable reply, as a closed set strategies match on."

    PROVIDER_FAILED = "provider_failed"
    TRANSPORT_LOST = "transport_lost"
    CANCELLED = "cancelled"
    PROVIDER_UNAVAILABLE = "provider_unavailable"


class DigestScheme(StrEnum):
    "Closed set of revision digest schemes; a digest is `<scheme>:<value>`."

    GIT_COMMIT = "git-commit"


class LifecycleClass(StrEnum):
    "Lifecycle class lifecycle contract."

    QUERY = "query"
    IDEMPOTENT_WRITE = "idempotent_external_write"
    OWNED_JOB = "owned_long_running_job"
    SESSION_TURN = "session_turn"


class EvidenceKind(StrEnum):
    "Closed measurement kinds used for eligibility requirements."

    CORRECTNESS = "correctness"
    BENCHMARK = "benchmark"
    PROFILING = "profiling"


class AssessmentKind(StrEnum):
    "Closed semantic assessment kinds, distinct from evidence production."

    CORRECTNESS = "correctness"
    BENCHMARK = "benchmark"
    PROFILING = "profiling"
    LOCAL_VALIDATION = "local-validation"


class ObservationStatus(StrEnum):
    "Observation status lifecycle contract."

    SUCCEEDED = "succeeded"
    PENDING = "pending"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    FAILED = "failed"
    UNKNOWN = "unknown"


class RejectionCode(StrEnum):
    "Rejection code lifecycle contract."

    STALE_VIEW = "stale-view"
    IDENTITY_CONFLICT = "identity-conflict"
    UNKNOWN_KIND = "unknown-kind"
    UNKNOWN_SCHEMA = "unknown-schema"
    UNDECLARED_OPERATION = "undeclared-operation"
    CAPABILITY = "capability"
    OWNERSHIP = "ownership"
    GENERATION = "generation"
    CLOSED_SCOPE = "closed-scope"
    BUDGET = "budget"
    DEPENDENCY = "dependency"
    EVIDENCE = "evidence"
    ALREADY_SETTLED = "already-settled"
    NOT_IMPLEMENTED_IN_KERNEL = "not-implemented-in-kernel"


class Area(StrEnum):
    "Area lifecycle contract."

    SCHEDULING = "scheduling"
    ATTEMPTS = "attempts"
    SESSIONS = "sessions"
    EVALUATION = "evaluation"
    SETTLEMENT = "settlement"
    RECORDS = "records"


class RevisionAuthority(StrEnum):
    "Explicit workspace authority, never inferred from operation kind names."

    NONE = "none"
    SNAPSHOT = "snapshot"
    RETAIN = "retain"
    RESTORE = "restore"
    DISCARD = "discard"


class OperationNormalizationKind(StrEnum):
    "Closed normalization routes; pure normalizers live outside state."

    NONE = "none"
    SCOPE_REOPEN = "scope_reopen"


class CompletionStatus(StrEnum):
    "Terminal semantic decision outcome, independent of dispatch acceptance."

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DependencyStatus(StrEnum):
    "Readiness of durable completion and request dependencies."

    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ChargeKind(StrEnum):
    "Separate currencies with different admission and refund semantics.\n\nADMISSION charges accepted registration including queued starts. ATTEMPT\ncharges a paid invocation or failed setup cycle without an existing paid\ncharge. TURN charges one logical invocation, regardless of max_turns or\ncharge class, and never refunds."

    ADMISSION = "admission"
    ATTEMPT = "attempt"
    TURN = "turn"


class SetupFailureKind(StrEnum):
    "Setup classification; unknown acceptance requires inspection."

    TRANSIENT = "transient"
    PERMANENT = "permanent"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


class WorkspaceMode(StrEnum):
    "Workspace mode lifecycle contract."

    EXCLUSIVE_ROOT = "exclusive-root"
    ISOLATED_CHILD = "isolated-child"
    READ_ONLY_REVISION = "read-only-revision"


class WorkKind(StrEnum):
    "What a ``Wait`` can ask for, plus the clock wake-up."

    REQUEST_TURN = "request_turn"
    START_ATTEMPT = "start_attempt"
    WITHDRAW = "withdraw"
    MEASURE = "measure"
    PROPOSE_WINNER = "propose_winner"
    OPERATION = "operation"
    WAKE_AT = "wake_at"


class EventKind(StrEnum):
    "The kinds of event a search receives, past tense, one per thing that happened."

    WORKSPACE_OPENED = "workspace_opened"
    WORKSPACE_CLOSED = "workspace_closed"
    TURN_FINISHED = "turn_finished"
    MEASUREMENT_FINISHED = "measurement_finished"
    OPERATION_FINISHED = "operation_finished"
    ADOPTION_FINISHED = "adoption_finished"
    STOP_REQUESTED = "stop_requested"
    STOP_LIFTED = "stop_lifted"
    TIME_REACHED = "time_reached"
    FAILED = "failed"
    MESSAGE_DELIVERED = "message_delivered"
    MESSAGE_DROPPED = "message_dropped"
    INTERRUPTED = "interrupted"
    TURN_ENDED = "turn_ended"
    TURN_YIELDED = "turn_yielded"
    YIELD_FAILED = "yield_failed"
    RESUME_READY = "resume_ready"
    RESUME_FAILED = "resume_failed"
    CHECKPOINT_KNOWN = "checkpoint_known"
    COMPARISON_SETTLED = "comparison_settled"


class ComparisonVerdict(StrEnum):
    "Which side of the noise a candidate's result falls on, against its base.\n\n``INCONCLUSIVE`` is no side: the samples held give no estimate of the noise (too few runs a\nside, or runs that all read alike), so the ratio is neither a gain nor within the noise."

    BETTER = "better"
    WITHIN_NOISE = "within_noise"
    WORSE = "worse"
    INCONCLUSIVE = "inconclusive"


class EffectVerdict(StrEnum):
    "What a comparison says of a plan's expected effect, a minimum ratio over the base.\n\nThe framework derives it (`vs_core._comparison.effect_verdict`); no agent states it."

    SUPPORTED = "supported"
    WITHIN_NOISE = "within_noise"
    DISPROVEN = "disproven"
    INCONCLUSIVE = "inconclusive"


class WaitEnd(StrEnum):
    "What ended a wait on a job: every wait ends by exactly one of these, or by a stop.\n\nClosed. The bound of every wait is a time (``_wait_clock.Bound``), never absent; this\nnames the rule that set it."

    LIMIT = "limit"
    QUEUE_BUDGET = "queue_budget"
    RUN_END = "run_end"


class MeasurementFailure(MeasurementFailurePolicy, StrEnum):
    "Submission failure classification; unknown never proves workload rejection.\n\n``AMBIGUOUS`` is a failure the evidence cannot assign to the candidate or to the\nmachinery. The backend decides how often it measures such a failure again, within its\ndeclared bound and core's hard cap; what reaches core is final.\n\n``RUN_ENVIRONMENT_UNAVAILABLE`` is a measurement the run environment refused because\nit can run no such work at all (a local Slurm root its probe could not prove shared).\nIt is nobody's candidate's fault, and no retry can succeed, so it is never retried."

    WORKLOAD = "workload"
    INFRASTRUCTURE = "infrastructure"
    AMBIGUOUS = "ambiguous"
    UNKNOWN = "unknown"
    RUN_ENVIRONMENT_UNAVAILABLE = "run_environment_unavailable"


class WaitKind(StrEnum):
    "What is waiting."

    SESSION = "session"
    RECOVERY_CHECK = "recovery-check"
    OWNED_JOB = "owned-job"
    RELEASE_DEPENDENCY = "release-dependency"
    YIELD_PUBLICATION = "yield-publication"
    INTERRUPT = "interrupt"
    REFUND = "refund"
    DISPATCH = "dispatch"
    RETRY = "retry"
    TURN_ACQUISITION = "turn-acquisition"
    BLOCKED_RECORD = "blocked-record"


class Charge(StrEnum):
    "What a role's first turn in a session costs the run's budget.\n\nA turn that works on a candidate costs one ``ATTEMPT`` (and a turn); a turn that does not\n(a judge, a planner) costs only a ``TURN``. Corrections and resumes cost a turn."

    ATTEMPT = "paid"
    TURN = "free"


class Correctable(StrEnum):
    "The failures of a reply that core may ask the agent to correct, within ``corrections``."

    INVALID_REPLY = "invalid-reply"
    UNRECORDED_WAIT = "unrecorded-wait"


class FailureReason(StrEnum):
    "Why a requested piece of work ended without a result. Closed: no free-text reasons."

    TIMED_OUT = "timed-out"
    WORKSPACE_CLOSED = "workspace-closed"
    OWNER_GONE = "owner-gone"
    REFUSED = "refused"
    LOST = "lost"
    INVALID_REPLY = "invalid-reply"
    RENDER_FAILED = "render-failed"
    TURN_FAILED = "turn-failed"
    WAIT_REFUSED = "wait-refused"
    QUEUE_BUDGET_SPENT = "queue-budget-spent"
    OUT_OF_BUDGET = "out-of-budget"
    MEASUREMENT_UNTRUSTED = "measurement-untrusted"


class WaitRefusal(StrEnum):
    "Why a turn that asked to wait for its evaluations was not suspended. Closed."

    NOT_OFFERED = "not-offered"
    NOT_RECORDED = "not-recorded"
    NOT_PUBLISHED = "not-published"
    NOT_RESUMABLE = "not-resumable"


class ReplyErrorCode(StrEnum):
    "Why a reply did not validate against the agent's reply model. Closed."

    MISSING = "missing"
    UNEXPECTED = "unexpected"
    WRONG_TYPE = "wrong-type"
    INVALID_VALUE = "invalid-value"
    NOT_JSON = "not-json"


class EvaluationHistoryAvailability(StrEnum):
    "Unavailable history cannot prove an empty or successful history."

    COMPLETE = "complete"
    UNAVAILABLE = "unavailable"


class EvaluationStageOutcome(StrEnum):
    "Scientific stage result, independent of external execution success."

    PASSED = "passed"
    FAILED = "failed"
    UNKNOWN = "unknown"


class AttemptTerminalReason(StrEnum):
    "Durable continuation exhaustion, distinct from resource release."

    REPEATED_TRACEBACK = "repeated-traceback"
    REPEATED_MEASUREMENT = "repeated-measurement"
    HISTORY_UNAVAILABLE = "history-unavailable"
    NO_NEW_EVALUATION = "no-new-evaluation"
