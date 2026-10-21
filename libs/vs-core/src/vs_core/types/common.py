"""Strict immutable values shared by lifecycle areas."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    field_serializer,
    model_serializer,
    model_validator,
)

from vs_core._corevalue import ContractValidationError
from vs_core._generated.vibesys.core.v1.common_cv import (
    IDENTITY_PATTERN,
    ArtifactId,
    ArtifactRef,
    AttemptId,
    ChargeId,
    ContinuationId,
    ControlAction,
    ControlId,
    DecisionId,
    EventId,
    EvidenceId,
    HostId,
    Identity,
    InputId,
    InputMode,
    InvocationId,
    InvocationRef,
    ItemId,
    OperationId,
    PauseOwner,
    PoolId,
    RequestId,
    ResourceId,
    ResumeWhen,
    RevisionId,
    RoleId,
    RunId,
    RunStatus,
    SchemaRef,
    Scope,
    SessionId,
    SettlementId,
    StopMode,
    StrategyId,
)
from vs_core._generated.vibesys.core.v1.control_cv import (
    MAX_REQUEST_RETRIES,
    ControlInput,
    InputTarget,
    InvocationInputTarget,
    ItemInputTarget,
    Limits,
    PoolCapacity,
    RequestRetries,
    RoleInputTarget,
    RunPause,
    ScopeInputTarget,
)
from vs_core._generated.vibesys.core.v1.leaves_cv import (
    AttemptRef,
    ChargeReceipt,
    ChildManifest,
    Count,
    DependencyRef,
    EventCursor,
    EvidenceKey,
    Generation,
    HostFence,
    LifecycleCapability,
    Observation,
    OperationRef,
    ReservedInputOccurrence,
    RevisionNumber,
    RevisionRef,
    ScopeReopenNormalization,
    Seconds,
    TaskObjective,
    TaskStage,
    WorkspaceRef,
)
from vs_core._generated.vibesys.core.v1.vocabulary_cv import (
    Area,
    AssessmentKind,
    ChargeKind,
    CheckpointDecline,
    CompletionStatus,
    DependencyStatus,
    DigestScheme,
    EvidenceKind,
    LifecycleClass,
    ObservationStatus,
    OperationNormalizationKind,
    RejectionCode,
    RevisionAuthority,
    SetupFailureKind,
    TurnFailureKind,
    WorkspaceMode,
)
from vs_core._values import deeply_immutable, emit_fragment

if TYPE_CHECKING:
    from pydantic import SerializationInfo


__all__ = [
    "IDENTITY_PATTERN",
    "MAX_CORRECTIONS",
    "MAX_LOST_TURNS",
    "MAX_REASK_CHAIN",
    "MAX_REQUEST_RETRIES",
    "MAX_SUBMISSIONS_PER_MEASUREMENT",
    "RETRYABLE_FAILURES",
    "THIS_RUN",
    "Area",
    "ArtifactId",
    "ArtifactRef",
    "AssessmentKind",
    "AttemptId",
    "AttemptRef",
    "Capabilities",
    "ChargeId",
    "ChargeKind",
    "ChargeReceipt",
    "CheckpointDecline",
    "ChildManifest",
    "CompletionStatus",
    "ContinuationId",
    "ContractValidationError",
    "ControlAction",
    "ControlId",
    "ControlInput",
    "Count",
    "DecisionId",
    "DependencyRef",
    "DependencyStatus",
    "DigestScheme",
    "EventCursor",
    "EventId",
    "EvidenceId",
    "EvidenceKey",
    "EvidenceKind",
    "ExecuteRegisteredOperation",
    "Generation",
    "HostFence",
    "HostId",
    "Identity",
    "IdentityTypeError",
    "InputId",
    "InputMode",
    "InputTarget",
    "InvocationId",
    "InvocationInputTarget",
    "InvocationRef",
    "ItemId",
    "ItemInputTarget",
    "KernelNotImplementedError",
    "LifecycleCapability",
    "LifecycleClass",
    "Limits",
    "Observation",
    "ObservationStatus",
    "OperationDescriptor",
    "OperationDescriptorError",
    "OperationId",
    "OperationNormalizationKind",
    "OperationRef",
    "OperationSchemaRef",
    "OperationWire",
    "PauseOwner",
    "PoolCapacity",
    "PoolId",
    "PureOption",
    "RejectionCode",
    "ReleaseDependency",
    "RequestBase",
    "RequestId",
    "RequestRetries",
    "ReservedInputOccurrence",
    "ResourceId",
    "ResumeWhen",
    "RevisionAuthority",
    "RevisionId",
    "RevisionNumber",
    "RevisionRef",
    "RoleId",
    "RoleInputTarget",
    "RunFacts",
    "RunId",
    "RunPause",
    "RunStatus",
    "SchemaRef",
    "Scope",
    "ScopeInputTarget",
    "ScopeReopenNormalization",
    "Seconds",
    "SessionId",
    "SettlementId",
    "SetupFailureKind",
    "SignalCycleError",
    "StopMode",
    "StrategyId",
    "TaskObjective",
    "TaskStage",
    "TaskTerms",
    "TurnFailureKind",
    "Value",
    "WorkspaceMode",
    "WorkspaceRef",
    "escalate_stop",
    "unset",
    "validate_setup_failure",
]


def unset(value: object) -> bool:
    """Whether an optional field is unset (None) or a flag is off, so serialization leaves it out.

    For ``Field(exclude_if=unset)`` on optional fields and flags: left out when unset, a value that sets
    none of them serializes, and so digests, budget keys and request ids, exactly as before the
    field existed. That keeps identities stable. Any new field changes the envelope's shape and
    version, which needs its migration (``vs_core._envelope_migrations``).
    """
    return value is None or value is False or (isinstance(value, tuple) and not value)


class Value(BaseModel):
    """Persistable data, never live interfaces or mutable containers."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, validate_default=True)

    # Pydantic hashes a frozen model by hashing every field, recursively, on every call. Core
    # keys its memoized checks (`lru_cache` on whole envelopes and requests) by these values, so
    # each lookup re-walked the entire unchanged history: the dominant cost of a step. A value
    # cannot change, so its hash is computed once. A slot keeps it out of `__dict__` and out of
    # equality, copies and serialization; a copy is a new object and hashes afresh. The other
    # slots hold facts derived from the fields (immutability verdict, canonical text, JSON
    # fragment; see `vs_core._values.recall`) under the same rule, and are why those facts cost
    # an attribute read instead of a side-table lookup.
    __slots__ = ("_canonical", "_hash", "_immutable", "_text")

    def __hash__(self) -> int:
        """Hash of the class's fields, computed once."""
        try:
            return self._hash
        except AttributeError:
            fields = self.__dict__
            # `model_construct` may leave fields unset; pydantic's own hash tolerates that. The
            # tuple's hash is the very value pydantic's generated `__hash__` returns; the
            # `hash` builtin is avoided only because the purity ratchet bars it from core.
            value = tuple.__hash__(
                tuple(fields.get(name) for name in type(self).__pydantic_fields__)
            )
            object.__setattr__(self, "_hash", value)
            return value

    def __deepcopy__(self, memo: dict[int, object] | None = None) -> Self:
        """The value itself when nothing in it can change, as for a tuple of strings."""
        if deeply_immutable(self):
            return self
        return super().__deepcopy__(memo)

    @model_serializer(mode="wrap")
    def _serialize(self, handler: SerializerFunctionWrapHandler, info: SerializationInfo) -> object:
        # Writes nothing of its own: `encode_json` asks each frozen value for its remembered text.
        return emit_fragment(self, handler, info.context)


"""Host-offered lifecycle abilities. "profile-capture" admits profile measurements."""


THIS_RUN = Scope(owner=RunId(root="this-run"), generation=0)
"""The scope a search names for work the run itself owns. Core replaces it with the run's own."""


RETRYABLE_FAILURES = frozenset(
    {ObservationStatus.FAILED, ObservationStatus.REJECTED, ObservationStatus.CANCELLED}
)
"""Statuses that, on a non-terminal observation, say the executor did not run the request."""


class KernelNotImplementedError(Exception):
    """Typed wave-1 rejection; callers may identify the exact owning lane."""

    code = RejectionCode.NOT_IMPLEMENTED_IN_KERNEL

    def __init__(self, area: Area, event_kind: str, subarea: str | None = None) -> None:
        """Name the owning area and its unimplemented event."""
        self.area = area
        self.event_kind = event_kind
        self.subarea = subarea
        owner = f"{area.value}/{subarea}" if subarea is not None else area.value
        super().__init__(f"{owner}: {event_kind} not implemented in kernel")


class SignalCycleError(Exception):
    """A reducer re-emitted a signal already consumed in this transition."""


class OperationSchemaRef(Value):
    """Operation schema ref lifecycle contract."""

    # Fields declared with this class hold exactly this class (a subclass instance is rebuilt as
    # one on validation), so one remembered JSON text per instance holds wherever it sits.
    model_config = ConfigDict(revalidate_instances="subclass-instances")

    kind: str = Field(min_length=1)
    request_schema: SchemaRef
    outcome_schema: SchemaRef
    lifecycle: LifecycleClass


class OperationDescriptor(OperationSchemaRef):
    """Operation descriptor lifecycle contract."""

    normalization: OperationNormalizationKind = OperationNormalizationKind.NONE
    inspect: bool = False
    cancel: bool = False
    watch: bool = False
    resource_pool: PoolId | None = None
    revision_authority: RevisionAuthority = RevisionAuthority.NONE

    @model_validator(mode="after")
    def lifecycle_contract(self) -> OperationDescriptor:
        """Require explicit resource and revision ownership declarations."""
        if self.lifecycle == LifecycleClass.OWNED_JOB and self.resource_pool is None:
            raise OperationDescriptorError("resource_pool", "owned jobs require a declared pool")
        if self.lifecycle != LifecycleClass.OWNED_JOB and self.resource_pool is not None:
            raise OperationDescriptorError("resource_pool", "only owned jobs declare pools")
        if (
            self.revision_authority != RevisionAuthority.NONE
            and self.lifecycle != LifecycleClass.IDEMPOTENT_WRITE
        ):
            raise OperationDescriptorError(
                "revision_authority", "revision mutations require idempotent writes"
            )
        if self.normalization == OperationNormalizationKind.SCOPE_REOPEN and (
            self.lifecycle != LifecycleClass.IDEMPOTENT_WRITE
            or not self.inspect
            or self.resource_pool is not None
            or self.revision_authority != RevisionAuthority.NONE
        ):
            raise OperationDescriptorError(
                "normalization", "scope reopen requires inspectable non-revision idempotent write"
            )
        return self


class OperationDescriptorError(ValueError):
    """Invalid operation ownership contract."""

    def __init__(self, path: str, detail: str) -> None:
        """Name the invalid declaration."""
        super().__init__(f"{path}: {detail}")


class Capabilities(Value):
    """Capabilities lifecycle contract."""

    lifecycle: frozenset[LifecycleCapability] = frozenset()
    operations: tuple[OperationDescriptor, ...] = ()

    @field_serializer("lifecycle", when_used="json")
    def _sorted_lifecycle(self, value: frozenset[LifecycleCapability]) -> list[str]:
        # A frozenset iterates in hash order, which varies with insertion history and the
        # string hash seed, so two equal sets could otherwise be written differently.
        """Write the set sorted, so equal sets give equal JSON."""
        return sorted(value)


MAX_SUBMISSIONS_PER_MEASUREMENT = 5
MAX_CORRECTIONS = 5
"""The most corrections of one reply any role may ask for."""
MAX_LOST_TURNS = 5
"""The most times any role may have a lost turn asked again."""
MAX_REASK_CHAIN = MAX_CORRECTIONS + MAX_LOST_TURNS
"""The longest chain of corrections and re-asks any role's bounds allow; core refuses a longer one.

A re-ask after a turn lost to its provider (``PROVIDER_UNAVAILABLE``) is no link of the chain:
the run's capacity gate bounds an outage, by its budget and the pause that follows it."""


class PureOption(Value):
    """Pure option lifecycle contract."""

    key: str = Field(min_length=1)
    value: str | int | Annotated[float, Field(allow_inf_nan=False)] | bool | None


class TaskTerms(Value):
    """What the task fixes about measuring and comparing candidates.

    Core carries these terms and holds none of them as a default: the host resolves them from
    the task's input bundle and domain, and a search reads them through ``ctx.task``. The
    evaluator, workload and environment identities that go with them are the run facts'.
    """

    recipe: ArtifactRef
    """The stored evaluation plan every measurement plan cites."""
    stages: tuple[TaskStage, ...]
    """Every stage the task can measure, in dependency order, with its time limit."""
    measured: tuple[str, ...]
    """The stages a candidate is measured on, in order: the task's gates."""
    queue_allowance: float = Field(ge=0, allow_inf_nan=False)
    """Seconds a measurement may wait in the evaluation queue."""
    accuracy_stage: str | None = Field(default=None, min_length=1)
    """The stage whose pass is the correctness gate."""
    benchmark_stage: str | None = Field(default=None, min_length=1)
    """The stage whose benchmark reading a candidate is compared with its base on, if any.

    A task without one has no number to compare, so core measures no base for it
    (`vs_core._comparison`)."""
    accuracy_gated: tuple[EvidenceKind, ...] = ()
    """Evidence kinds that count only if the accuracy stage passed."""
    operation_seconds: float = Field(gt=0, allow_inf_nan=False)
    """Seconds one read of the evidence (decoding it, verifying a parent) may take."""
    objectives: tuple[TaskObjective, ...] = ()
    """The directed axes candidates are ranked on, headline first."""
    relative_noise: float = Field(default=0.0, ge=0.0, lt=1.0)
    """The benchmark's declared variation: a reading must beat the input by more than this."""
    base_staleness: float = Field(default=21_600.0, gt=0, allow_inf_nan=False)
    """Seconds after which a measured base no longer stands for the host it was timed on.

    A candidate's comparison reads only base samples this young, on the run clock, and a base
    with none fresh is measured again (`vs_core._comparison`)."""
    runaway_evaluations: int = Field(default=40, ge=1)
    """How many evaluations one attempt's agent may ask for before the run warns of it.

    Evaluations have no allowance, so this refuses nothing: it marks only an egregious count
    (`vs_core._runaway`)."""
    profile_fields: tuple[str, ...] = ()
    """The measurement fields the task's profile capture supplies."""

    @property
    def requires_accuracy_proof(self) -> bool:
        """Whether a candidate must carry an accepted accuracy proof to be adopted.

        Only a task that measures its accuracy stage can produce one; a task without that gate
        adopts what its other gates admit.
        """
        return self.accuracy_stage is not None and self.accuracy_stage in self.measured

    @model_validator(mode="after")
    def _stages_exist(self) -> TaskTerms:
        known = {item.stage_id for item in self.stages}
        named = {
            *self.measured,
            *([self.accuracy_stage] if self.accuracy_stage else []),
            *([self.benchmark_stage] if self.benchmark_stage else []),
        }
        if unknown := sorted(named - known):
            raise ContractValidationError("measured", f"names stages the task lacks: {unknown}")
        return self


class RunFacts(Value):
    """Run facts lifecycle contract."""

    objective: str
    options: tuple[PureOption, ...] = ()
    constraints: tuple[PureOption, ...] = ()
    baseline: RevisionRef
    evaluator_digest: str
    workload_digest: str
    environment_digest: str
    dedicated_measurement_resources: bool = Field(default=False, exclude_if=unset)
    """Each measurement job gets its GPUs and CPU cores to itself, as the run's executor declares.

    The executor's capability, derived from how it submits jobs (a Slurm job with a bare
    ``--exclusive`` or an operator declaration), never from a backend's name. A run timed so is timed as an official
    measurement is, so only such a run may answer one (``vs_core._measurement_reuse``).
    """
    artifacts: tuple[ArtifactRef, ...] = ()
    task: TaskTerms | None = None
    """What the task fixes; a search that measures candidates reads it with ``ctx.task``."""


def escalate_stop(held: StopMode | None, asked: StopMode) -> StopMode:
    """The stop in force once ``asked`` arrives while ``held`` is: a stop only escalates.

    The one order of stop modes. A cancel replaces a drain and nothing replaces a cancel,
    so the requests' order never weakens a stop: every holder of a run's stop (the run's
    control channel, its bridge to core, core itself) folds requests through this.
    """
    return StopMode.CANCEL if StopMode.CANCEL in (held, asked) else StopMode.DRAIN


OPERATOR_ACTIONS: frozenset[ControlAction] = frozenset(
    {"cancel_attempt", "cancel_item", "set_max_parallel"}
)
"""The typed operator actions on core entities, which core applies alike in every search."""


class RequestBase(Value):
    """Canonical I/O record correlation, including the current occupancy episode.

    Attempt mutations carry admission_id. Canonical registration derives a
    missing attempt episode from the exact owner and generation. Explicit older
    episodes authorize only recorded cleanup, never mutation of a newer episode.
    Delayed observations may complete that cleanup but cannot mutate later
    ownership. Referenced IDs are allocated before state or signals refer to them.
    """

    request_id: RequestId | None = None
    scope: Scope
    depends_on: tuple[RequestId, ...] = ()
    decision_id: DecisionId | None = None
    admission_id: DecisionId | None = None


def validate_setup_failure(observation: Observation, failure: SetupFailureKind) -> None:
    """Classification requires terminal failure and grants no acceptance proof.

    An accepted-but-failed setup retains its lease cleanup obligations. Refunds
    require separate positive nonacceptance and recorded charge authority.
    """
    if failure != SetupFailureKind.UNKNOWN and not (
        observation.terminal
        and observation.status
        in (ObservationStatus.FAILED, ObservationStatus.REJECTED, ObservationStatus.CANCELLED)
    ):
        raise ContractValidationError("setup_failure", "requires terminal setup failure")


class IdentityTypeError(ValueError):
    """Different identity domains cannot be substituted."""

    def __init__(self) -> None:
        """Report a domain mismatch at validation ingress."""
        super().__init__("identity type mismatch")


class ReleaseDependency(Value):
    """An owned resource or disposition acknowledgement required for release."""

    kind: Literal["session", "job", "workspace", "operation", "request"]
    identity: SessionId | ResourceId | RequestId | OperationId

    @model_validator(mode="after")
    def domain_matches_kind(self) -> ReleaseDependency:
        """Reject accidental cross-domain release graph edges."""
        expected = {
            "session": SessionId,
            "job": ResourceId,
            "workspace": RequestId,
            "operation": OperationId,
            "request": RequestId,
        }
        if type(self.identity) is not expected[self.kind]:
            raise IdentityTypeError
        return self


class OperationWire(Value):
    """Operation wire lifecycle contract."""

    schema_ref: OperationSchemaRef
    payload_json: str


class ExecuteRegisteredOperation(RequestBase):
    """Execute a canonical registered wire with separately reserved input payload.

    Sessions A resolves the accepted Operation origin and reserves its inputs
    before Records A registers this final executable payload. Dispatch uses that
    sole canonical manifest; changing it under the same request ID is a conflict.
    """

    kind: Literal["execute_registered_operation"] = "execute_registered_operation"
    operation_id: OperationId
    operation: OperationWire
    retry_limit: Count
    inputs: tuple[ReservedInputOccurrence, ...] = ()

    @model_validator(mode="after")
    def distinct_reserved_occurrences(self) -> ExecuteRegisteredOperation:
        """An occurrence may appear once even when artifacts repeat."""
        if self.inputs and self.operation.schema_ref.lifecycle != LifecycleClass.SESSION_TURN:
            raise ContractValidationError(
                "inputs", "reserved inputs require SESSION_TURN ownership"
            )
        identities = tuple(item.input_id for item in self.inputs)
        if len(set(identities)) != len(identities):
            raise ContractValidationError("inputs", "duplicate input_id")
        return self
