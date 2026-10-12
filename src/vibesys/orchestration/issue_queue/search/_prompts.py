"""Typed data the search hands to the prompt renderer.

The search never builds prompt text. It sends a `PromptContext`; the agent pipeline asks the
render operation to render the named template and store the artifacts. Each context mirrors
the variables of one template. The board is part of the data: agents on the core lifecycle
work in an isolated checkout, so they read the issue under work in full and every other issue
as a list from the prompt, not from files. The contexts of the core protocol (a reply that
broke its schema, a resumed turn, a wait nobody recorded) come from `vibesys.prompts`.
"""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field

from vibesys.metrics import MetricRow, PartialRow
from vibesys.prompts import ReplyCorrectionPrompt, ResumePrompt, WaitUnrecordedPrompt
from vs_core.api.search import EvidenceId, EvidenceKind, ObservationStatus, RevisionRef, Value


class PromptTemplate(StrEnum):
    """Closed set of templates the search can request."""

    IMPLEMENT = "implement"
    REVIEW = "review"
    PERFORMANCE = "performance"


class IssueBrief(Value):
    """One issue as the list the agents see."""

    id: int = Field(ge=1)
    type: str
    status: str
    title: str
    attempts: int = Field(ge=0)


class IssueDetail(IssueBrief):
    """The issue under work, in full."""

    description: str


class ImplementPrompt(Value):
    """One bounded implementation attempt of one issue."""

    template: Literal[PromptTemplate.IMPLEMENT] = PromptTemplate.IMPLEMENT
    issue: IssueDetail
    board: tuple[IssueBrief, ...] = ()
    # The judge's feedback and analysis when the previous attempt at this issue failed review.
    feedback: str | None = None
    analysis: str | None = None


class ReviewEvaluation(Value):
    """One trusted evaluation record of the candidate under review, as the judge reads it.

    The ledger holds identities and the observation's status; ``passed`` is None because the
    issue queue never asks the evidence owner to decode a record.
    """

    evidence_id: EvidenceId
    kind: EvidenceKind
    revision: RevisionRef
    status: ObservationStatus
    passed: bool | None = None
    metrics: tuple[MetricRow, ...] = ()
    partial: PartialRow | None = None
    feedback: str = ""


class ReviewPrompt(Value):
    """Independent review of one issue's candidate."""

    template: Literal[PromptTemplate.REVIEW] = PromptTemplate.REVIEW
    issue: IssueDetail
    board: tuple[IssueBrief, ...] = ()
    summary: str
    files_touched: tuple[str, ...] = ()
    self_check: str = ""
    # The trusted evaluations this issue's candidate was measured by, stated at render time.
    evaluations: tuple[ReviewEvaluation, ...] = ()


class PriorPerformance(Value):
    """One finished performance evaluation, shown to the next one."""

    iteration: int = Field(gt=0)
    primary_trend: str
    secondary_trend: str
    metrics_json: str
    new_issues: int = Field(ge=0)


class PerformancePrompt(Value):
    """One measured performance assessment and the issues it may file."""

    template: Literal[PromptTemplate.PERFORMANCE] = PromptTemplate.PERFORMANCE
    iteration: int = Field(gt=0)
    max_new_issues: int = Field(gt=0)
    board: tuple[IssueBrief, ...] = ()
    prior: tuple[PriorPerformance, ...] = ()
    load_levels_json: str | None = None


type PromptContext = Annotated[
    ImplementPrompt
    | ReviewPrompt
    | PerformancePrompt
    | ResumePrompt
    | ReplyCorrectionPrompt
    | WaitUnrecordedPrompt,
    Field(discriminator="template"),
]
"""The union the render operation carries; `AgentTurns` is typed by it."""
