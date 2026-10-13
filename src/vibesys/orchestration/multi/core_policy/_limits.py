"""Run bounds derived from the validated multi config, never from constants."""

from __future__ import annotations

from typing import TYPE_CHECKING

from vibesys.candidates.api import RunBudget, turn_chain
from vibesys.candidates.api import limits_for as limits_from
from vs_core.api import MAX_SUBMISSIONS_PER_MEASUREMENT

if TYPE_CHECKING:
    from vibesys.candidates.api import Bounds
    from vibesys.orchestration.multi.strategy.api import MultiConfig
    from vs_core.api import Limits


def limits_for(config: MultiConfig, bounds: Bounds) -> Limits:
    """Bounds that fit the strategy's own budgets exactly.

    One round is one admitted attempt, run serially, so attempts are `max_rounds`. Per round:
    the three run-scoped chains (routing, profile, plan), and for each paid implementer chain
    (at most `max_retries_per_round`) one review chain, with one resume per measurement
    submission (at most core's hard cap) for each of the two roles.
    """
    chain = turn_chain(corrections=config.max_corrections, lost_turns=config.max_turn_drops)
    per_round = 3 + 2 * config.max_retries_per_round + 2 * MAX_SUBMISSIONS_PER_MEASUREMENT
    reask = config.max_corrections + config.max_turn_drops + 1
    budget = RunBudget(
        attempts=config.max_rounds,
        turns=chain * config.max_rounds * per_round,
        parallel=1,
        refunds=config.max_rounds,
        adoption_rounds=reask + 1,
    )
    return limits_from(budget, bounds)
