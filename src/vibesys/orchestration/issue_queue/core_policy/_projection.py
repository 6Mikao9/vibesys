"""Read model of an issue-queue run: the search state in the shape frontends already consume.

The state keeps phases, not the flags the old read model reported, so the cursor and the job
step are derived from the phase the run is in.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from vibesys.orchestration.issue_queue.search.api import (
    Blocking,
    Bootstrapping,
    Claiming,
    Cursor,
    Done,
    Evaluating,
    Filing,
    Implementing,
    JobKind,
    Reading,
    ReadingEvidence,
    Recording,
    Reporting,
    Reviewing,
    Settling,
    Starting,
    Working,
)
from vibesys.run.contracts import PluginProjection

if TYPE_CHECKING:
    from vibesys.orchestration.issue_queue.search.api import IssueQueueState


def _cursor(state: IssueQueueState) -> str:
    """Where the run is between jobs, as the read model has always named it."""
    match state.run:
        case Reading(then=Cursor.AFTER_PERF):
            return "after_perf"
        case Reading(then=Cursor.START) | Bootstrapping():
            return "start"
        case Working(job=job) if job.kind is JobKind.PERF:
            return "assess"
        case _:
            return "drain"


_STEPS: dict[type, str] = {
    Starting: "start",
    Claiming: "claim",
    Implementing: "work",
    Evaluating: "work",
    Recording: "record",
    ReadingEvidence: "review",
    Reviewing: "review",
    Filing: "file",
    Reporting: "file",
    Blocking: "block",
    Settling: "settle",
    Done: "done",
}
"""The step of the open job, as the read model has always named it."""


def project_strategy_state(state: IssueQueueState, *, experiment_revision: int) -> PluginProjection:
    """Project one search state; `experiment_revision` is the core revision it was read at."""
    job = state.run.job if isinstance(state.run, Working) else None
    payload = {
        "version": 1,
        "round_idx": state.round_idx,
        "cursor": _cursor(state),
        "phase": None if job is None else _STEPS[type(job.phase)],
        "current_issue_id": None if job is None or job.issue is None else job.issue.id,
        "bootstrap_done": state.bootstrapped,
        "issues": [
            {
                "id": issue.id,
                "type": issue.type.value,
                "status": issue.status.value,
                "title": issue.title,
                "attempts": issue.attempts,
            }
            for issue in state.board
        ],
        "performance": [
            {
                "iteration": record.iteration,
                "throughput_trend": record.primary_trend,
                "latency_trend": record.secondary_trend,
                "metrics": json.loads(record.metrics_json) if record.metrics_json else {},
                "new_issue_ids": list(record.new_issues),
            }
            for record in state.performance
        ],
    }
    return PluginProjection(payload=payload, experiment_revision=experiment_revision)
