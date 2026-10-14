"""A recipe yields the same ordered plan for every caller that builds one from it."""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from vs_core.api import (
    ArtifactId,
    ArtifactRef,
    EvidenceKind,
    MeasurementRecipe,
    RevisionId,
    RevisionRef,
    StageBudget,
)

CANDIDATE = RevisionRef(revision_id=RevisionId(root="c"), digest="digest-c")
_NAMES = ("accuracy", "benchmark", "profile")


def _recipe(seconds: tuple[float, float, float], queue: float) -> MeasurementRecipe:
    return MeasurementRecipe(
        evaluator_digest="e",
        workload_digest="w",
        environment_digest="n",
        recipe=ArtifactRef(artifact_id=ArtifactId(root="recipe"), digest="r"),
        stages=tuple(
            StageBudget(stage_id=n, execution_seconds=s)
            for n, s in zip(_NAMES, seconds, strict=True)
        ),
        queue_allowance=queue,
        accuracy_stage="accuracy",
        accuracy_gated=(EvidenceKind.CORRECTNESS, EvidenceKind.BENCHMARK),
    )


_SECONDS = st.tuples(*[st.integers(0, 10_000).map(float)] * 3)
_PICK = st.lists(st.sampled_from(_NAMES), unique=True, min_size=1).map(
    lambda names: tuple(sorted(names, key=_NAMES.index))
)


@given(
    seconds=_SECONDS,
    queue=st.integers(0, 1_000).map(float),
    picked=_PICK,
    now=st.integers(0, 1_000_000).map(float),
)
def test_a_plan_orders_its_stages_and_is_bounded_by_every_budget_and_the_queue(
    seconds: tuple[float, float, float], queue: float, picked: tuple[str, ...], now: float
) -> None:
    plan = _recipe(seconds, queue).plan(CANDIDATE, purpose="official", now_at=now, stages=picked)
    assert tuple(stage.stage_id for stage in plan.stages) == picked
    assert [stage.depends_on for stage in plan.stages] == [()] + [(name,) for name in picked[:-1]]
    assert plan.time_bound == sum(stage.execution_budget for stage in plan.stages) + queue
    assert plan.time_bound == _recipe(seconds, queue).budget(picked)
    assert plan.accuracy_stage == ("accuracy" if "accuracy" in picked else None)


@given(seconds=_SECONDS, picked=_PICK)
def test_restricting_a_recipe_plans_the_same_as_naming_those_stages(
    seconds: tuple[float, float, float], picked: tuple[str, ...]
) -> None:
    recipe = _recipe(seconds, 5.0)
    restricted = recipe.only(picked).plan(CANDIDATE, purpose="baseline", now_at=1.0)
    named = recipe.plan(CANDIDATE, purpose="baseline", now_at=1.0, stages=picked)
    assert restricted == named
