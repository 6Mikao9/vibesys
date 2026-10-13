"""The keyed board changes and progress entries each step of a job makes, as pure builders.

Every change carries a key unique to the fact it records (run, job, step). The tracker stores it
with the issue or event it writes, which is how a write sent again after its effect stayed
unknown is recognized as already landed instead of repeated.
"""

from collections.abc import Sequence

from vibesys.orchestration.issue_queue.models import (
    IssueImplementerResult,
    IssueJudgeResult,
    NewBug,
    NewIssue,
)
from vibesys.orchestration.issue_queue.search._board import BOOTSTRAP_TEMPLATE
from vibesys.orchestration.issue_queue.search._values import IssueJob, PerfJob, change_key
from vs_core.api.search import Value
from vs_issue_tracker.api import (
    CreateIssue,
    IssueChange,
    IssueStatus,
    IssueType,
    LogProgress,
    RecordAttempt,
    SetIssueStatus,
    TemplateBody,
    TextBody,
)

NOTE_LIMIT = 200
"""How much of an implementer's summary the attempt record's note keeps."""
FEEDBACK_LIMIT = 500
"""How much of a judge's feedback the issue's status note keeps."""


class Writes(Value):
    """One step's board writes: the changes applied as a batch, and the log entries beside them."""

    changes: tuple[IssueChange, ...] = ()
    logs: tuple[LogProgress, ...] = ()


def bootstrap(run_id: str, title: str, iteration: int) -> Writes:
    """File the run's first issue: the task itself, described from the bootstrap template."""
    create = CreateIssue(
        key=change_key(run_id, "run", "bootstrap"),
        type=IssueType.FEATURE,
        title=title,
        body=TemplateBody(template=BOOTSTRAP_TEMPLATE),
        created_by="loop:bootstrap",
        iteration=iteration,
    )
    return Writes(changes=(create,))


def claim(job: IssueJob) -> Writes:
    """Mark the issue in progress."""
    change = SetIssueStatus(
        key=job.change_key("claim"),
        issue_id=job.issue.id,
        status=IssueStatus.IN_PROGRESS,
        actor="loop",
        iteration=job.iteration,
        note="claimed for processing",
    )
    return Writes(changes=(change,))


def record(job: IssueJob, spent: int, reply: IssueImplementerResult) -> Writes:
    """Count one attempt on the issue and append its record."""
    payload = reply.model_dump_json()
    attempt = RecordAttempt(
        key=job.change_key(f"attempt.{spent}"),
        issue_id=job.issue.id,
        actor="implementer",
        iteration=job.iteration,
        note=reply.summary[:NOTE_LIMIT],
        payload_json=payload,
    )
    log = LogProgress(
        key=job.change_key(f"log.implement.{spent}"),
        iteration=job.iteration,
        step="implement",
        issue_id=job.issue.id,
        payload_json=payload,
    )
    return Writes(changes=(attempt,), logs=(log,))


def verdict(job: IssueJob, spent: int, reply: IssueJudgeResult) -> Writes:
    """Close the issue, or send it back to work, and file the bug the judge found."""
    payload = reply.model_dump_json()
    passed = reply.verdict == "pass"
    status = SetIssueStatus(
        key=job.change_key(f"verdict.{spent}"),
        issue_id=job.issue.id,
        status=IssueStatus.CLOSED if passed else IssueStatus.IN_PROGRESS,
        actor="judge",
        iteration=job.iteration,
        note=(
            f"closed by judge after attempt {spent}"
            if passed
            else (reply.feedback or reply.analysis)[:FEEDBACK_LIMIT]
        ),
        payload_json=payload,
    )
    log = LogProgress(
        key=job.change_key(f"log.review.{spent}"),
        iteration=job.iteration,
        step="review",
        issue_id=job.issue.id,
        payload_json=payload,
    )
    return Writes(
        changes=(status, *_creates(job, reply.new_issues, "judge", f"{spent}")), logs=(log,)
    )


def block(job: IssueJob) -> Writes:
    """Block an issue whose attempts are spent."""
    change = SetIssueStatus(
        key=job.change_key("block"),
        issue_id=job.issue.id,
        status=IssueStatus.BLOCKED,
        actor="loop",
        iteration=job.iteration,
        note=f"exhausted {job.config.max_attempts_per_issue} attempts",
    )
    return Writes(changes=(change,))


def filing(job: PerfJob, issues: Sequence[NewIssue], payload: str) -> Writes:
    """File the issues a performance evaluation found, and log the evaluation."""
    log = LogProgress(
        key=job.change_key("log.performance"),
        iteration=job.iteration,
        step="performance",
        issue_id=None,
        payload_json=payload,
    )
    return Writes(changes=_creates(job, issues, "perf_eval", "perf"), logs=(log,))


def _creates(
    job: IssueJob | PerfJob, filed: Sequence[NewBug | NewIssue], author: str, scope: str
) -> tuple[CreateIssue, ...]:
    return tuple(
        CreateIssue(
            key=job.change_key(f"filed.{scope}.{index}"),
            type=IssueType(item.type),
            title=item.title,
            body=TextBody(text=item.description),
            created_by=author,
            iteration=job.iteration,
        )
        for index, item in enumerate(filed)
    )
