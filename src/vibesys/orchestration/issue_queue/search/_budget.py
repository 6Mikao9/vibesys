"""What an issue-queue run may spend, stated once from its config and its roles.

A run starts at most ``max_rounds * (issues_per_round + 1)`` jobs, each one admitted attempt. An
issue costs an implementer turn and a judge turn for each of its attempts; an evaluation costs
one turn; each logical turn is a chain of its first reply, its corrections and its asks after a
lost turn, and may also be resumed once for each measurement its role submits (at most core's
hard cap). Core enforces the numbers; this module is the one place that derives them from what
the search does, so a change to a role's corrections changes the budget with it.
"""

from vibesys.candidates.api import RunBudget, turn_chain
from vibesys.orchestration.issue_queue.search._config import IssueQueueConfig
from vibesys.orchestration.issue_queue.search._roles import ROLES
from vs_core.api.search import MAX_SUBMISSIONS_PER_MEASUREMENT

ADOPTION_ROUNDS = 3
"""AdoptRevision rounds core may spend on the winner, the first included."""


def budget(config: IssueQueueConfig) -> RunBudget:
    """The attempts, turns, parallelism and refunds an issue-queue run may use."""
    submissions = MAX_SUBMISSIONS_PER_MEASUREMENT
    chain = max(turn_chain(corrections=r.corrections, lost_turns=r.lost_turns) for r in ROLES)
    per_issue = 2 * config.max_attempts_per_issue + submissions
    per_round = config.issues_per_round * per_issue + (1 + submissions)
    return RunBudget(
        attempts=config.max_jobs,
        turns=chain * config.max_rounds * per_round,
        parallel=1,
        refunds=config.max_jobs,
        adoption_rounds=ADOPTION_ROUNDS,
    )
