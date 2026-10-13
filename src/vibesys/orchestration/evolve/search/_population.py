"""The population policy as the search uses it: proposals, outcomes and fitness.

`PopulationSearch` is the pure selection and admission policy and is unchanged. This module
binds it to the search's values: a generation's proposals are a pure function of the population
as it stood when the generation began, and a finished child becomes a `CandidateOutcome`.
"""

from vibesys.metrics import AcceptedReading, MetricSpace, reading_of
from vibesys.orchestration.evolve.population import (
    CandidateOutcome,
    Individual,
    Metrics,
    PopulationConfig,
    PopulationSearch,
    PopulationState,
)
from vibesys.orchestration.evolve.search._config import EvolveConfig
from vibesys.orchestration.evolve.search._values import (
    ChildDone,
    FailureKind,
    ProfileReading,
    SlotProposal,
)
from vs_core.api.search import EvidenceKind


def search_of(config: EvolveConfig, space: MetricSpace) -> PopulationSearch:
    """The run's deterministic selection policy."""
    return PopulationSearch(
        PopulationConfig(
            space=space,
            selection_temperature=config.selection_temperature,
            frontier_bias=config.frontier_bias,
            k_top_inspirations=config.k_top_inspirations,
            k_random_inspirations=config.k_random_inspirations,
            selector=config.selector,
            openevolve=config.openevolve,
            seed=config.seed,
        )
    )


def individual(population: PopulationState, identifier: int) -> Individual | None:
    """The individual with this id."""
    return next((item for item in population.individuals if item.id == identifier), None)


def proposals(
    search: PopulationSearch, start: PopulationState, children: int
) -> tuple[SlotProposal | None, ...]:
    """The selector's choices for every slot of a generation that began at ``start``.

    The generator state is threaded through the slots in order and the result is discarded, so a
    given seed proposes the same parents and inspirations in every generation whatever order the
    slots complete in.
    """
    state = start
    chosen: list[SlotProposal | None] = []
    for _ in range(children):
        proposal, state = search.propose(state)
        chosen.append(
            None
            if proposal is None
            else SlotProposal(
                parent_id=proposal.parent.id,
                inspiration_ids=tuple(item.id for item in proposal.inspirations),
                policy_parent_id=proposal.policy_parent_id,
                target_island=proposal.target_island,
            )
        )
    return tuple(chosen)


def fitness(
    profile: ProfileReading | None, benchmark: AcceptedReading | None
) -> tuple[float | None, str | None, Metrics]:
    """A candidate's recorded fitness: the trusted benchmark over the profiler's self-report.

    A trusted headline always labels itself: its declared unit, else its metric name (the
    profiler's own unit describes the profiler's number, which the headline replaces).

    The trusted row is merged over the profiler's row rather than replacing it: the scalar
    contract reports one number, so on a two-axis task replacing the row would leave every
    individual incomplete on the second axis, and the frontier (which keeps only individuals
    carrying a value for every configured axis) would return nothing. Without a benchmark the
    profiler's self-report stands; it is never winner-eligible evidence by itself, since core
    grants authority only to trusted evidence.
    """
    metrics = dict(profile.metrics) if profile is not None else {}
    headline = None if benchmark is None else benchmark.headline()
    if benchmark is not None and headline is not None:
        trusted = {row.name: row.value for row in benchmark.metrics}
        unit = headline.unit or headline.name
        return headline.value, unit, tuple((metrics | trusted).items())
    return (
        None if profile is None else profile.perf_metric,
        None if profile is None else profile.perf_unit,
        tuple(metrics.items()),
    )


def keeps_commit(done: ChildDone) -> bool:
    """Whether the individual records its revision: a pass, or a cold start's work in progress."""
    return done.passed or (done.start.cold_start and done.settlement is not None)


def earns_individual(done: ChildDone) -> bool:
    """Whether the child is an individual at all.

    A child that never got a workspace, one whose candidate was never measured, one an operator
    stop cut short and one core settled nothing for earn none; every other child is one, a failed
    one keeping its feedback for the lessons later cold starts read.
    """
    cause = done.closing.cause
    if done.refused is not None or done.settlement is None:
        return False
    return cause is None or cause.kind not in (FailureKind.STOPPED, FailureKind.UNMEASURED)


def outcome_of(done: ChildDone) -> CandidateOutcome | None:
    """The child as the population sees it, or None when it earns no individual."""
    if not earns_individual(done):
        return None
    settlement = done.settlement
    closing = done.closing
    proposal = done.start.proposal
    benchmark = reading_of(closing.readings, EvidenceKind.BENCHMARK)
    retained = None if settlement is None else settlement.candidate
    commit = retained.revision_id.root if retained is not None and keeps_commit(done) else None
    metric, unit, metrics = fitness(closing.profile, benchmark) if done.passed else (None, None, ())
    return CandidateOutcome(
        passed=done.passed,
        parent_id=None if proposal is None else proposal.parent_id,
        inspiration_ids=() if proposal is None else proposal.inspiration_ids,
        summary=closing.summary,
        feedback=done.feedback,
        commit=commit,
        perf_metric=metric,
        perf_unit=unit,
        metrics=metrics,
        policy_parent_id=None if proposal is None else proposal.policy_parent_id,
        target_island=None if proposal is None else proposal.target_island,
        code=closing.patch if done.passed else None,
    )
