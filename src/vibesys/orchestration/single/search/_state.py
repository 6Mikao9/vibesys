"""`SingleState`: the scientific state of a single-agent search, as phases.

Lifecycle and accounting facts (attempt phases, slot occupancy, charges, session phases, the
evidence ledger, settlements) are never copied here; they are read from the run's view. What lives
here is policy memory core cannot know: the work in flight (the input measurement, the design of
the next hypothesis or the one open round), the hypothesis ledger, the outcome of each closed
round, the newest revision a later round may build on and the winner.

Every model is a strict, frozen, tuple-only `Value`, so the state round-trips through the
envelope codec unchanged. The state is version 2; a state saved by the flag-based strategy
(version 1) is refused with an error naming its version.
"""

from typing import Annotated, Literal

from pydantic import Field

from vibesys.candidates.api import Adopting, Baseline, Winner
from vibesys.orchestration.single.search._design import Design
from vibesys.orchestration.single.search._round import Round
from vibesys.orchestration.single.search._values import Unreachable
from vibesys.serial_search.api import GiveUp, LastProfile, RoundOutcome, StoredLedger
from vs_core.api.search import Children, RevisionRef, SchemaRef, StrategyState, Value

STATE_SCHEMA = SchemaRef(name="single.search-state", version=2)


class Searching(Value):
    """Rounds are being designed and run."""

    kind: Literal["searching"] = "searching"


class Selecting(Value):
    """Nothing is left to run: the winner is proposed and its adoption awaited."""

    kind: Literal["selecting"] = "selecting"
    adopting: Adopting


class Finished(Value):
    """The run's result was proposed; nothing more happens."""

    kind: Literal["finished"] = "finished"
    winner: Winner | None


type RunPhase = Annotated[Searching | Selecting | Finished, Field(discriminator="kind")]

type Part = Annotated[Baseline | Design | Round, Field(discriminator="kind")]
"""One child of the search: the input measurement, a design or a round."""


class SingleState(StrategyState):
    """Persisted scientific state of the single-agent search."""

    schema_version: Literal[2] = 2
    run: RunPhase = Searching()
    stopping: bool = False
    parts: Children[str, Part] = Children()
    ledger: StoredLedger = StoredLedger()
    """The hypothesis ledger as of the last closed round; a design or round in flight holds the
    newer one it is working on (see ``in_force``)."""
    giveup: GiveUp | None = None
    """Why designing ended for good, once it has."""
    closed: int = Field(default=0, ge=0)
    outcomes: tuple[RoundOutcome, ...] = ()
    checkpoint: RevisionRef | None = None
    """The newest revision a later round may build on (a retained checkpoint)."""
    last_profile: LastProfile | None = None
    unreachable: tuple[Unreachable, ...] = ()

    @property
    def in_force(self) -> StoredLedger:
        """The ledger as it stands now: the one the design or round in flight is working on."""
        for _, part in self.parts.in_order():
            if isinstance(part, Design | Round):
                return part.ledger
        return self.ledger
