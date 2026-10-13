"""Scripted agent replies in the shapes the issue-queue roles are asked to produce."""

from __future__ import annotations

from vibesys.orchestration.issue_queue.models import (
    GenericPerfMetrics,
    IssueImplementerResult,
    IssueJudgeResult,
    IssuePerfEvalResult,
    LoadLevelMetrics,
    NewBug,
    NewIssue,
    PerfMetrics,
    PerformanceMeasurement,
    ThroughputStats,
)


def implemented(summary: str = "done", **extra: object) -> IssueImplementerResult:
    """An implementer result reply."""
    return IssueImplementerResult.model_validate(
        {"summary": summary, "files_touched": ("a.py",), "self_check": "ok", **extra}
    )


def judged(*, passed: bool = True, bugs: tuple[str, ...] = ()) -> IssueJudgeResult:
    """A judge result reply, optionally filing bugs."""
    return IssueJudgeResult(
        analysis="analysis",
        feedback="" if passed else "fix it",
        verdict="pass" if passed else "fail",
        new_issues=tuple(
            NewBug(type="bug", title=title, description=f"## Background\n{title}") for title in bugs
        ),
    )


def evaluated(*titles: str, kinds: tuple[str, ...] = ()) -> IssuePerfEvalResult:
    """A serving performance evaluator reply filing one issue per title."""
    level = LoadLevelMetrics(
        target_rate=1.0,
        actual_rate=1.0,
        num_requests=4,
        num_completed=4,
        num_failed=0,
        duration=1.0,
        throughput=ThroughputStats(request_throughput=4.0, token_throughput=40.0),
    )
    return IssuePerfEvalResult(
        analysis="analysis",
        metrics=PerfMetrics(load_levels=(level,)),
        evaluator_feedback=("measure more",),
        new_issues=tuple(
            NewIssue(
                type=kinds[index] if index < len(kinds) else "perf",  # ty: ignore[invalid-argument-type]
                title=title,
                description=f"## Background\n{title}",
            )
            for index, title in enumerate(titles)
        ),
        throughput_trend="improved",
        latency_trend="mixed",
    )


def generic_evaluated() -> IssuePerfEvalResult:
    """A domain-neutral performance evaluator reply."""
    return IssuePerfEvalResult(
        analysis="The candidate processes more work per second.",
        metrics=GenericPerfMetrics(
            measurements=(
                PerformanceMeasurement(
                    name="operations_per_second", value=4.0, unit="ops/s", workload="default"
                ),
            )
        ),
        evaluator_feedback=("measure more",),
        primary_metric_trend="improved",
        secondary_metric_trend=None,
    )
