"""The issue-queue search's own knobs: how long to run, how hard to try one issue, how much to file.

Everything else a run needs belongs to someone else. How candidates are measured is the task's
(`ctx.task`); how an agent is asked, how long its turn may take and how often a reply is
corrected are its role's (`_roles`); which tracker holds the board is the host's. A config is an
input of the search, never persisted state: running the search again with the same config and the
same events reproduces every decision.
"""

from typing import Annotated

from pydantic import Field, field_validator

from vibesys.orchestration.issue_queue.models import LoadLevel
from vs_core.api.search import Value

type Positive = Annotated[int, Field(gt=0, le=10_000)]
type Retries = Annotated[int, Field(ge=0, le=20)]


class IssueQueueConfig(Value):
    """Budgets and bounds of one issue-queue run."""

    max_rounds: Positive
    """Rounds of work: each drains up to ``issues_per_round`` issues, then assesses performance."""
    max_attempts_per_issue: Positive
    """Implementer turns one issue may use before it is blocked."""
    max_issues_per_perf_eval: Positive
    """The most issues one performance evaluation files; any more it names are dropped."""
    max_issues_per_round: Positive | None = None
    """Issues worked in one round before the evaluation; ``None`` leaves room for the bootstrap
    issue, the evaluator's issues and a bug or two the judge found (see ``issues_per_round``)."""
    max_board_retries: Retries = 3
    """Times one board request is sent again after its effect stayed unknown."""
    load_levels: tuple[LoadLevel, ...] | None = None
    """The benchmark workloads the evaluator measures, or ``None`` to let it discover them."""

    @field_validator("load_levels", mode="before")
    @classmethod
    def _freeze_load_levels(cls, value: object) -> object:
        """Accept the input bundle's list while storing an immutable tuple."""
        return tuple(value) if isinstance(value, list) else value

    @property
    def issues_per_round(self) -> int:
        """The bound on issues worked per round."""
        if self.max_issues_per_round is not None:
            return self.max_issues_per_round
        return 2 * self.max_issues_per_perf_eval + 2

    @property
    def max_jobs(self) -> int:
        """Jobs a run can start: each round works at most the cap of issues and one evaluation."""
        return self.max_rounds * (self.issues_per_round + 1)
