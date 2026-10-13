"""Read model of an evolve run: the population in the shape frontends consume."""

from __future__ import annotations

from typing import TYPE_CHECKING

from vibesys.run.contracts import (
    IterationKind,
    IterationMetric,
    IterationStatus,
    IterationSummary,
    IterationTree,
    PluginProjection,
)

if TYPE_CHECKING:
    from vibesys.orchestration.evolve.search.api import EvolveSnapshot


def project_snapshot(snapshot: EvolveSnapshot, *, experiment_revision: int) -> PluginProjection:
    """Project the published record; `experiment_revision` is the core revision it was read at."""
    population = snapshot.population
    return PluginProjection(
        payload={
            "population": population.model_dump(mode="json"),
            "generation": population.generation,
            "stage": snapshot.stage.value,
        },
        experiment_revision=experiment_revision,
        iterations=_iterations(snapshot),
    )


def _iterations(snapshot: EvolveSnapshot) -> IterationTree:
    """Map generations and candidates without borrowing round semantics."""
    population = snapshot.population
    by_generation = {
        generation: tuple(
            sorted(
                (item for item in population.individuals if item.generation == generation),
                key=lambda item: item.id,
            )
        )
        for generation in range(population.generation + 1)
    }
    nodes: list[IterationSummary] = []
    for generation, candidates in by_generation.items():
        generation_id = f"generation:{generation}"
        nodes.append(
            IterationSummary(
                id=generation_id,
                kind=IterationKind.GENERATION,
                label=f"Generation {generation}",
                ordinal=generation,
                status=_generation_status(
                    snapshot,
                    generation,
                    current_generation=population.generation,
                    bootstrap_has_candidates=bool(candidates),
                ),
                child_count=len(candidates),
            )
        )
        for ordinal, candidate in enumerate(candidates):
            metrics = tuple(
                IterationMetric(
                    id=name,
                    value=value,
                    unit=candidate.perf_unit if index == 0 else None,
                )
                for index, (name, value) in enumerate(candidate.metrics[:4])
            )
            if not metrics and candidate.perf_metric is not None:
                metrics = (
                    IterationMetric(
                        id="performance",
                        value=candidate.perf_metric,
                        unit=candidate.perf_unit,
                    ),
                )
            nodes.append(
                IterationSummary(
                    id=f"candidate:{candidate.id}",
                    parent_id=generation_id,
                    kind=IterationKind.CANDIDATE,
                    label=candidate.summary or f"Candidate {candidate.id}",
                    ordinal=ordinal,
                    status=(
                        IterationStatus.COMPLETED if candidate.passed else IterationStatus.FAILED
                    ),
                    headline_metrics=metrics,
                )
            )
    return IterationTree.from_nodes(tuple(nodes))


def _generation_status(
    snapshot: EvolveSnapshot,
    generation: int,
    *,
    current_generation: int,
    bootstrap_has_candidates: bool,
) -> IterationStatus:
    if generation < current_generation:
        return IterationStatus.COMPLETED
    if generation == current_generation and snapshot.generation_open:
        return IterationStatus.ACTIVE
    if generation > 0:
        return IterationStatus.COMPLETED
    return IterationStatus.COMPLETED if bootstrap_has_candidates else IterationStatus.PENDING
