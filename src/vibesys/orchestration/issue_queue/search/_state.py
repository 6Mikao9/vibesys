"""`IssueQueueState`: the policy memory of an issue-queue run, as phases.

Lifecycle and accounting facts (attempt phases, slot use, charges, session phases, the
settlements) are never copied here; they are read from the run's view. What lives here is what
core cannot know: where the run is between jobs, the board as last read, the one open job, the
evaluations done and the revision the next job builds on.

The board is a cache of the tracker, which stays the source of truth. A write makes it stale:
the run reads it again before it decides anything from it, so the phase after a job is always a
read.

Every model is a strict, frozen, tuple-only `Value`, so the state round-trips through the
envelope codec unchanged. The state is version 2; a state saved by the flag-based strategy
(version 1) is refused with an error naming its version.
"""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field

from vibesys.candidates.api import Adopting, Winner
from vibesys.orchestration.issue_queue.search._job import Job
from vibesys.orchestration.issue_queue.search._values import Ending, PerformanceRecord, Tip
from vs_core.api.search import SchemaRef, StrategyState, Value
from vs_issue_tracker.api import IssueRow

STATE_SCHEMA = SchemaRef(name="plain.search-state", version=2)


class Cursor(StrEnum):
    """What the run decides once the board it asked for arrives."""

    START = "start"
    """The run begins: file the first issue unless it exists."""

    DRAIN = "drain"
    """Work the next open issue, or assess when the round has none."""

    AFTER_PERF = "after-perf"
    """An evaluation settled: end the run or start the next round."""


class Reading(Value):
    """The board is being read; ``sent`` counts how often this read was sent."""

    kind: Literal["reading"] = "reading"
    then: Cursor
    sent: int = Field(default=1, ge=1)


class Bootstrapping(Value):
    """The first issue is being filed; ``sent`` counts how often the write was sent."""

    kind: Literal["bootstrapping"] = "bootstrapping"
    sent: int = Field(default=1, ge=1)


class Working(Value):
    """A job is open; the run waits for it to finish."""

    kind: Literal["working"] = "working"
    job: Job


class Verifying(Value):
    """The accuracy of the final candidate is being measured, so core can adopt it."""

    kind: Literal["verifying"] = "verifying"
    ending: Ending


class Selecting(Value):
    """The final candidate is proposed and its adoption awaited."""

    kind: Literal["selecting"] = "selecting"
    ending: Ending
    adopting: Adopting


class Finished(Value):
    """The run's result was proposed; nothing more happens."""

    kind: Literal["finished"] = "finished"
    winner: Winner | None


type RunPhase = Annotated[
    Reading | Bootstrapping | Working | Verifying | Selecting | Finished,
    Field(discriminator="kind"),
]


class IssueQueueState(StrategyState):
    """Persisted policy state of the issue-queue search."""

    schema_version: Literal[2] = 2
    run: RunPhase = Reading(then=Cursor.START)
    board: tuple[IssueRow, ...] = ()
    reads: int = Field(default=0, ge=0)
    """Board reads sent; the next read's identity is numbered by it."""
    bootstrapped: bool = False
    stopping: bool = False
    round_idx: int = Field(default=0, ge=0)
    processed: int = Field(default=0, ge=0)
    """Issues worked in the current round."""
    jobs_started: int = Field(default=0, ge=0)
    performance: tuple[PerformanceRecord, ...] = ()
    tip: Tip = Tip()
