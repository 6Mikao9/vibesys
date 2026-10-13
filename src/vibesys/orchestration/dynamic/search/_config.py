"""The dynamic search's own knobs: how wide, how long, how often to review, when to give up.

Everything else a run needs belongs to someone else. How candidates are measured and compared is
the task's (`ctx.task`); how an agent is asked is its role's (`_roles`); how a backend retries
its infrastructure is the backend's. A config is an input of the search, never persisted state:
running the search again with the same config and the same events reproduces every decision.
"""

from typing import Annotated

from pydantic import Field

from vs_core.api.search import Value

type Positive = Annotated[int, Field(gt=0, le=1000)]
type NonNegative = Annotated[int, Field(ge=0, le=1000)]
type Width = Annotated[int, Field(gt=0, le=32)]


class DynamicConfig(Value):
    """Budget, width, review cadence and give-up rules of one dynamic run."""

    max_rounds: Positive
    """Rounds of agent work per slot: the run schedules at most ``max_rounds * max_in_flight``."""
    max_in_flight: Width = 2
    """Workstreams that run at once."""
    judge_every: Positive = 1
    """A candidate is reviewed by the judge every this many rounds."""
    max_unmeasured_turns: Width = 2
    """Turns of one workstream that may end without a candidate reaching measurement."""
    max_continuation_rounds: NonNegative = 2
    """Times one hypothesis may be continued after its first round."""
    profiling: bool = False
    """Whether the planner may schedule profile workstreams. Launch fails without the capability."""

    @property
    def start_budget(self) -> int:
        """Workstreams the run may schedule: every one is one round of agent work."""
        return self.max_rounds * self.max_in_flight
