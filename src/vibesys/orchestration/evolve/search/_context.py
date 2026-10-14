"""The prompt data of a mutation: a pure function of the population and the config.

The search never writes prompt text. It builds the typed `MutatePrompt` a cold start or a child
sends, from the population as it stood when the attempt (or its generation) began.
"""

from vibesys.orchestration.evolve.population import Individual, PopulationSearch, PopulationState
from vibesys.orchestration.evolve.search._population import individual
from vibesys.orchestration.evolve.search._prompts import MutatePrompt
from vibesys.orchestration.evolve.search._values import SlotProposal
from vs_core.api.search import RevisionRef


def cold_start_prompt(
    search: PopulationSearch, population: PopulationState, base: RevisionRef, *, repair: bool
) -> MutatePrompt:
    """The brief of a cold start: write the program, or repair the last attempt's work in place."""
    return MutatePrompt(
        base_revision=base,
        is_cold_start=True,
        repair_seed=repair,
        failed_lessons=tuple(search.failure_lessons(population)),
        num_failed_attempts=sum(not item.passed for item in population.individuals),
    )


def child_prompt(
    started: PopulationState, base: RevisionRef, proposal: SlotProposal
) -> MutatePrompt | None:
    """The brief of a child of ``proposal``'s parent, or None when the parent is not there."""
    parent = individual(started, proposal.parent_id)
    if parent is None:
        return None
    found = (individual(started, item) for item in proposal.inspiration_ids)
    peers: tuple[Individual, ...] = tuple(item for item in found if item is not None)
    return MutatePrompt(base_revision=base, is_cold_start=False, parent=parent, inspirations=peers)
