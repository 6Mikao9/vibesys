"""Read model of an evolve run: the population in the shape frontends consume."""

from __future__ import annotations

from typing import TYPE_CHECKING

from vibesys.orchestration.evolve.search.api import stage_of
from vibesys.run.contracts import PluginProjection

if TYPE_CHECKING:
    from vibesys.orchestration.evolve.search.api import EvolveState


def project_strategy_state(state: EvolveState, *, experiment_revision: int) -> PluginProjection:
    """Project one search state; `experiment_revision` is the core revision it was read at."""
    population = state.population
    return PluginProjection(
        payload={
            "population": population.model_dump(mode="json"),
            "generation": population.generation,
            "stage": stage_of(population).value,
        },
        experiment_revision=experiment_revision,
    )
