"""Prompt data derived from the population, a slot's plan and the config.

Everything here is a pure function of its arguments. Prompt contexts are typed values; the search
never writes prompt text.
"""

from vibesys.orchestration.evolve.population import Individual, PopulationState
from vibesys.orchestration.evolve.search import _population as population
from vibesys.orchestration.evolve.search._config import EvolveConfig
from vibesys.orchestration.evolve.search._prompts import (
    JudgePrompt,
    MutatePrompt,
    ObjectiveAxis,
    ProfilerPrompt,
)
from vibesys.orchestration.evolve.search._values import SlotPlan
from vibesys.orchestration.profilers import ProfilerKind
from vs_core.api.search import EvidenceRef, RevisionRef


def _peers(frozen: PopulationState, identifiers: tuple[int, ...]) -> tuple[Individual, ...]:
    """The named individuals as they stood when the generation began."""
    found = (population.individual(frozen, item) for item in identifiers)
    return tuple(item for item in found if item is not None)


def mutate_prompt(
    plan: SlotPlan, current: PopulationState, frozen: PopulationState, config: EvolveConfig
) -> MutatePrompt:
    """The brief of the slot's mutation: a cold start, or a child of a parent.

    A cold start reads the lessons of ``current``. A child's parent and inspirations are read
    from ``frozen``, the population as its generation began, so the brief does not depend on
    which lower slots have been admitted since.
    """
    proposal = plan.proposal
    if proposal is None:
        lessons = tuple(population.search_of(config).failure_lessons(current))
        return MutatePrompt(
            base_revision=plan.base,
            is_cold_start=True,
            repair_seed=plan.repair_seed,
            failed_lessons=lessons,
            num_failed_attempts=sum(not item.passed for item in current.individuals),
            reference_path=config.reference_path,
            interface=config.interface,
        )
    (parent,) = _peers(frozen, (proposal.parent_id,))
    return MutatePrompt(
        base_revision=plan.base,
        is_cold_start=False,
        parent=parent,
        inspirations=_peers(frozen, proposal.inspiration_ids),
        reference_path=config.reference_path,
        interface=config.interface,
    )


def judge_prompt(
    candidate: RevisionRef, config: EvolveConfig, cited_evidence: tuple[EvidenceRef, ...]
) -> JudgePrompt:
    """The review brief for the slot's candidate."""
    return JudgePrompt(
        candidate=candidate, interface=config.interface, cited_evidence=cited_evidence
    )


def profile_prompt(candidate: RevisionRef, config: EvolveConfig) -> ProfilerPrompt:
    """The profiling brief for the slot's passing candidate."""
    return ProfilerPrompt(
        candidate=candidate,
        profiler_kind=ProfilerKind(config.profiler_id).value,
        profile_execution=config.profile_execution,
        benchmark_command=config.benchmark_command,
        objectives=tuple(
            ObjectiveAxis(name=item.name, direction=item.direction)
            for item in config.metric_space.objectives
        ),
        profiler_support_name=config.profiler_support_name,
        profiler_mcp_name=config.profiler_mcp_name,
    )
