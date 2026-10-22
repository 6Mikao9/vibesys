"""An agent's measurement and a trusted one are planned from one recipe, so they share an identity."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
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
from vs_runtime.api.core import AgentEvaluationPolicy
from vs_sim.api.testing import examples

CANDIDATE = RevisionRef(revision_id=RevisionId(root="c"), digest="digest-c")
_NAMES = ("accuracy", "benchmark")
_GATES = ("accuracy", "benchmark")


@given(
    seconds=st.tuples(st.integers(0, 10_000).map(float), st.integers(0, 10_000).map(float)),
    queue=st.integers(0, 1_000).map(float),
    now=st.integers(0, 1_000_000).map(float),
    gated=st.booleans(),
)
def test_the_agent_plan_is_the_recipes_plan(
    seconds: tuple[float, float], queue: float, now: float, *, gated: bool
) -> None:
    recipe = MeasurementRecipe(
        evaluator_digest="e",
        workload_digest="w",
        environment_digest="n",
        recipe=ArtifactRef(artifact_id=ArtifactId(root="recipe"), digest="r"),
        stages=tuple(
            StageBudget(stage_id=name, execution_seconds=s)
            for name, s in zip(_NAMES, seconds, strict=True)
        ),
        queue_allowance=queue,
        accuracy_stage="accuracy" if gated else None,
        accuracy_gated=(EvidenceKind.CORRECTNESS, EvidenceKind.BENCHMARK),
    )
    policy = AgentEvaluationPolicy.from_recipe(recipe)
    assert policy.plan(CANDIDATE, measured_input="sha256:in", now_at=now) == recipe.plan(
        CANDIDATE, purpose="local-validation", now_at=now
    ).model_copy(update={"measured_input": "sha256:in"})
    assert policy.measurement == recipe
    assert policy.stages_to_run(()) == _NAMES


def _policy(*names: str) -> AgentEvaluationPolicy:
    recipe = MeasurementRecipe(
        evaluator_digest="e",
        workload_digest="w",
        environment_digest="n",
        recipe=ArtifactRef(artifact_id=ArtifactId(root="recipe"), digest="r"),
        stages=tuple(StageBudget(stage_id=name, execution_seconds=1.0) for name in names),
        queue_allowance=1.0,
        accuracy_stage="accuracy" if "accuracy" in names else None,
        accuracy_gated=(EvidenceKind.BENCHMARK,) if "accuracy" in names else (),
    )
    return AgentEvaluationPolicy.from_recipe(recipe)


@settings(max_examples=examples(pr=25, nightly=100))
@given(
    order=st.permutations(list(_GATES)),
    recorded=st.frozensets(st.sampled_from(_GATES)),
    with_build=st.booleans(),
)
def test_requested_steps_keep_order_and_skip_recorded_gates(
    order: list[str], recorded: frozenset[str], *, with_build: bool
) -> None:
    names = ("build", *_GATES) if with_build else _GATES
    policy = _policy(*names)
    got = policy.stages_to_run(tuple(order), recorded=recorded)
    pending = tuple(step for step in order if step not in recorded)
    expected = () if not pending else (("build", *pending) if with_build else pending)
    assert got == expected
    assert set(got).isdisjoint(recorded)
    assert policy.stages_to_run((), recorded=recorded) == names


def test_an_unknown_step_names_the_field() -> None:
    with pytest.raises(ValueError, match="steps"):
        _policy("accuracy", "benchmark").stages_to_run(("profile",))


def test_a_benchmark_first_plan_runs_benchmark_before_accuracy() -> None:
    policy = _policy("build", "accuracy", "benchmark")
    plan = policy.plan(
        CANDIDATE,
        measured_input="sha256:in",
        now_at=1.0,
        stages=policy.stages_to_run(("benchmark", "accuracy")),
    )
    assert tuple(stage.stage_id for stage in plan.stages) == ("build", "benchmark", "accuracy")
    assert plan.stages[1].depends_on == ("build",)
    assert plan.stages[2].depends_on == ("benchmark",)
    assert plan.accuracy_stage == "accuracy"
