"""What a single run may spend, stated once from its config and its roles.

A round is one admitted attempt, run serially, so the run admits `max_rounds` attempts. Core
enforces the numbers; this module is the one place that derives them from what the search does,
so a change to a role's corrections or to the number of paid turns changes the budget with it.
"""

from vibesys.candidates.api import RunBudget, turn_chain
from vibesys.orchestration.single.search._config import SingleConfig
from vibesys.orchestration.single.search._roles import IMPLEMENTER
from vibesys.serial_search.api import ORCHESTRATOR, PLAN_CORRECTIONS
from vs_core.api.search import MAX_SUBMISSIONS_PER_MEASUREMENT

ADOPTION_ROUNDS = 3
"""AdoptRevision rounds core may spend on the winner, the first included."""


def budget(config: SingleConfig) -> RunBudget:
    """The attempts, turns, parallelism and refunds a single run may use."""
    designing = turn_chain(corrections=ORCHESTRATOR.corrections, lost_turns=ORCHESTRATOR.lost_turns)
    implementing = turn_chain(
        corrections=IMPLEMENTER.corrections, lost_turns=IMPLEMENTER.lost_turns
    )
    # The designer is asked, and corrected when the ledger refuses its plan; each paid
    # implementer turn is a chain, with one resume per measurement submission.
    per_round = designing * (1 + PLAN_CORRECTIONS) + implementing * (
        config.max_retries_per_round + MAX_SUBMISSIONS_PER_MEASUREMENT
    )
    return RunBudget(
        attempts=config.max_rounds,
        turns=config.max_rounds * per_round,
        parallel=1,
        refunds=config.max_rounds,
        adoption_rounds=ADOPTION_ROUNDS,
    )
