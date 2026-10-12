"""The designer's round plan: the input to the hypothesis search.

``OrchestratorPlan`` is both the designer's structured reply schema and the
search plan type: the framework applies it in this order: optional
``revert_to_round`` git checkout, then implementer with ``task``, then judge
with ``pass_criteria``. The skill recommendations and title normalization live
here because they are parts of this plan's agent-facing contract.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from vs_runtime.api import AgentId

HypothesisStrategyDisposition = Literal["parked", "abandoned"]

HYPOTHESIS_TITLE_MAX_LEN = 60


class PlanFault(StrEnum):
    """What a designer's plan asked of the hypothesis ledger that it cannot accept. Closed."""

    DUPLICATE_UPDATES = "duplicate-updates"
    """``hypothesis_updates`` names one hypothesis more than once."""
    SELF_REFERENCE = "self-reference"
    """``hypothesis_updates`` names the plan's own new hypothesis."""
    REUSED_ID = "reused-id"
    """The new hypothesis ID was already used in this run."""
    UNKNOWN_HYPOTHESIS = "unknown-hypothesis"
    """An update names a hypothesis the ledger has never seen."""
    ACTIVE_HYPOTHESIS = "active-hypothesis"
    """An update parks or abandons the hypothesis that is still being worked on."""
    INCOMPLETE_HYPOTHESIS = "incomplete-hypothesis"
    """An update names a hypothesis that has no completed round yet."""


class InvalidPlanError(ValueError):
    """The designer returned an invalid hypothesis state transition.

    ``fault`` says which rule the plan broke and ``hypothesis_id`` the hypothesis it names
    (empty when the rule names none), so a caller can report the problem as data.
    """

    def __init__(self, fault: PlanFault, detail: str, hypothesis_id: str = "") -> None:
        """Name the violated plan invariant."""
        super().__init__(detail)
        self.fault = fault
        self.hypothesis_id = hypothesis_id

    @classmethod
    def duplicate_updates(cls, hypothesis_id: str = "") -> InvalidPlanError:
        """Report repeated prior hypothesis IDs."""
        if hypothesis_id:
            detail = f"duplicate strategy update for hypothesis {hypothesis_id!r}"
        else:
            detail = "hypothesis_updates names one hypothesis more than once"
        return cls(PlanFault.DUPLICATE_UPDATES, detail, hypothesis_id)

    @classmethod
    def self_reference(cls, hypothesis_id: str = "") -> InvalidPlanError:
        """Report a plan that updates its own new hypothesis."""
        return cls(
            PlanFault.SELF_REFERENCE, "hypothesis_updates includes the new hypothesis", hypothesis_id
        )

    @classmethod
    def reused_id(cls, hypothesis_id: str) -> InvalidPlanError:
        """Report a new hypothesis ID already used in this run."""
        return cls(
            PlanFault.REUSED_ID, f"hypothesis ID {hypothesis_id!r} was already used", hypothesis_id
        )

    @classmethod
    def unknown_hypothesis(cls, hypothesis_id: str) -> InvalidPlanError:
        """Report an update of a hypothesis the ledger does not have."""
        return cls(
            PlanFault.UNKNOWN_HYPOTHESIS,
            f"strategy update names unknown hypothesis {hypothesis_id!r}",
            hypothesis_id,
        )

    @classmethod
    def active_hypothesis(cls, hypothesis_id: str, disposition: str) -> InvalidPlanError:
        """Report an update that parks or abandons the active hypothesis."""
        return cls(
            PlanFault.ACTIVE_HYPOTHESIS,
            f"cannot {disposition} active hypothesis {hypothesis_id!r}",
            hypothesis_id,
        )

    @classmethod
    def incomplete_hypothesis(cls, hypothesis_id: str) -> InvalidPlanError:
        """Report an update of a hypothesis with no completed round."""
        return cls(
            PlanFault.INCOMPLETE_HYPOTHESIS,
            f"cannot update incomplete hypothesis {hypothesis_id!r}",
            hypothesis_id,
        )


class SkillResourceSelection(BaseModel):
    """Advisory selection of resources from one installed agent skill.

    The planner can recommend these resources while implementation and review
    agents remain free to select different installed skills. Paths are relative
    to the named skill root and are resolved before another agent sees them.
    """

    model_config = ConfigDict(extra="forbid")

    skill: str = Field(min_length=1, description="Exact installed skill name.")
    resource_paths: list[str] = Field(
        default_factory=list,
        description=(
            "Optional files relative to the skill root. An empty list selects "
            "the skill router without preselecting a resource."
        ),
    )
    purpose: str = Field(
        min_length=1,
        description="Short reason these resources may help with the current work.",
    )

    @field_validator("skill", "purpose")
    @classmethod
    def _strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            message = "must contain non-whitespace text"
            raise ValueError(message)
        return value


def truncate_hypothesis_title(text: str) -> str:
    """Truncate a normalized title on a word boundary, adding an ellipsis."""
    if len(text) <= HYPOTHESIS_TITLE_MAX_LEN:
        return text
    truncated = text[: HYPOTHESIS_TITLE_MAX_LEN - 1]
    boundary = truncated.rfind(" ")
    if boundary > 0:
        truncated = truncated[:boundary]
    return truncated.rstrip() + "…"


def normalize_hypothesis_title(title: str) -> str:
    """Strip, collapse internal whitespace, and truncate a plan title.

    Return an empty string when the input carries no text.
    """
    return truncate_hypothesis_title(" ".join(title.split()))


def derive_hypothesis_title(claim: str) -> str | None:
    """Derive a display title from a hypothesis claim's first sentence.

    Return ``None`` when the claim carries no text.
    """
    stripped = claim.strip()
    if not stripped:
        return None
    first_line = stripped.splitlines()[0]
    first_sentence = first_line.split(". ", 1)[0].rstrip(". ")
    collapsed = " ".join(first_sentence.split())
    if not collapsed:
        return None
    return truncate_hypothesis_title(collapsed)


class HypothesisStrategyUpdate(BaseModel):
    """One structured update to a previously completed hypothesis."""

    model_config = ConfigDict(extra="forbid")

    # Exact: it is matched against recorded hypothesis IDs, never normalized.
    hypothesis_id: AgentId
    disposition: HypothesisStrategyDisposition
    reason: str = Field(min_length=1)

    @field_validator("reason")
    @classmethod
    def _strip_non_empty(cls, value: str) -> str:
        if not (stripped := value.strip()):
            message = "must not be blank"
            raise ValueError(message)
        return stripped


class OrchestratorPlan(BaseModel):
    """The per-round plan produced by the orchestrator.

    The framework applies the plan in this order: optional
    ``revert_to_round`` git checkout, then implementer with ``task``,
    then judge with ``pass_criteria``. The loop always runs the full
    ``max_rounds`` budget; there is no early-stop signal from the
    orchestrator.
    """

    hypothesis_id: str = Field(
        default="",
        description=(
            "Stable short identifier for this round's new hypothesis. Never reuse "
            "an identifier used earlier in this run."
        ),
    )
    hypothesis_updates: list[HypothesisStrategyUpdate] = Field(
        default_factory=list,
        description=(
            "Strategic parked/abandoned updates for previously completed hypotheses. "
            "These are persisted by the framework; roadmap prose is not authoritative."
        ),
    )
    hypothesis: str = Field(
        default="",
        description="Causal, falsifiable claim explaining why the proposed change should help.",
    )
    title: str = Field(
        default="",
        description=(
            f"Short plain-language title (<={HYPOTHESIS_TITLE_MAX_LEN} chars, no "
            f"trailing period or 'Hypothesis:' prefix)."
        ),
    )
    activation_evidence: str = Field(
        default="",
        description=(
            "Concise observable evidence that the intended code path or mechanism "
            "ran; include only hypothesis-specific signals."
        ),
    )
    falsification_criteria: str = Field(
        default="",
        description="Evidence that would disprove the causal hypothesis for this workload.",
    )
    expected_effect: str = Field(
        default="",
        description=(
            "Analytical forecast range used to prioritize and later calibrate the "
            "hypothesis; this is not an acceptance threshold."
        ),
    )
    minimum_acceptance_criteria: str = Field(
        default="",
        description=(
            "Minimum observed end-to-end benefit and allowed tradeoffs that justify "
            "retaining the change, derived separately from forecast uncertainty, "
            "benchmark noise, and implementation cost."
        ),
    )
    invariants: str = Field(
        default="",
        description=(
            "Hypothesis-specific invariants and diagnostics. Cite authoritative "
            "objective/runtime paths for stable constraints instead of restating them."
        ),
    )
    recommended_skills: list[SkillResourceSelection] = Field(
        default_factory=list,
        description=(
            "Zero or more installed skill resources that may help implement this "
            "hypothesis. Recommendations are advisory, not an allowlist or gate."
        ),
    )
    task: str = Field(
        description=(
            "Causally complete component/interface and staged-evaluation delta handed "
            "to the implementer, without restating stable authoritative contracts."
        )
    )
    pass_criteria: str = Field(
        description=(
            "Hypothesis-specific activation, correctness, cleanup, and evidence gates. "
            "Reference stable trusted gates by path; they run separately when configured."
        )
    )
    request_official_evaluation: bool = Field(
        default=False,
        description=(
            "Run the framework-owned canonical evaluation after this hypothesis "
            "passes independent review, even when the normal sparse-evaluation "
            "cadence is not yet due."
        ),
    )
    revert_to_round: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Optional round whose candidate tree the framework materializes "
            "before the implementer runs; 0 is the input implementation. "
            "Experiment memory is preserved and Git HEAD does not move."
        ),
    )
    reasoning: str = Field(
        description="Brief decisive comparison supporting this round's parent and mechanism."
    )
