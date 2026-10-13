"""Pure readings of an agent's reply: what it asks of the board and what the next prompt carries."""

import json

from vibesys.orchestration.issue_queue.models import (
    IssueJudgeResult,
    IssuePerfEvalResult,
)
from vibesys.orchestration.issue_queue.search._config import IssueQueueConfig
from vibesys.orchestration.issue_queue.search._values import PerfRecord
from vs_core.api.search import Value


class Rejection(Value):
    """Why the judge sent an attempt back: what the next attempt is shown."""

    feedback: str
    analysis: str


def rejection_of(reply: IssueJudgeResult) -> Rejection:
    """The judge's feedback (its analysis when it gave none) and analysis."""
    return Rejection(feedback=reply.feedback or reply.analysis, analysis=reply.analysis)


def metrics_json(reply: IssuePerfEvalResult) -> str:
    """The evaluation's measurements as stable JSON, to show the next evaluation."""
    return json.dumps(reply.metrics.model_dump(mode="json"), sort_keys=True)


def perf_record(iteration: int, reply: IssuePerfEvalResult, created: tuple[int, ...]) -> PerfRecord:
    """The finished evaluation as the next one sees it."""
    return PerfRecord(
        iteration=iteration,
        primary_trend=reply.primary_trend,
        secondary_trend=reply.secondary_trend or "",
        metrics_json=metrics_json(reply),
        new_issues=created,
    )


def load_levels_json(config: IssueQueueConfig) -> str | None:
    """The configured workloads as sorted-key JSON, or None to let the evaluator discover them."""
    if config.load_levels is None:
        return None
    return json.dumps(
        [level.model_dump(mode="json") for level in config.load_levels], sort_keys=True
    )
