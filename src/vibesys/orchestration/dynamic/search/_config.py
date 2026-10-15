"""The dynamic search's own knobs: how wide, how long, how often to review, when to give up.

Everything else a run needs belongs to someone else. How candidates are measured and compared is
the task's (`ctx.task`); how an agent is asked is its role's (`_roles`); how a backend retries
its infrastructure is the backend's. A config is an input of the search, never persisted state:
running the search again with the same config and the same events reproduces every decision.
"""

from typing import Annotated, Self

from pydantic import Field, model_validator

from vibesys.orchestration.dynamic.search._steps import Outline, PlanMode
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
    mode: PlanMode = "explore"
    """``explore``: the planner proposes hypotheses. ``follow_plan``: the run implements the
    task's plan step by step, and the planner is asked only to repair a failed step and, once
    every step is done, to explore from there."""
    plan: Outline | None = None
    """The task's plan (its ``[plan]`` table); read only when ``mode`` is ``follow_plan``."""

    @model_validator(mode="after")
    def _a_plan_to_follow(self) -> Self:
        if self.mode == "follow_plan" and self.plan is None:
            message = (
                "mode 'follow_plan' needs the task's plan, and the task declares none: add a "
                "[plan] table to its vibesys.input.toml, or launch with mode 'explore'"
            )
            raise ValueError(message)
        return self

    @property
    def follows_plan(self) -> bool:
        """Whether the run follows the task's plan."""
        return self.mode == "follow_plan"

    @property
    def start_budget(self) -> int:
        """Workstreams the run may schedule: every one is one round of agent work."""
        return self.max_rounds * self.max_in_flight
