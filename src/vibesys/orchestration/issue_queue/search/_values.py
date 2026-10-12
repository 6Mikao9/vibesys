"""Typed values the issue-queue search keeps: roles, failures, replies' facts and endings.

Every model is a strict, frozen, tuple-only `Value`, so the state round-trips through the
envelope codec unchanged. Nothing here is free text the search wrote: a failure or an ending
is an enum and the facts that belong to it, and the words are made where they are shown.
"""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field

from vibesys.orchestration.issue_queue.models import PerformanceTrend
from vs_core.api.search import (
    CheckpointDecline,
    FailureReason,
    RejectionCode,
    RevisionRef,
    SettlementId,
    TurnFailureKind,
    Value,
    WaitRefusal,
)
from vs_issue_tracker.api import IssueType, OutcomeStatus


class Role(StrEnum):
    """Agent roles; values match the `plain-<role>` role ids."""

    IMPLEMENTER = "implementer"
    JUDGE = "judge"
    PERF = "perf-evaluator"


class JobKind(StrEnum):
    """What a job does with its attempt."""

    ISSUE = "issue"
    PERF = "perf"


class FailureKind(StrEnum):
    """Why a job could not be completed. Closed."""

    STOPPED = "stopped"
    """The operator stopped the run."""

    ENDED = "ended"
    """A wait ended without a result; ``reason`` says how."""

    CHECKPOINT_DECLINED = "checkpoint-declined"
    """Core did not retain the workspace of a write turn; ``declined`` says why."""

    BOARD_REFUSED = "board-refused"
    """The tracker did not take a write; ``status`` is its answer."""

    BOARD_UNKNOWN = "board-unknown"
    """A board write stayed unknown after every send."""


class JobFailure(Value):
    """A typed reason a job failed, with only the fields its kind has."""

    kind: FailureKind
    role: Role | None = None
    reason: FailureReason | None = None
    cause: RejectionCode | None = None
    refusal: WaitRefusal | None = None
    turn_failure: TurnFailureKind | None = None
    declined: CheckpointDecline | None = None
    status: OutcomeStatus | None = None


class FiledIssue(Value):
    """An issue an agent asked to file, as the reply named it."""

    type: IssueType
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)


class Implemented(Value):
    """What the implementer's reply said, kept to record the attempt and to brief the judge."""

    summary: str
    files_touched: tuple[str, ...] = ()
    self_check: str


class Judged(Value):
    """The judge's verdict and the bug it filed."""

    passed: bool
    analysis: str
    feedback: str
    filed: tuple[FiledIssue, ...] = ()


class Evaluation(Value):
    """The performance evaluator's account, kept to record the evaluation once it settles.

    ``metrics_json`` is the sorted-key JSON of the metrics the evaluator reported: they are an
    open document (a mapping with arbitrary keys), which the state's tuple-only values cannot
    hold, and the search only shows them to the next evaluation.
    """

    primary_trend: PerformanceTrend
    secondary_trend: PerformanceTrend | None
    metrics_json: str
    filed: tuple[FiledIssue, ...] = ()


class PerformanceRecord(Value):
    """A finished performance evaluation, shown to the next one."""

    iteration: int = Field(gt=0)
    primary_trend: PerformanceTrend
    secondary_trend: PerformanceTrend | None
    metrics_json: str
    new_issues: tuple[int, ...] = ()


class Tip(Value):
    """The revision the next job builds on: the last closed issue's candidate, else the input."""

    revision: RevisionRef | None = None
    settlement: SettlementId | None = None


# -- how a run ends ----------------------------------------------------------------------------


class BlankObjective(Value):
    """The task has no objective, so there is no first issue to file."""

    kind: Literal["blank_objective"] = "blank_objective"


class RoundBudgetSpent(Value):
    """The round budget is spent."""

    kind: Literal["round_budget_spent"] = "round_budget_spent"
    rounds: int = Field(gt=0)


class AllBlocked(Value):
    """Work remains and every remaining issue is blocked."""

    kind: Literal["all_blocked"] = "all_blocked"
    remaining: int = Field(gt=0)


class Drained(Value):
    """The board is drained and the last evaluation filed nothing."""

    kind: Literal["drained"] = "drained"


class TrackerUse(StrEnum):
    """What the run was asking of the tracker."""

    READING = "reading the board"
    BOOTSTRAP = "filing the bootstrap issue"


class TrackerUnavailable(Value):
    """The tracker could not be used."""

    kind: Literal["tracker_unavailable"] = "tracker_unavailable"
    during: TrackerUse
    status: OutcomeStatus | None = None
    reason: FailureReason | None = None


class JobFailed(Value):
    """A job failed, and a run does not go on past a job that failed."""

    kind: Literal["job_failed"] = "job_failed"
    job: JobKind
    issue_id: int | None
    iteration: int = Field(gt=0)
    failure: JobFailure


type Ending = Annotated[
    BlankObjective | RoundBudgetSpent | AllBlocked | Drained | TrackerUnavailable | JobFailed,
    Field(discriminator="kind"),
]
"""Why the run stopped working, once nothing is in flight."""
