# Generated from vibesys/core/v1/leaves.proto by protoc-gen-corevalue. DO NOT EDIT.
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
    AttemptId,
    ChargeId,
    ContinuationId,
    DecisionId,
    EventId,
    EvidenceId,
    HostId,
    InputId,
    InvocationId,
    OperationId,
    RequestId,
    ResourceId,
    RevisionId,
    Scope,
)
from vs_core._generated.vibesys.core.v1.vocabulary_cv import (
    ChargeKind,
    CheckpointDecline,
    ComparisonVerdict,
    EvaluationStageOutcome,
    ObservationStatus,
    WaitEnd,
    WaitKind,
    WorkspaceMode,
)
from vs_core._leaf_behavior import BackoffPolicy, ObservationFacts, WaitDuration
from vs_core._revision_ref import RevisionReference

Direction = Literal["max", "min"]

ChargeClass = Literal["free", "paid", "correction", "resume"]

LifecycleCapability = Literal["park", "interrupt", "steer", "suspend", "profile-capture"]

TaskObjectiveDirection = Literal["max", "min"]

ChildManifestBasis = Literal["enumerated", "lifecycle-closed"]

JobProgressState = Literal["pending", "running", "unknown"]

type Generation = Annotated[int, Field(ge=0)]

type Count = Annotated[int, Field(ge=0)]

type RevisionNumber = Annotated[int, Field(ge=0)]

type Seconds = Annotated[float, Field(ge=0.0, allow_inf_nan=False)]

type Finite = Annotated[float, Field(allow_inf_nan=False)]

type Limit = Annotated[float, Field(allow_inf_nan=False)]


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class EvidenceKey(CoreValue):
    "Full identity of one evidence record: the request that produced it plus its ID.\n\nEvidenceId alone is only unique within one source request, so two jobs can\nreport the same ID. Every ledger, settlement and continuation lookup keys on\nthis pair."

    model_fields: ClassVar[tuple[str, ...]] = ("kind", "source_request", "evidence_id")
    kind: Literal["evidence_key"] = "evidence_key"
    source_request: RequestId
    evidence_id: EvidenceId

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class RevisionRef(RevisionReference, CoreValue):
    "Immutable revision identity.\n\nCore compares digest as an opaque equality key. Producers that mint a ref from\na git commit use of_git_commit, which makes revision_id the commit and digest\n`git-commit:<commit>`; consumers that must trust the scheme read git_commit,\nwhich is None unless digest names the same commit as revision_id."

    model_fields: ClassVar[tuple[str, ...]] = ("revision_id", "digest")
    revision_id: RevisionId
    digest: Annotated[str, Field(min_length=1)]

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class AttemptRef(CoreValue):
    "Attempt ref lifecycle contract."

    model_fields: ClassVar[tuple[str, ...]] = ("attempt_id", "generation")
    attempt_id: AttemptId
    generation: Annotated[int, Field(ge=0)]

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class OperationRef(CoreValue):
    "Operation ref lifecycle contract."

    model_fields: ClassVar[tuple[str, ...]] = ("operation_id", "generation")
    operation_id: OperationId
    generation: Annotated[int, Field(ge=0)]

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class DependencyRef(CoreValue):
    "Dependency ref lifecycle contract."

    model_fields: ClassVar[tuple[str, ...]] = ("decision_id", "request_id")
    decision_id: DecisionId | None = None
    request_id: RequestId | None = None

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class ScopeReopenNormalization(CoreValue):
    "Canonical guarded reopen target bound to a registered payload.\n\nThe operation is run-scoped. Its target must be the exact parked attempt,\nand resolved_cancelled_jobs must equal the continuation cancellation set.\nA newer park authority invalidates older reopen acknowledgements."

    model_fields: ClassVar[tuple[str, ...]] = (
        "kind",
        "attempt",
        "continuation_id",
        "park_authority",
        "resolved_cancelled_jobs",
    )
    kind: Literal["scope_reopen"] = "scope_reopen"
    attempt: AttemptRef
    continuation_id: ContinuationId
    park_authority: RequestId
    resolved_cancelled_jobs: tuple[ResourceId, ...]

    def __post_init__(self) -> None:
        """Enforce the proto message rules at construction as well as decode."""
        if not (
            len(set(p.root for p in self.resolved_cancelled_jobs))
            == len(self.resolved_cancelled_jobs)
        ):
            raise ValueError("resolved_cancelled_jobs: duplicate resource ID")

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class TaskStage(CoreValue):
    "One stage the task can measure and the seconds its execution may take."

    model_fields: ClassVar[tuple[str, ...]] = ("stage_id", "execution_seconds")
    stage_id: Annotated[str, Field(min_length=1)]
    execution_seconds: Annotated[float, Field(ge=0.0, allow_inf_nan=False)]

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class TaskObjective(CoreValue):
    "One axis the task compares candidates on, and which way is better."

    model_fields: ClassVar[tuple[str, ...]] = ("name", "direction")
    name: Annotated[str, Field(min_length=1)]
    direction: TaskObjectiveDirection

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class ChildManifest(CoreValue):
    'Authoritative set of every transitive descendant at one observation.\n\nmembers lists all descendants, not only direct children. basis says how the\nexecutor knows it: "enumerated" from the live process or resource tree, or\n"lifecycle-closed" because the closed handle can admit no descendant (members\nmay then be empty). Core requires every member to be owned and released\nbefore it accepts the release as complete.'

    model_fields: ClassVar[tuple[str, ...]] = ("members", "basis")
    members: tuple[ResourceId, ...] = ()
    basis: ChildManifestBasis

    def __post_init__(self) -> None:
        """Enforce the proto message rules at construction as well as decode."""
        if not (len(set(p.root for p in self.members)) == len(self.members)):
            raise ValueError("members: duplicate resource ID")

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class Observation(ObservationFacts, CoreValue):
    "External facts correlated by request, scope, generation and episode.\n\nchildren_complete explicitly claims an authoritative manifest; an empty\nchildren tuple alone does not prove it. child_manifest, when present, is\nthat manifest: it must contain every child, only an observation that claims\nchildren_complete may carry it, and consumers read descendants, never\nchildren alone, when proving release. A children_complete claim without a\nmanifest stays accepted for producers that predate it. Install discovered child ownership\nbefore removing provisional request ownership. Unknown acceptance or missing\nidentity never proves release. revision is the revision a snapshot or retain\nrequest produced or retained, as the executor saw it; a caller-supplied\nrevision on a derived event is accepted only when it equals this one."

    model_fields: ClassVar[tuple[str, ...]] = (
        "event_id",
        "request_id",
        "scope",
        "sequence",
        "observed_at",
        "status",
        "resource_id",
        "revision",
        "measured_input",
        "accepted",
        "terminal",
        "released",
        "children",
        "children_complete",
        "child_manifest",
        "admission_id",
        "diagnostic",
    )
    event_id: EventId
    request_id: RequestId
    scope: Scope
    sequence: Annotated[int, Field(ge=0)]
    observed_at: Annotated[float, Field(ge=0.0, allow_inf_nan=False)]
    status: ObservationStatus
    resource_id: ResourceId | None = None
    revision: RevisionRef | None = None
    measured_input: Annotated[str | None, Field(min_length=1, exclude_if=omit_unset)] = None
    accepted: bool = False
    terminal: bool = False
    released: bool = False
    children: tuple[ResourceId, ...] = ()
    children_complete: bool = False
    child_manifest: ChildManifest | None = None
    admission_id: DecisionId | None = None
    diagnostic: str = ""

    def __post_init__(self) -> None:
        """Enforce the proto message rules at construction as well as decode."""
        if not (not (self.child_manifest is not None) or self.children_complete):
            raise ValueError("child_manifest requires children_complete")
        if not (
            not (self.child_manifest is not None)
            or all((c in self.child_manifest.members) for c in self.children)
        ):
            raise ValueError("child_manifest omits a reported child")

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class ChargeReceipt(CoreValue):
    "Authoritative charge and bounded, deduplicated refund proof.\n\nHistorical aggregate proof preserves consumed migration budget only. It\ngrants no dispatch authority and cannot receive a new refund without exact\nlive charge correspondence. Unsupported ADMISSION refunds require positive\nnonacceptance and drained ownership; ATTEMPT refunds need bounded authority."

    model_fields: ClassVar[tuple[str, ...]] = (
        "charge_id",
        "kind",
        "invocation_id",
        "source_request",
        "charged",
        "refunded",
        "refund_sources",
        "historical_proof",
    )
    charge_id: ChargeId
    kind: ChargeKind
    invocation_id: InvocationId | None = None
    source_request: RequestId | None = None
    charged: Annotated[int, Field(ge=0)]
    refunded: Annotated[int, Field(ge=0)] = 0
    refund_sources: tuple[RequestId, ...] = ()
    historical_proof: ArtifactRef | None = None

    def __post_init__(self) -> None:
        """Enforce the proto message rules at construction as well as decode."""
        if not (
            len(str(self.refunded)) < len(str(self.charged))
            or (
                len(str(self.refunded)) == len(str(self.charged))
                and str(self.refunded) <= str(self.charged)
            )
        ):
            raise ValueError("refunded exceeds charged amount")
        if not (
            {"admission": 1, "attempt": 2, "turn": 3}.get(self.kind, 0) != 3 or self.refunded == 0
        ):
            raise ValueError("TURN charges never refund")
        if not (len(set(p.root for p in self.refund_sources)) == len(self.refund_sources)):
            raise ValueError("refund_sources: duplicate refund authority")

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class HostFence(CoreValue):
    "Host fence lifecycle contract."

    model_fields: ClassVar[tuple[str, ...]] = ("host_id", "epoch")
    host_id: HostId
    epoch: Annotated[int, Field(ge=0)]

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class EventCursor(CoreValue):
    "Event cursor lifecycle contract."

    model_fields: ClassVar[tuple[str, ...]] = ("sequence",)
    sequence: Annotated[int, Field(ge=0)]

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class WorkspaceRef(CoreValue):
    "Workspace ref lifecycle contract."

    model_fields: ClassVar[tuple[str, ...]] = ("scope", "revision", "mode")
    scope: Scope
    revision: RevisionRef
    mode: WorkspaceMode

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class ReservedInputOccurrence(CoreValue):
    "Immutable execution payload for an input occurrence reserved by Sessions B.\n\nSessions A validates the exact ID/artifact manifest against InputRecord before\ndispatch. The operation wire remains canonical and is never rewritten to\ntransport inputs. Equal artifacts with distinct occurrence IDs stay distinct."

    model_fields: ClassVar[tuple[str, ...]] = ("input_id", "artifact")
    input_id: InputId
    artifact: ArtifactRef

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class Reading(CoreValue):
    "A run's headline reading of the benchmark stage, as the trusted record states it."

    model_fields: ClassVar[tuple[str, ...]] = (
        "metric",
        "value",
        "direction",
        "unit",
        "repetitions",
    )
    metric: Annotated[str, Field(min_length=1)]
    value: Annotated[float, Field(allow_inf_nan=False)]
    direction: Direction | None = None
    unit: str | None = None
    repetitions: Annotated[int, Field(ge=1)] = 1

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class BaseRef(CoreValue):
    "The revision a candidate is compared with, and what the evaluation reads there.\n\nBoth facts together or neither: a base without its measured input could not be matched to\nits own samples."

    model_fields: ClassVar[tuple[str, ...]] = ("commit", "measured_input")
    commit: RevisionRef
    measured_input: Annotated[str, Field(min_length=1)]

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class Sample(CoreValue):
    "One framework run of one measured input: the unit of measurement, always traceable.\n\nA sample is built only from a run that ended conclusively with a trusted benchmark reading,\nand requires the ``commit`` it measured and the ``measured_input`` digest (#2961). The\n``base`` is set on a candidate's sample and names the revision it is compared with."

    model_fields: ClassVar[tuple[str, ...]] = (
        "run",
        "scope",
        "commit",
        "measured_input",
        "evaluation",
        "gates",
        "host",
        "dedicated_resources",
        "reading",
        "observed_at",
        "base",
    )
    run: RequestId
    scope: Scope
    commit: RevisionRef
    measured_input: Annotated[str, Field(min_length=1)]
    evaluation: Annotated[str, Field(min_length=1)]
    gates: tuple[str, ...]
    host: Annotated[str, Field(min_length=1)]
    dedicated_resources: bool
    reading: Reading
    observed_at: Annotated[float, Field(ge=0.0, allow_inf_nan=False)]
    base: BaseRef | None = None

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class CoreRun(CoreValue):
    "A run core itself asked for, on a candidate's behalf, in its scope.\n\nNo decision and no agent call asked for it, so this record is its origin: core admitted it\nso that ``candidate`` (the run whose result needed it) has a comparison with a measured\nbase. Its plan says which side it measures: the base, or (under no declared floor) the\ncandidate's own input again (`vs_core._comparison`)."

    model_fields: ClassVar[tuple[str, ...]] = (
        "request_id",
        "scope",
        "candidate",
        "candidate_input",
        "fresh_when_asked",
    )
    request_id: RequestId
    scope: Scope
    candidate: RequestId
    candidate_input: Annotated[str, Field(min_length=1)]
    fresh_when_asked: Annotated[int, Field(ge=0)]

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class BaseClaim(CoreValue):
    "A run of the strategy's own that core counts as samples of a base once it ends.\n\nThe strategy measures the run's input before any agent works, from a plan that names no\nmeasured input. When an agent's evaluation then needs that base while the run is still\ngoing, core starts no second run of it: it claims the running one, so its result becomes the\nbase's samples (``base``) when it ends, and the candidate's comparison follows."

    model_fields: ClassVar[tuple[str, ...]] = ("request_id", "base")
    request_id: RequestId
    base: BaseRef

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class Runaway(CoreValue):
    "An attempt whose agent has asked for an unusual number of evaluations.\n\nA warning and nothing more: it is recorded once per scope, when the count of the scope's\nadmitted evaluations first reaches ``limit``, and it never refuses a call."

    model_fields: ClassVar[tuple[str, ...]] = ("scope", "evaluations", "limit")
    scope: Scope
    evaluations: Annotated[int, Field(ge=0)]
    limit: Annotated[int, Field(ge=0)]

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class Comparison(CoreValue):
    "A candidate's samples compared with its base's: the ratio, its noise and the verdict.\n\n``ratio`` is the candidate's mean over the base's, as measured; ``direction`` says which\nway is better, so ``better`` is a ratio above one for ``max`` and below one for ``min``.\n``noise`` is one standard error of the ratio, and a verdict needs two of them: the\nstatistic is in ``vs_core._comparison``."

    model_fields: ClassVar[tuple[str, ...]] = (
        "scope",
        "metric",
        "unit",
        "direction",
        "candidate",
        "candidate_input",
        "base",
        "base_input",
        "candidate_value",
        "base_value",
        "ratio",
        "noise",
        "verdict",
        "candidate_samples",
        "base_samples",
    )
    scope: Scope
    metric: Annotated[str, Field(min_length=1)]
    unit: str | None = None
    direction: Direction
    candidate: RevisionRef
    candidate_input: Annotated[str, Field(min_length=1)]
    base: RevisionRef
    base_input: Annotated[str, Field(min_length=1)]
    candidate_value: Annotated[float, Field(allow_inf_nan=False)]
    base_value: Annotated[float, Field(allow_inf_nan=False)]
    ratio: Annotated[float, Field(allow_inf_nan=False)]
    noise: Annotated[float, Field(ge=0.0, allow_inf_nan=False)]
    verdict: ComparisonVerdict
    candidate_samples: Annotated[int, Field(ge=0)]
    base_samples: Annotated[int, Field(ge=0)]

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class StatedComparison(CoreValue):
    "A candidate's comparison with its base as a flow reads it from the run's view.\n\n``settling`` is true while a run that will feed the comparison has not ended (core's own\nrun of the base, or of the candidate's input, that a close call earned): the comparison\nchanges when it lands, so it is not yet the one a verdict may rest on\n(`vs_core._comparison.comparison_settling`)."

    model_fields: ClassVar[tuple[str, ...]] = ("comparison", "settling", "answer_until")
    comparison: Comparison
    settling: bool
    answer_until: Annotated[
        float | None, Field(ge=0.0, allow_inf_nan=False, exclude_if=omit_unset)
    ] = None

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class ComparisonSettled(CoreValue):
    "A run that fed a comparison ended, and no run that feeds it is under way any more.\n\nCore reports it at the end of the run, so a flow that waits for the comparison to settle\n(``ctx.evaluation.measure_compared``) wakes when the comparison it would read is final, and\nnot at a poll. It names the comparison only: the run's view states it\n(``RunView.comparisons``), or no longer does when its base went stale meanwhile."

    model_fields: ClassVar[tuple[str, ...]] = ("kind", "scope", "candidate_input", "base_input")
    kind: Literal["comparison_settled"] = "comparison_settled"
    scope: Scope
    candidate_input: Annotated[str, Field(min_length=1)]
    base_input: Annotated[str, Field(min_length=1)]

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class JobProgress(CoreValue):
    "Current nonterminal job progress, retaining absent measurements as None.\n\nSequence and time must match the carrying observation. Older observations\ncannot replace newer progress; changed payload at equal sequence conflicts.\nStage IDs must belong to the owner's validated stage registry."

    model_fields: ClassVar[tuple[str, ...]] = (
        "observation_sequence",
        "observed_at",
        "state",
        "stage_id",
        "queued_s",
        "ran_s",
        "pending_reason",
        "estimated_start_at",
    )
    observation_sequence: Annotated[int, Field(ge=0)]
    observed_at: Annotated[float, Field(ge=0.0, allow_inf_nan=False)]
    state: JobProgressState
    stage_id: Annotated[str | None, Field(min_length=1)] = None
    queued_s: Annotated[float | None, Field(ge=0.0, allow_inf_nan=False)] = None
    ran_s: Annotated[float | None, Field(ge=0.0, allow_inf_nan=False)] = None
    pending_reason: str | None = None
    estimated_start_at: Annotated[float | None, Field(ge=0.0, allow_inf_nan=False)] = None

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class JobTimeout(CoreValue):
    "Frozen progress of one unfinished dependency when the bound that ended it passed.\n\nA wait ends a job either with the wait itself (its earliest bound) or alone, when the\njob's own queue budget runs out while its siblings go on (``QUEUE_BUDGET``)."

    model_fields: ClassVar[tuple[str, ...]] = ("resource_id", "progress", "by", "at")
    resource_id: ResourceId
    progress: JobProgress | None = None
    by: WaitEnd
    at: Annotated[float, Field(ge=0.0, allow_inf_nan=False)]

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class TimedOut(CoreValue):
    "Immutable wait-all timeout evidence, separate from job termination.\n\nFreeze before applying observations at or beyond the deadline. unfinished\nnames each unfinished dependency once, in continuation order. Late results\nmay resolve cleanup but never rewrite this value or resume authorization.\nWaiting, inspection and timeout authorization create no execution charge."

    model_fields: ClassVar[tuple[str, ...]] = (
        "kind",
        "deadline_at",
        "reached_at",
        "by",
        "unfinished",
    )
    kind: Literal["timed_out"] = "timed_out"
    deadline_at: Annotated[float, Field(ge=0.0, allow_inf_nan=False)]
    reached_at: Annotated[float, Field(ge=0.0, allow_inf_nan=False)]
    by: WaitEnd = WaitEnd("limit")
    unfinished: Annotated[tuple[JobTimeout, ...], Field(min_length=1)] = ()

    def __post_init__(self) -> None:
        """Enforce the proto message rules at construction as well as decode."""
        if not (self.reached_at >= self.deadline_at):
            raise ValueError("reached_at precedes deadline_at")
        if not (all((j.at <= self.reached_at) for j in self.unfinished)):
            raise ValueError("job bound follows reached_at")
        if not (len(set(p.resource_id.root for p in self.unfinished)) == len(self.unfinished)):
            raise ValueError("unfinished: duplicate resource ID")

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class WaitSubject(CoreValue):
    "One waiting entity: what kind of wait it is and the identity ``OpenWait.subject`` names."

    model_fields: ClassVar[tuple[str, ...]] = ("kind", "identity")
    kind: WaitKind
    identity: str

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class WaitOn(CoreValue):
    "The requests, waiting entities and timer one waiter waits on; any one of them wakes it.\n\nThe transition that makes an entity wait writes this value in the same step, naming\nexactly what can end the wait: requests by identity, other waiting entities whose own\nprogress carries this one (``subjects``), and a core time the shell's clock reaches.\nNothing is inferred later. Applying an answer of one of the requests wakes the waiter,\nwhich moves on or waits again; a waiter that needs several answers waits again on the ones\nstill open. A named entity carries the waiter only while its own recorded wait does, and\nwhich kinds may name which is fixed and acyclic (``MAY_WAIT_ON`` in ``_waits``), so the\nchain of waits always ends at a request or a timer. A wait on nothing is no wait, so an\nempty one is refused."

    model_fields: ClassVar[tuple[str, ...]] = ("requests", "subjects", "timer")
    requests: tuple[RequestId, ...] = ()
    subjects: tuple[WaitSubject, ...] = ()
    timer: Annotated[float | None, Field(ge=0.0, allow_inf_nan=False)] = None

    def __post_init__(self) -> None:
        """Enforce the proto message rules at construction as well as decode."""
        if not (len(self.requests) > 0 or len(self.subjects) > 0 or (self.timer is not None)):
            raise ValueError("a wait names a dependency")
        if not (len(set(p.root for p in self.requests)) == len(self.requests)):
            raise ValueError("duplicate request")
        if not (
            all(
                (
                    len(
                        tuple(
                            p
                            for p in self.subjects
                            if (
                                {
                                    "session": 1,
                                    "recovery-check": 2,
                                    "owned-job": 3,
                                    "release-dependency": 4,
                                    "yield-publication": 5,
                                    "interrupt": 6,
                                    "refund": 7,
                                    "dispatch": 8,
                                    "retry": 9,
                                    "turn-acquisition": 10,
                                    "blocked-record": 11,
                                }.get(p.kind, 0)
                                == {
                                    "session": 1,
                                    "recovery-check": 2,
                                    "owned-job": 3,
                                    "release-dependency": 4,
                                    "yield-publication": 5,
                                    "interrupt": 6,
                                    "refund": 7,
                                    "dispatch": 8,
                                    "retry": 9,
                                    "turn-acquisition": 10,
                                    "blocked-record": 11,
                                }.get(s.kind, 0)
                                and p.identity == s.identity
                            )
                        )
                    )
                    == 1
                )
                for s in self.subjects
            )
        ):
            raise ValueError("duplicate wait subject")

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class Backoff(BackoffPolicy, CoreValue):
    "Run-clock wait before a lost turn is asked again; each further ask doubles it."

    model_fields: ClassVar[tuple[str, ...]] = ("first", "cap")
    first: Annotated[float, Field(ge=0.0, allow_inf_nan=False)]
    cap: Annotated[float, Field(ge=0.0, allow_inf_nan=False)]

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class Exchanges(CoreValue):
    "The templates of the exchanges core itself starts with a role's agent.\n\nEach names a template file the renderer reads; the wording is the template's. A role needs\nexactly the exchanges it can meet (see ``Role``), and a missing one is a declaration error."

    model_fields: ClassVar[tuple[str, ...]] = ("correction", "resume", "wait_unrecorded")
    correction: str | None = None
    resume: str | None = None
    wait_unrecorded: str | None = None

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class WaitUnrecorded(CoreValue):
    "What the prompt is built from when a reply waited and the run recorded no wait."

    model_fields: ClassVar[tuple[str, ...]] = ("kind", "role")
    kind: Literal["wait-unrecorded"] = "wait-unrecorded"
    role: str

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class Retained(CoreValue):
    "Core retained the exact revision this turn produced."

    model_fields: ClassVar[tuple[str, ...]] = ("kind", "revision")
    kind: Literal["retained"] = "retained"
    revision: RevisionRef

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class Declined(CoreValue):
    "Core declined or failed this turn's checkpoint."

    model_fields: ClassVar[tuple[str, ...]] = ("kind", "reason")
    kind: Literal["declined"] = "declined"
    reason: CheckpointDecline

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class WhileOpen(CoreValue):
    "End the wait with ``WORKSPACE_CLOSED`` when this workspace stops being open."

    model_fields: ClassVar[tuple[str, ...]] = ("kind", "workspace")
    kind: Literal["while_open"] = "while_open"
    workspace: AttemptRef

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class WhileAlive(CoreValue):
    "End the wait with ``OWNER_GONE`` when this job is no longer alive."

    model_fields: ClassVar[tuple[str, ...]] = ("kind", "job")
    kind: Literal["while_alive"] = "while_alive"
    job: ResourceId

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class Within(WaitDuration, CoreValue):
    "A duration on the run clock, plus the watches that may end the wait earlier.\n\n``limit`` is relative to the time core accepts the wait (an agent turn's requests count it\nfrom their dispatch instead: see ``FromDispatch``). A limit of zero or less is\nrefused by core (``Failed(REFUSED)``); the type does not forbid it so that a bypassed\nvalue still reaches the runtime check instead of failing on construction."

    model_fields: ClassVar[tuple[str, ...]] = ("limit", "watches")
    limit: Annotated[float, Field(allow_inf_nan=False)]
    watches: tuple[Watch, ...] = ()

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class FromDispatch(CoreValue):
    "Internal: ``limit`` seconds counted from the instant the shell dispatches the request.\n\nA request waits for the shell's free slot before it runs, and the time it waits is not\nthe work's. Core therefore keeps the request open until the run deadline while it is\nqueued, and stamps ``Until(at=dispatch time + limit)`` when ``DispatchAuthorized``\narrives with the time the shell dispatched it (that time is journaled with the event, so\nreplay stamps the same instant)."

    model_fields: ClassVar[tuple[str, ...]] = ("kind", "limit", "watches")
    kind: Literal["from_dispatch"] = "from_dispatch"
    limit: Annotated[float, Field(allow_inf_nan=False)]
    watches: tuple[Watch, ...] = ()

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class Inherited(CoreValue):
    "Internal: this request ends when the decision that caused it ends (same ``Until``)."

    model_fields: ClassVar[tuple[str, ...]] = ("kind",)
    kind: Literal["inherited"] = "inherited"

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class Until(CoreValue):
    "What core stored when it accepted a ``Within``: an absolute time and the watches."

    model_fields: ClassVar[tuple[str, ...]] = ("at", "watches")
    at: Annotated[float, Field(ge=0.0, allow_inf_nan=False)]
    watches: tuple[Watch, ...] = ()

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class EvaluationStageResult(CoreValue):
    "One validated stage result from the accepted terminal submission fact."

    model_fields: ClassVar[tuple[str, ...]] = ("stage_id", "outcome")
    stage_id: Annotated[str, Field(min_length=1)]
    outcome: EvaluationStageOutcome

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class BenchmarkFailure(CoreValue):
    "Failed benchmark partial and the explicit comparison interval containing it.\n\nThe measurement owner validates the rate and interval against its typed\noutcome. Absence of this value cannot prove a repeated measurement failure."

    model_fields: ClassVar[tuple[str, ...]] = ("partial_rate", "rate_lower", "rate_upper")
    partial_rate: Annotated[float, Field(ge=0.0, allow_inf_nan=False)]
    rate_lower: Annotated[float, Field(ge=0.0, allow_inf_nan=False)]
    rate_upper: Annotated[float, Field(ge=0.0, allow_inf_nan=False)]

    def __post_init__(self) -> None:
        """Enforce the proto message rules at construction as well as decode."""
        if not (self.rate_lower <= self.partial_rate and self.partial_rate <= self.rate_upper):
            raise ValueError("partial_rate outside declared rate range")

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class EvaluationHistoryCursor(CoreValue):
    "Immutable preceding-submission cursor captured at resume publication.\n\nZero identifies the beginning; otherwise submission_id names the exact\nordinal. A missing ID never stands in for a nonempty historical cursor."

    model_fields: ClassVar[tuple[str, ...]] = ("ordinal", "submission_id")
    ordinal: Annotated[int, Field(ge=0)] = 0
    submission_id: RequestId | None = None

    def __post_init__(self) -> None:
        """Enforce the proto message rules at construction as well as decode."""
        if not ((self.ordinal == 0) != (self.submission_id is not None)):
            raise ValueError("submission_id must match cursor ordinal")

    __hash__ = CoreValue.cached_hash


type Watch = Annotated[WhileOpen | WhileAlive, Field(discriminator="kind")]

type Checkpoint = Annotated[Retained | Declined, Field(discriminator="kind")]

resolve_value(EvidenceKey)
resolve_value(RevisionRef)
resolve_value(AttemptRef)
resolve_value(OperationRef)
resolve_value(DependencyRef)
resolve_value(ScopeReopenNormalization)
resolve_value(TaskStage)
resolve_value(TaskObjective)
resolve_value(ChildManifest)
resolve_value(Observation)
resolve_value(ChargeReceipt)
resolve_value(HostFence)
resolve_value(EventCursor)
resolve_value(WorkspaceRef)
resolve_value(ReservedInputOccurrence)
resolve_value(Reading)
resolve_value(BaseRef)
resolve_value(Sample)
resolve_value(CoreRun)
resolve_value(BaseClaim)
resolve_value(Runaway)
resolve_value(Comparison)
resolve_value(StatedComparison)
resolve_value(ComparisonSettled)
resolve_value(JobProgress)
resolve_value(JobTimeout)
resolve_value(TimedOut)
resolve_value(WaitSubject)
resolve_value(WaitOn)
resolve_value(Backoff)
resolve_value(Exchanges)
resolve_value(WaitUnrecorded)
resolve_value(Retained)
resolve_value(Declined)
resolve_value(WhileOpen)
resolve_value(WhileAlive)
resolve_value(Within)
resolve_value(FromDispatch)
resolve_value(Inherited)
resolve_value(Until)
resolve_value(EvaluationStageResult)
resolve_value(BenchmarkFailure)
resolve_value(EvaluationHistoryCursor)
