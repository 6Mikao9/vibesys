"""What an evolve run may spend, stated once from its config and its roles.

A run starts at most every cold start plus every child of every generation, and each is one
admitted attempt. Core enforces the numbers; this is the one place that derives them from what
the search does, so a change to a role's corrections or lost-turn tries changes the budget with it.
"""

from vibesys.candidates.api import RunBudget, turn_chain
from vibesys.orchestration.evolve.search._config import EvolveConfig
from vibesys.orchestration.evolve.search._roles import JUDGE, MUTATOR, PROFILER
from vs_core.api.search import MAX_SUBMISSIONS_PER_MEASUREMENT, Role

ADOPTION_ROUNDS = 3
"""AdoptRevision rounds core may spend on the winner, the first included."""


def _chain(role: Role) -> int:
    return turn_chain(corrections=role.corrections, lost_turns=role.lost_turns)


def budget(config: EvolveConfig) -> RunBudget:
    """The attempts, turns, parallelism and refunds an evolve run may use."""
    # The mutator and the judge may each wait on measurements, one resumed turn per submission;
    # the profiler is asked once.
    waiting = _chain(MUTATOR) + _chain(JUDGE)
    per_attempt = waiting * (1 + MAX_SUBMISSIONS_PER_MEASUREMENT) + _chain(PROFILER)
    attempts = config.total_attempts
    return RunBudget(
        attempts=attempts,
        turns=attempts * per_attempt,
        parallel=config.max_parallelism,
        refunds=attempts,
        adoption_rounds=ADOPTION_ROUNDS,
    )
