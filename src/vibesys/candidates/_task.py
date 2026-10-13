"""What the task fixes for candidate policy: its gates and the space candidates are ranked in.

The task's terms (`vs_core.api.TaskTerms`) are resolved once by the host from the input bundle
and the domain. These two functions read them into the values the pure policy takes, so no search
holds a copy of a gate flag or a metric space.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from vibesys.candidates._settlement import Gates
from vibesys.metrics import MetricSpace, Objective
from vs_core.api.search import (
    ArtifactRef,
    EvidenceKind,
    TaskObjective,
    TaskStage,
    TaskTerms,
)

ACCURACY = "accuracy"
BENCHMARK = "benchmark"
"""The names of the two gate stages in a task's recipe."""


def gates_of(terms: TaskTerms) -> Gates:
    """Which gates a candidate must pass: the stages the task measures."""
    return Gates(accuracy=ACCURACY in terms.measured, benchmark=BENCHMARK in terms.measured)


def space_of(terms: TaskTerms) -> MetricSpace:
    """The axes and the tolerance candidates are compared within."""
    return MetricSpace(
        objectives=tuple(Objective(item.name, item.direction) for item in terms.objectives),
        relative_noise=terms.relative_noise,
    )


PROFILE = "profile"
STAGE_SECONDS = {ACCURACY: 1800.0, BENCHMARK: 3600.0, PROFILE: 1800.0}
"""Seconds one stage of a measurement may execute when the evaluation plan declares no limit."""
OPERATION_SECONDS = 300.0
"""Seconds one read of evidence (decoding it, verifying a parent) may take."""


@dataclass(frozen=True)
class Measuring:
    """What the evaluation plan and the backend say about how the task's stages run."""

    stage_seconds: Mapping[str, float | None]
    """The limit the plan declares for a stage, or None where it declares none."""
    queue_allowance: float
    """Seconds a measurement may wait in the evaluation queue."""
    profile_fields: tuple[str, ...] = ()
    benchmark_needs_accuracy: bool = True
    """A benchmark counts only where the accuracy gate passed (the task's ``requires_accuracy``)."""
    """The measurement fields the run's profile capture supplies."""


def terms_of(
    recipe: ArtifactRef, gates: Gates, space: MetricSpace, measuring: Measuring
) -> TaskTerms:
    """The task's terms from what the host resolved about its input bundle and its domain."""
    measured = tuple(
        name for name, gated in ((ACCURACY, gates.accuracy), (BENCHMARK, gates.benchmark)) if gated
    )
    return TaskTerms(
        recipe=recipe,
        stages=tuple(
            TaskStage(stage_id=name, execution_seconds=measuring.stage_seconds.get(name) or default)
            for name, default in STAGE_SECONDS.items()
        ),
        measured=measured,
        queue_allowance=measuring.queue_allowance,
        accuracy_stage=ACCURACY if gates.accuracy else None,
        accuracy_gated=(
            (EvidenceKind.BENCHMARK,)
            if gates.accuracy and measuring.benchmark_needs_accuracy
            else ()
        ),
        operation_seconds=OPERATION_SECONDS,
        objectives=tuple(
            TaskObjective(name=item.name, direction=item.direction) for item in space.objectives
        ),
        relative_noise=space.relative_noise,
        profile_fields=measuring.profile_fields,
    )
