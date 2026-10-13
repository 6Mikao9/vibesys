"""Read model of an issue-queue run: the search's published record in the shape frontends consume."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from vibesys.run.contracts import (
    IterationKind,
    IterationMetric,
    IterationStatus,
    IterationSummary,
    IterationTree,
    PluginProjection,
)
from vs_issue_tracker.api import IssueStatus

if TYPE_CHECKING:
    from vibesys.orchestration.issue_queue.search.api import IssueBrief, QueueSnapshot


def project_run_snapshot(snapshot: QueueSnapshot, *, experiment_revision: int) -> PluginProjection:
    """Project one published record; `experiment_revision` is the core revision it was read at."""
    payload = {
        "version": 1,
        "round_idx": snapshot.round_idx,
        "cursor": snapshot.cursor,
        "current_issue_id": snapshot.current_issue_id,
        "issues": [
            {
                "id": issue.id,
                "type": issue.type,
                "status": issue.status,
                "title": issue.title,
                "attempts": issue.attempts,
            }
            for issue in snapshot.issues
        ],
        "performance": [
            {
                "iteration": record.iteration,
                "throughput_trend": record.primary_trend,
                "latency_trend": record.secondary_trend,
                "metrics": json.loads(record.metrics_json) if record.metrics_json else {},
                "new_issue_ids": list(record.new_issues),
            }
            for record in snapshot.performance
        ],
    }
    return PluginProjection(
        payload=payload,
        experiment_revision=experiment_revision,
        iterations=_iterations(snapshot),
    )


def _iterations(snapshot: QueueSnapshot) -> IterationTree:
    """Map the durable board snapshot without borrowing round semantics."""
    root_id = "board:issues"
    issues = tuple(sorted(snapshot.issues, key=lambda issue: issue.id))
    nodes = [
        IterationSummary(
            id=root_id,
            kind=IterationKind.BOARD,
            label="Issue board",
            ordinal=0,
            status=_board_status(snapshot),
            child_count=len(issues),
        )
    ]
    nodes.extend(
        IterationSummary(
            id=f"item:{issue.id}",
            parent_id=root_id,
            kind=IterationKind.ITEM,
            label=f"#{issue.id} {issue.title}",
            ordinal=issue.id,
            status=_issue_status(issue, active=issue.id == snapshot.current_issue_id),
            headline_metrics=(IterationMetric(id="attempts", value=float(issue.attempts)),),
        )
        for issue in issues
    )
    return IterationTree.from_nodes(tuple(nodes))


def _board_status(snapshot: QueueSnapshot) -> IterationStatus:
    if snapshot.cursor == "finished":
        if snapshot.stopped:
            return IterationStatus.CANCELLED
        if snapshot.ending is not None and snapshot.ending.outcome == "failure":
            return IterationStatus.FAILED
        return IterationStatus.COMPLETED
    return IterationStatus.ACTIVE


def _issue_status(issue: IssueBrief, *, active: bool) -> IterationStatus:
    if issue.status == IssueStatus.CLOSED.value:
        return IterationStatus.COMPLETED
    if issue.status == IssueStatus.BLOCKED.value:
        return IterationStatus.FAILED
    if active or issue.status == IssueStatus.IN_PROGRESS.value:
        return IterationStatus.ACTIVE
    return IterationStatus.PENDING
