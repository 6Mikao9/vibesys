"""`DynamicState`: the scientific state of a dynamic search, as phases.

Lifecycle and accounting facts (attempt phases, slot occupancy, charges, session phases, the
evidence ledger, settlements) are never copied here; they are read from the run's view. What
lives here is policy memory core cannot know: the concurrent work in flight (the input
measurement, one planning call, one child per workstream), hypothesis history, the parents
offered for later work and the winner.

Every model is a strict, frozen, tuple-only `Value`, so the state round-trips through the
envelope codec unchanged. The state is version 2; a state saved by the flag-based search
(version 1) is refused with an error naming its version.
"""

from typing import Annotated, Literal

from pydantic import Field

from vibesys.candidates.api import Adopting, Baseline, Winner
from vibesys.orchestration.dynamic.search._parents import ParentSnapshot
from vibesys.orchestration.dynamic.search._portfolio import Planning
from vibesys.orchestration.dynamic.search._values import (
    FinishedWork,
    HypothesisRecord,
    PlanProblem,
    Unreachable,
)
from vibesys.orchestration.dynamic.search._workstream import Workstream
from vs_core.api import Children, SchemaRef, StrategyState, Value

STATE_SCHEMA = SchemaRef(name="dynamic.search-state", version=2)


class Searching(Value):
    """Work is being scheduled and run."""

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

type Part = Annotated[Baseline | Planning | Workstream, Field(discriminator="kind")]
"""One child of the search: the input measurement, a planning call or a workstream."""


class PlannerGate(Value):
    """Whether planning may start again, and why the last call ended empty."""

    failed: bool = False
    """The last call ended with nothing valid and nothing in flight to wait for."""
    blocked_at_done: int | None = Field(default=None, ge=0)
    """Finished workstreams when a call scheduled nothing; planning resumes after another one."""
    last_error: PlanProblem | None = None
    """Shown in the first prompt of the next call."""


class DynamicState(StrategyState):
    """Persisted scientific state of the dynamic search."""

    schema_version: Literal[2] = 2
    run: RunPhase = Searching()
    stopping: bool = False
    parts: Children[str, Part] = Children()
    gate: PlannerGate = PlannerGate()
    calls: int = Field(default=0, ge=0)
    """Planning calls opened; the next call's key and ask IDs are numbered by it."""
    hypotheses: tuple[HypothesisRecord, ...] = ()
    finished: tuple[FinishedWork, ...] = ()
    parents: tuple[ParentSnapshot, ...] = ()
    refunded: int = Field(default=0, ge=0)
    """Workstreams refunded because they never opened."""
    withheld: tuple[str, ...] = ()
    """Parent revisions whose verification failed; never offered to the planner again."""
    unreachable: tuple[Unreachable, ...] = ()
