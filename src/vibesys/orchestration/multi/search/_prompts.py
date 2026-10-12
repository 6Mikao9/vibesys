"""Typed data the search hands to the prompt renderer.

The search never builds prompt text. It sends a `PromptContext`; the render operation renders the
named template with `vs_prompts` and stores the artifacts. The evidence the legacy loop kept in
progress files (round history, the Pareto frontier, carried notices, the profile, the
implementer's claims for the judge) is rendered from the typed rows below, because a round's
workspace is an isolated child of a revision and holds no progress files. The contexts of the core
protocol (a reply that broke its schema, a resumed turn, a wait nobody recorded) come from
`vibesys.prompts`, and the correction of a plan the ledger refused from `vibesys.serial_search`.
"""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field

from vibesys.orchestration.multi.search._values import ArchiveConflictView, Repair
from vibesys.prompts import ReplyCorrectionPrompt, ResumePrompt, WaitUnrecordedPrompt
from vibesys.serial_search.api import NoReply, OfficialReason, PlanCorrection, PlanEvidence
from vs_core.api.search import EvidenceRef, RevisionRef, Value


class PromptTemplate(StrEnum):
    """Closed set of multi's own templates (the shared ones are `CoreProtocolTemplate`)."""

    PRE_ROUND = "pre_round"
    PROFILER = "profiler"
    PLAN = "plan"
    IMPLEMENT = "implement"
    CONTINUE = "continue"
    JUDGE = "judge"


class PreRoundPrompt(Value):
    """The routing decision before a new hypothesis: is fresh profile evidence wanted?"""

    template: Literal[PromptTemplate.PRE_ROUND] = PromptTemplate.PRE_ROUND
    round_number: int = Field(gt=0)
    has_history: bool
    profiler_kind: str
    profile_execution: str
    regression_kind: str | None = None
    regression_round: int | None = None
    exhaustion_feedback: str | None = None
    exhaustion_round: int | None = None


class ProfilerPrompt(Value):
    """One advisory profiling turn: where to look and where to write bounded evidence."""

    template: Literal[PromptTemplate.PROFILER] = PromptTemplate.PROFILER
    round_number: int = Field(gt=0)
    profiler_kind: str
    profile_focus: str
    benchmark_command: str | None = None
    profile_execution: str
    profiler_support_name: str
    profiler_mcp_name: str
    evidence_directory: str


class PlanPrompt(PlanEvidence):
    """One new-hypothesis planning request."""

    template: Literal[PromptTemplate.PLAN] = PromptTemplate.PLAN
    base_revision: RevisionRef


class Claim(Value):
    """One named number the implementer reported."""

    name: str
    value: float


class ImplementerClaims(Value):
    """What the implementer reported, as untrusted data for the judge."""

    summary: str
    expected_behavior: str
    hypothesis_outcome: str
    evidence: str = ""
    next_step: str = ""
    perf_metric: float | None = None
    perf_unit: str | None = None
    metrics: tuple[Claim, ...] = ()
    evaluation_artifact: str | None = None
    candidate_disposition: str
    candidate_metrics: tuple[Claim, ...] = ()
    candidate_evaluation_artifact: str | None = None
    candidate_operating_point: str = ""
    candidate_retention_reason: str = ""
    validation_recipe_artifact: str | None = None


class HypothesisPlan(Value):
    """The designer's typed plan of the hypothesis a round works on."""

    hypothesis_id: str
    title: str = ""
    hypothesis: str
    task: str
    pass_criteria: str
    invariants: str = ""
    activation_evidence: str = ""
    falsification_criteria: str = ""
    minimum_acceptance_criteria: str = ""


class GateFacts(Value):
    """What the framework's gates and parent choice already established for the hypothesis."""

    revert_applied: bool = False
    revert_round: int | None = None
    revert_commit: str | None = None
    revalidation_pending: bool = False
    approved_evaluation_artifact: str | None = None
    approved_perf_metric: float | None = None
    approved_perf_unit: str | None = None


class ImplementPrompt(Value):
    """The first implementation request of a hypothesis, or a retry of it."""

    template: Literal[PromptTemplate.IMPLEMENT] = PromptTemplate.IMPLEMENT
    round_number: int = Field(gt=0)
    retry: int = Field(ge=1)
    plan: HypothesisPlan
    base_revision: RevisionRef
    framework: GateFacts
    repair: Repair | None = None
    no_reply: NoReply | None = None
    official_reason: OfficialReason | None = None
    benchmark_enabled: bool = True
    active_component: str | None = None
    reference_path: str = "."
    interface: str = "inprocess"


class ContinuePrompt(Value):
    """A bounded continuation of a hypothesis the implementer asked to keep working on."""

    template: Literal[PromptTemplate.CONTINUE] = PromptTemplate.CONTINUE
    round_number: int = Field(gt=0)
    retry: int = Field(ge=1)
    plan: HypothesisPlan
    base_revision: RevisionRef
    continuation_step: str
    framework: GateFacts
    repair: Repair | None = None
    no_reply: NoReply | None = None


class JudgePrompt(Value):
    """The independent review of one implementer reply."""

    template: Literal[PromptTemplate.JUDGE] = PromptTemplate.JUDGE
    round_number: int = Field(gt=0)
    retry: int = Field(ge=1)
    plan: HypothesisPlan
    claims: ImplementerClaims
    framework: GateFacts
    conflict: ArchiveConflictView | None = None
    official_reason: OfficialReason | None = None
    benchmark_enabled: bool = True
    interface: str = "inprocess"
    # The trusted evaluations the candidate was measured by, stated at render time.
    cited_evidence: tuple[EvidenceRef, ...] = ()


type PromptContext = Annotated[
    PreRoundPrompt
    | ProfilerPrompt
    | PlanPrompt
    | PlanCorrection
    | ImplementPrompt
    | ContinuePrompt
    | JudgePrompt
    | ResumePrompt
    | ReplyCorrectionPrompt
    | WaitUnrecordedPrompt,
    Field(discriminator="template"),
]
