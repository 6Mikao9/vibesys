"""Run bounds derived from the validated issue_queue config, never from constants."""

from __future__ import annotations

from typing import TYPE_CHECKING

from vibesys.candidates.api import RunBudget
from vibesys.candidates.api import limits_for as limits_from
from vs_core.api import MAX_SUBMISSIONS_PER_MEASUREMENT

if TYPE_CHECKING:
    from vibesys.candidates.api import Bounds
    from vibesys.orchestration.issue_queue.strategy.api import IssueQueueConfig
    from vs_core.api import Limits


def limits_for(config: IssueQueueConfig, bounds: Bounds) -> Limits:
    """Bounds that fit the strategy's own budgets exactly.

    Each round works at most ``max_issues_per_round`` issues and runs one evaluation, and
    every job is one attempt, so attempts are rounds times that plus one. An issue has a
    logical implementer turn and a judge turn for each of its ``max_attempts_per_issue``
    attempts, an evaluation one; each logical turn is a chain of its first reply, its
    corrections and its asks after a lost turn, and may also be resumed once for each
    measurement its role submits (at most core's hard cap).
    """
    rounds = config.max_rounds
    submissions = MAX_SUBMISSIONS_PER_MEASUREMENT
    per_issue = 2 * config.max_attempts_per_issue + submissions
    per_round = config.max_issues_per_round * per_issue + (1 + submissions)
    extra = config.max_corrections + config.max_turn_drops
    logical = rounds * (config.max_issues_per_round * 2 * config.max_attempts_per_issue + 1)
    reask = extra * logical + 1
    budget = RunBudget(
        attempts=config.max_attempts,
        turns=config.chain * rounds * per_round,
        parallel=1,
        refunds=config.max_attempts,
        adoption_rounds=reask + 1,
        reask_chain=reask,
    )
    return limits_from(budget, bounds)
