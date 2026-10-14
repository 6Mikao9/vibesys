"""The evolve search's own knobs: how big a population, how many generations, how it selects.

Everything else a run needs belongs to someone else. How candidates are measured and compared is
the task's (`ctx.task`); how an agent is asked is its role's (`_roles`); how a backend retries its
infrastructure is the backend's. A config is an input of the search, never persisted state:
running the search again with the same config and the same events reproduces every decision.
"""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from vibesys.orchestration.evolve.population import OpenEvolveSelectorConfig
from vs_core.api.search import Value

type Positive = Annotated[int, Field(gt=0)]


class EvolveConfig(Value):
    """Budgets, selection knobs and width of one evolve run."""

    max_generations: Positive
    """Generations of children after the cold start."""
    children_per_generation: Positive
    """Children (parent plus inspirations, mutated) every generation proposes."""
    k_top_inspirations: Annotated[int, Field(ge=0)]
    """Best peers shown to the mutator beside the parent."""
    k_random_inspirations: Annotated[int, Field(ge=0)]
    """Random peers shown to the mutator beside the parent."""
    selection_temperature: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    """How evenly the scalar selector spreads parents over the ranking."""
    seed: int | None = None
    """Seeds the selection, so a given seed proposes the same parents every time."""
    search_policy: Literal["vibesys", "openevolve"] | None = None
    """The selector; unset means OpenEvolve when any of its knobs is set, else the built-in one."""
    openevolve_population_size: Positive | None = None
    openevolve_archive_size: Positive | None = None
    openevolve_num_islands: Positive | None = None
    openevolve_migration_interval: Positive | None = None
    openevolve_migration_rate: Annotated[float, Field(ge=0, le=1)] | None = None
    frontier_bias: Annotated[float, Field(ge=0, le=1)]
    """How much the multi-objective selector favours the Pareto frontier."""
    bootstrap_max_attempts: Positive
    """Cold-start attempts; the first passing one seeds the population."""
    max_parallelism: Positive = 1
    """Children of a generation that run at once."""
    profiling: bool = False
    """Whether a passing child gets a profiler turn. Follows the run's chosen profiler."""
    exclusive_root: bool = False
    """Whether children take turns in the run's own root, for a host that cannot open
    isolated candidate sandboxes. Follows the run environment; it needs `max_parallelism` 1."""

    @model_validator(mode="after")
    def _validate_root(self) -> Self:
        if self.exclusive_root and self.max_parallelism != 1:
            message = "children that share the run's root cannot run in parallel"
            raise ValueError(message)
        return self

    @model_validator(mode="after")
    def _validate_selector(self) -> Self:
        if self.search_policy == "vibesys" and self.openevolve is not None:
            message = "OpenEvolve settings require search_policy='openevolve'"
            raise ValueError(message)
        return self

    @property
    def openevolve(self) -> OpenEvolveSelectorConfig | None:
        """The configured OpenEvolve policy, if any knob selected it."""
        values = (
            self.openevolve_population_size,
            self.openevolve_archive_size,
            self.openevolve_num_islands,
            self.openevolve_migration_interval,
            self.openevolve_migration_rate,
        )
        if all(value is None for value in values):
            return None
        defaults = OpenEvolveSelectorConfig()
        return OpenEvolveSelectorConfig(
            population_size=self.openevolve_population_size or defaults.population_size,
            archive_size=self.openevolve_archive_size or defaults.archive_size,
            num_islands=self.openevolve_num_islands or defaults.num_islands,
            migration_interval=self.openevolve_migration_interval or defaults.migration_interval,
            migration_rate=(
                self.openevolve_migration_rate
                if self.openevolve_migration_rate is not None
                else defaults.migration_rate
            ),
        )

    @property
    def selector(self) -> Literal["vibesys", "openevolve"]:
        """The population selector this run uses."""
        return self.search_policy or ("openevolve" if self.openevolve is not None else "vibesys")

    @property
    def needs_code(self) -> bool:
        """Whether admission needs each passing child's patch (the OpenEvolve selector)."""
        return self.selector == "openevolve"

    @property
    def total_attempts(self) -> int:
        """The most attempts a run can start: every cold start and every child."""
        return self.bootstrap_max_attempts + self.max_generations * self.children_per_generation
