"""The plain values the evolve search keeps and passes around, with no behavior of their own.

Every value is a strict, frozen `Value` with closed fields. Reasons are enums with the typed
facts that go with them. The only free text is text an agent or an evaluator wrote (a reviewer's
feedback, what a failed check printed), carried as data for a later prompt and never written by
the search: a reason the framework itself found is recorded as a `Failure`, and the text an
individual keeps for it is derived from that value (`Failure.lesson`).
"""

from enum import StrEnum

from pydantic import Field, model_validator

from vibesys.metrics import AcceptedReading
from vibesys.orchestration.evolve.population import Metrics
from vs_core.api.search import (
    CheckpointDecline,
    EvidenceKind,
    FailureReason,
    InvocationRef,
    ObservationStatus,
    RejectionCode,
    RevisionRef,
    SettlementId,
    TurnFailureKind,
    Value,
    WaitRefusal,
)


class Role(StrEnum):
    """Agent roles; values match the `evolve-<role>` role ids."""

    MUTATOR = "mutator"
    JUDGE = "judge"
    PROFILER = "profiler"


class UnreachableReason(StrEnum):
    """Typed reason a decision the search wants cannot be made."""

    RESUME_CAPABILITY_NOT_OFFERED = "resume-capability-not-offered"


class Unreachable(Value):
    """A decision not made, with the missing core contract named."""

    reason: UnreachableReason
    subject: str


# -- why a slot ended ---------------------------------------------------------------------------


class FailureKind(StrEnum):
    """Why a slot, or one step of it, went wrong. Closed."""

    STOPPED = "stopped"
    """The operator stopped the run."""
    NO_RETAINED_PARENT = "no-retained-parent"
    """The proposal names a parent no retained revision backs, so the slot never started."""
    REFUSED = "refused"
    """Core refused a decision of the slot; ``cause`` says why."""
    ENDED = "ended"
    """A wait of the slot ended without a result; ``ended`` says why."""
    OUT_OF_BUDGET = "out-of-budget"
    TURN_FAILED = "turn-failed"
    """An agent's turn ended without a reply; ``role`` and ``turn_failure`` say how."""
    INVALID_REPLY = "invalid-reply"
    RENDER_FAILED = "render-failed"
    WAIT_REFUSED = "wait-refused"
    """An agent asked to wait for evaluations and the run could not honor it."""
    CHECKPOINT_DECLINED = "checkpoint-declined"
    REVIEW_REJECTED = "review-rejected"
    """The judge's verdict was a fail; its feedback, when it wrote any, is the lesson."""
    GATE_FAILED = "gate-failed"
    """A configured gate rejected the candidate; ``gate`` says which."""
    NOT_MEASURED = "not-measured"
    """The machinery could not measure the candidate, which says nothing about it."""
    EVIDENCE_REFUSED = "evidence-refused"
    EVIDENCE_UNDECODABLE = "evidence-undecodable"
    EXPORT_FAILED = "export-failed"
    NOT_ELIGIBLE = "not-eligible"
    """Every gate passed, yet core did not accept the candidate as eligible."""


_FIELDS: dict[FailureKind, tuple[frozenset[str], frozenset[str]]] = {
    FailureKind.REFUSED: (frozenset({"cause"}), frozenset()),
    FailureKind.ENDED: (frozenset({"ended"}), frozenset({"role"})),
    FailureKind.TURN_FAILED: (frozenset({"role"}), frozenset({"turn_failure", "ended"})),
    FailureKind.INVALID_REPLY: (frozenset({"role"}), frozenset()),
    FailureKind.RENDER_FAILED: (frozenset({"role"}), frozenset()),
    FailureKind.WAIT_REFUSED: (frozenset({"role", "refusal"}), frozenset()),
    FailureKind.CHECKPOINT_DECLINED: (frozenset({"declined"}), frozenset()),
    FailureKind.GATE_FAILED: (frozenset({"gate"}), frozenset()),
    FailureKind.NOT_MEASURED: (frozenset({"status"}), frozenset({"output"})),
    FailureKind.EXPORT_FAILED: (frozenset(), frozenset({"output"})),
}

_FIELD_NAMES = ("role", "cause", "ended", "turn_failure", "refusal", "declined", "gate", "status")


class Failure(Value):
    """A typed account of what went wrong, with only the fields its kind has."""

    kind: FailureKind
    role: Role | None = None
    cause: RejectionCode | None = None
    ended: FailureReason | None = None
    turn_failure: TurnFailureKind | None = None
    refusal: WaitRefusal | None = None
    declined: CheckpointDecline | None = None
    gate: EvidenceKind | None = None
    status: ObservationStatus | None = None
    output: str = ""
    """What an evaluator or an owner printed, kept as captured data."""

    @model_validator(mode="after")
    def fields_match_kind(self) -> "Failure":
        """Refuse a field that does not belong to the kind, or a kind missing its field."""
        required, optional = _FIELDS.get(self.kind, (frozenset(), frozenset()))
        present = {name for name in _FIELD_NAMES if getattr(self, name) is not None} | (
            {"output"} if self.output else set()
        )
        if missing := required - present:
            message = f"{self.kind.value} needs {sorted(missing)[0]}"
            raise ValueError(message)
        if extra := present - required - optional:
            message = f"{sorted(extra)[0]} does not belong to {self.kind.value}"
            raise ValueError(message)
        return self

    @property
    def lesson(self) -> str:
        """The short fixed text a failed individual keeps when nobody else wrote its feedback.

        It names the kind and the typed facts that go with it, and nothing else.
        """
        facts = (
            getattr(self, name).value for name in _FIELD_NAMES if getattr(self, name) is not None
        )
        return ": ".join(("framework", self.kind.value, *facts))


# -- what a selector chose, what a profiler reported, what core settled -------------------------


class SlotProposal(Value):
    """The selector's choice for one slot of a generation, by individual id."""

    parent_id: int = Field(gt=0)
    inspiration_ids: tuple[int, ...] = ()
    policy_parent_id: str | None = None
    target_island: int | None = None


class ProfileReading(Value):
    """What the advisory profiler reported, the part that can become fitness."""

    perf_metric: float | None = Field(default=None, allow_inf_nan=False)
    perf_unit: str | None = None
    metrics: Metrics = ()


class SettledSlot(Value):
    """What core settled for a slot's attempt."""

    settlement: SettlementId
    retained: RevisionRef | None = None
    eligible: bool = False


class Retained(Value):
    """The settlement a retained individual's revision rests on."""

    individual: int = Field(gt=0)
    settlement: SettlementId
    revision: RevisionRef


class Advisory(Value):
    """A step that went wrong without costing the individual anything, kept for an operator."""

    subject: str
    failure: Failure


# -- the slot's plan and what it found ------------------------------------------------------------


class SlotPlan(Value):
    """What a slot was opened to do: fixed when its frame opens, never changed after."""

    number: int = Field(gt=0)
    """Position in the frame; slots are admitted to the population in this order."""
    search_generation: int = Field(ge=0)
    """The search generation of the child; 0 for a cold start."""
    proposal: SlotProposal | None = None
    base: RevisionRef
    repair_seed: bool = False
    """The workspace already holds the previous cold start's files, to be repaired in place."""

    @property
    def cold_start(self) -> bool:
        """Whether the slot looks for the first passing individual rather than evolving one."""
        return self.search_generation == 0


class Finding(Value):
    """What a slot established so far, filled in as its phases pass.

    It reads as the finished attempt the shared settlement policy judges. ``failure`` is a
    failed attempt; ``note`` only explains the individual's feedback (a verdict that failed, a
    gate that rejected the candidate), and leaves the attempt itself unfailed. ``advisory`` is a
    step that cost the individual nothing.
    """

    summary: str = ""
    candidate: RevisionRef | None = None
    feedback: str = ""
    """The reviewer's or a failed gate's own words, verbatim."""
    review_passed: bool | None = None
    judge_invocation: InvocationRef | None = None
    readings: tuple[AcceptedReading, ...] = ()
    profile: ProfileReading | None = None
    patch: str | None = None
    """The candidate's patch text, exported for a selector that needs code (OpenEvolve)."""
    passed: bool = False
    unmeasured: bool = False
    """The candidate was never measured (the machinery failed), so it earns no individual."""
    failure: Failure | None = None
    note: Failure | None = None
    advisory: Failure | None = None
    unreachable: Unreachable | None = None

    @property
    def lesson(self) -> str:
        """The feedback the individual records: the words written for it, else the framework's."""
        explained = self.failure or self.note
        return self.feedback or ("" if explained is None else explained.lesson)

    @property
    def outcome(self) -> None:
        """Evolve's mutator reports no outcome of its own."""
        return None

    @property
    def stopped(self) -> bool:
        """Whether the operator stopped the attempt (which counts as ``failed`` as well)."""
        return self.failure is not None and self.failure.kind is FailureKind.STOPPED

    @property
    def failed(self) -> bool:
        """Whether the attempt recorded a failure, an operator stop included."""
        return self.failure is not None
