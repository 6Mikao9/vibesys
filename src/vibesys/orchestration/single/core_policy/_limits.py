"""Run bounds derived from the validated single config, never from constants."""

from __future__ import annotations

from typing import TYPE_CHECKING

from vibesys.candidates.api import RunBudget, turn_chain
from vibesys.candidates.api import limits_for as limits_from
from vs_core.api import MAX_SUBMISSIONS_PER_MEASUREMENT

if TYPE_CHECKING:
    from vibesys.candidates.api import Bounds
    from vibesys.orchestration.single.strategy.api import SingleConfig
    from vs_core.api import Limits


def limits_for(config: SingleConfig, bounds: Bounds) -> Limits:
    """Bounds that fit the strategy's own budgets exactly.

    One round is one admitted attempt, run serially, so attempts are `max_rounds` and a refund
    can return at most that many. Per round: the designer's chain, `max_retries_per_round`
    paid chains and one resume per measurement submission (at most core's hard cap).
    """
    chain = turn_chain(corrections=config.max_corrections, lost_turns=config.max_turn_drops)
    per_round = 1 + config.max_retries_per_round + MAX_SUBMISSIONS_PER_MEASUREMENT
    reask = config.max_corrections + config.max_turn_drops + 1
    budget = RunBudget(
        attempts=config.max_rounds,
        turns=chain * config.max_rounds * per_round,
        parallel=1,
        refunds=config.max_rounds,
        adoption_rounds=reask + 1,
    )
    return limits_from(budget, bounds)
