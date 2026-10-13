"""Typed data the search hands to the prompt renderer.

The search never builds prompt text. It sends a `PromptContext`; the render operation renders the
role's template with `vs_prompts` and stores the artifacts. Each context mirrors the variables of
one template. The board is part of the data: agents work in an isolated checkout, so they read
the issue under work in full and every other issue as a list from the prompt, not from files.
The contexts of the exchanges core starts (a reply that broke its schema, a resumed turn, a wait
nobody recorded) are core's, rendered by the roles' `EXCHANGES` templates.
"""

from enum import StrEnum
from typing import Annotated, ClassVar, Literal

from pydantic import ConfigDict, Field

from vibesys.metrics import MetricRow, PartialRow
from vibesys.orchestration.issue_queue.search import _roles as roles
from vs_core.api.search import (
    EvidenceId,
    EvidenceKind,
    ObservationStatus,
    RevisionRef,
    RolePromptContext,
    SchemaRef,
    Value,
)


class PromptTemplate(StrEnum):
    """Closed set of templates the search can request."""

    IMPLEMENT = "implement"
    REVIEW = "review"
    PERFORMANCE = "performance"


class IssueBrief(Value):
    """One issue as the list the agents see."""

    # Fields declared with this class hold exactly this class (a subclass instance is rebuilt as
    # one on validation), so one remembered JSON text per instance holds wherever it sits.
    model_config = ConfigDict(revalidate_instances="subclass-instances")

    id: int = Field(ge=1)
    type: str
    status: str
    title: str
    attempts: int = Field(ge=0)


class IssueDetail(IssueBrief):
    """The issue under work, in full."""

    description: str


class ReviewEvaluation(Value):
    """One trusted evaluation result of the candidate under review, as the judge reads it.

    ``passed`` is None when the evidence owner could not decode the record; the status is core's
    own observation of the evaluation job. ``feedback`` is the end of the evaluator's failure
    text.
    """

    evidence_id: EvidenceId
    kind: EvidenceKind
    revision: RevisionRef
    status: ObservationStatus
    passed: bool | None = None
    metrics: tuple[MetricRow, ...] = ()
    partial: PartialRow | None = None
    feedback: str = ""


class ImplementPrompt(RolePromptContext):
    """One bounded implementation attempt of one issue."""

    reply_schema: ClassVar[SchemaRef | None] = roles.IMPLEMENTER.reply_schema

    template: Literal[PromptTemplate.IMPLEMENT] = PromptTemplate.IMPLEMENT
    issue: IssueDetail
    board: tuple[IssueBrief, ...] = ()
    feedback: str | None = None
    """The judge's feedback when the previous attempt at this issue failed review."""
    analysis: str | None = None
    """The judge's analysis of that attempt."""


class ReviewPrompt(RolePromptContext):
    """Independent review of one issue's candidate."""

    reply_schema: ClassVar[SchemaRef | None] = roles.JUDGE.reply_schema

    template: Literal[PromptTemplate.REVIEW] = PromptTemplate.REVIEW
    issue: IssueDetail
    board: tuple[IssueBrief, ...] = ()
    summary: str
    files_touched: tuple[str, ...] = ()
    self_check: str = ""
    evaluations: tuple[ReviewEvaluation, ...] = ()
    """Every trusted evaluation of the candidate, oldest first; empty when none exists yet."""


class PriorPerformance(Value):
    """One finished performance evaluation, shown to the next one."""

    iteration: int = Field(gt=0)
    throughput_trend: str
    latency_trend: str
    metrics_json: str
    new_issues: int = Field(ge=0)


class PerformancePrompt(RolePromptContext):
    """One measured performance assessment and the issues it may file."""

    reply_schema: ClassVar[SchemaRef | None] = roles.PERF_EVALUATOR.reply_schema

    template: Literal[PromptTemplate.PERFORMANCE] = PromptTemplate.PERFORMANCE
    iteration: int = Field(gt=0)
    max_new_issues: int = Field(gt=0)
    board: tuple[IssueBrief, ...] = ()
    prior: tuple[PriorPerformance, ...] = ()
    load_levels_json: str | None = None


type PromptContext = Annotated[
    ImplementPrompt | ReviewPrompt | PerformancePrompt, Field(discriminator="template")
]
"""The union the render operation carries; the exchanges core starts are core's own contexts."""
