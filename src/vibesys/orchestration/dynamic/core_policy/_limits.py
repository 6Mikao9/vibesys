"""Run bounds derived from the validated dynamic config, never from constants."""

from __future__ import annotations

from typing import TYPE_CHECKING

from vibesys.candidates.api import RunBudget, turn_chain
from vibesys.candidates.api import limits_for as limits_from
from vs_core.api import MAX_SUBMISSIONS_PER_MEASUREMENT

if TYPE_CHECKING:
    from vibesys.candidates.api import Bounds
    from vibesys.orchestration.dynamic.strategy.api import DynamicConfig
    from vs_core.api import Limits


def limits_for(config: DynamicConfig, bounds: Bounds) -> Limits:
    """Bounds that fit the strategy's own budgets exactly.

    Every workstream is one admitted attempt, so attempts are `max_rounds * max_in_flight`
    and a refund (an unsupported profile) can return at most that many. A chain of turns counts
    its first reply, `max_corrections` corrections and `max_turn_drops` asks after a lost turn.
    Per planning call (one per workstream plus the first) a first turn and up to
    `max_retries_per_round` fresh ones, and per workstream its paid turns, one judge and one
    resume per measurement submission (at most core's hard cap).
    """
    starts = config.start_budget
    chain = turn_chain(corrections=config.max_corrections, lost_turns=config.max_turn_drops)
    planning = 1 + config.max_retries_per_round
    per_workstream = config.max_retries_per_round + 1 + MAX_SUBMISSIONS_PER_MEASUREMENT
    reask = config.max_corrections + config.max_turn_drops + 1
    budget = RunBudget(
        attempts=starts,
        turns=chain * ((starts + 1) * planning + starts * per_workstream),
        parallel=config.max_in_flight,
        refunds=starts,
        adoption_rounds=reask + 1,
    )
    return limits_from(budget, bounds)
