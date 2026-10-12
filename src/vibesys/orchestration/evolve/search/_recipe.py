"""How an evolve run measures a candidate: the single source of every measurement plan."""

from __future__ import annotations

from typing import TYPE_CHECKING

from vs_core.api.search import MeasurementRecipe, StageBudget

if TYPE_CHECKING:
    from vibesys.orchestration.evolve.search._config import EvolveConfig
    from vs_core.api.search import RunFacts


def measurement_recipe(config: EvolveConfig, facts: RunFacts) -> MeasurementRecipe:
    """How this run measures a candidate.

    The search's trusted plans and the plan an agent measures with are both built from this
    value, so an agent measurement and a trusted one share one identity.
    """
    return MeasurementRecipe(
        evaluator_digest=facts.evaluator_digest,
        workload_digest=facts.workload_digest,
        environment_digest=facts.environment_digest,
        recipe=config.recipe,
        stages=(
            StageBudget(stage_id="accuracy", execution_seconds=config.accuracy_seconds),
            StageBudget(stage_id="benchmark", execution_seconds=config.benchmark_seconds),
        ),
        queue_allowance=config.queue_allowance_seconds,
        accuracy_stage="accuracy",
    )
