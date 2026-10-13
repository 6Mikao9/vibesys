"""Run bounds derived from the validated evolve config, never from constants."""

from __future__ import annotations

from typing import TYPE_CHECKING

from vibesys.candidates.api import RunBudget, turn_chain
from vibesys.candidates.api import limits_for as limits_from
from vs_core.api import MAX_SUBMISSIONS_PER_MEASUREMENT

if TYPE_CHECKING:
    from vibesys.candidates.api import Bounds
    from vibesys.orchestration.evolve.strategy.api import EvolveConfig
    from vs_core.api import Limits


def limits_for(config: EvolveConfig, bounds: Bounds) -> Limits:
    """Bounds that fit the strategy's own budgets exactly.

    One child is one attempt, and a run starts at most every cold start plus every child of
    every generation, so attempts are `total_attempts` and a refund can return at most that
    many. Per attempt: the mutator, judge and profiler chains, and one resume per measurement
    submission (at most core's hard cap) for each of the two roles that may wait.
    """
    chain = turn_chain(corrections=config.max_corrections, lost_turns=config.max_turn_drops)
    per_attempt = 3 + 2 * MAX_SUBMISSIONS_PER_MEASUREMENT
    attempts = config.total_attempts
    reask = config.max_corrections + config.max_turn_drops + 1
    budget = RunBudget(
        attempts=attempts,
        turns=chain * attempts * per_attempt,
        parallel=config.max_parallelism,
        refunds=attempts,
        adoption_rounds=reask + 1,
    )
    return limits_from(budget, bounds)
