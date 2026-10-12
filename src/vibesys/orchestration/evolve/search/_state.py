"""`EvolveState`: the scientific state of an evolve search, as phases.

Lifecycle and accounting facts (attempt phases, slot occupancy, charges, session phases, the
evidence ledger, settlements) are never copied here; they are read from the run's view. What
lives here is policy memory core cannot know: the immutable population (individuals and the
seeded generator state), the population as it stood when the open generation began, the slots of
the open frame and which settlement each retained individual rests on.

Every model is a strict, frozen, tuple-only `Value`, so the state round-trips through the
envelope codec unchanged. The state is version 2; a state saved by the flag-based strategy
(version 1) is refused with an error naming its version.
"""

from typing import Annotated, Literal

from pydantic import Field

from vibesys.candidates.api import Adopting, Winner
from vibesys.orchestration.evolve.population import PopulationState
from vibesys.orchestration.evolve.search._slot import Slot
from vibesys.orchestration.evolve.search._values import Advisory, Retained, Unreachable
from vs_core.api.search import Children, SchemaRef, StrategyState, Value

STATE_SCHEMA = SchemaRef(name="evolve.search-state", version=2)

# Advisory failures the state keeps for an operator to read.
ADVISORY_LIMIT = 20


class ColdStart(Value):
    """No individual has passed yet: single-slot attempts, each building on the last one's work."""

    kind: Literal["cold_start"] = "cold_start"
    spent: int = Field(ge=0)
    """Cold-start attempts that have ended, with or without an individual."""


class Generation(Value):
    """Evolving: the slots of a frame, proposed from the population as the generation began."""

    kind: Literal["generation"] = "generation"
    frozen: PopulationState
    """The population when the generation began; a proposal is a pure function of it."""


class Selecting(Value):
    """Nothing is left to run: the winner is proposed and its adoption awaited."""

    kind: Literal["selecting"] = "selecting"
    adopting: Adopting


class Finished(Value):
    """The run's result was proposed; nothing more happens."""

    kind: Literal["finished"] = "finished"
    winner: Winner | None


type RunPhase = Annotated[
    ColdStart | Generation | Selecting | Finished, Field(discriminator="kind")
]


class EvolveState(StrategyState):
    """Persisted scientific state of the evolve search."""

    schema_version: Literal[2] = 2
    run: RunPhase = ColdStart(spent=0)
    stopping: bool = False
    population: PopulationState
    slots: Children[str, Slot] = Children()
    """The open frame: one child per slot, kept until it is admitted to the population."""
    retained: tuple[Retained, ...] = ()
    advisories: tuple[Advisory, ...] = ()
    unreachable: tuple[Unreachable, ...] = ()
