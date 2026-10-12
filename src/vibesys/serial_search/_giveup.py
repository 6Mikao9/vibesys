"""Why a serial search stopped designing hypotheses, as typed data and as the run's reason.

Designing ends for good when the attribution a profile-guided run depends on fails, or when the
designer cannot produce a plan the ledger accepts within its corrections. The search keeps a
`GiveUp` and words it once, for the operator, when the run ends.
"""

from __future__ import annotations

from pydantic import model_validator

from vibesys.serial_search._attribution import (
    AttributionConfig,
    AttributionFailure,
    attribution_reason,
)
from vibesys.serial_search._plans import PlanProblem, ProblemKind
from vs_core.api.search import Value


class GiveUp(Value):
    """Why no further hypothesis can be designed: exactly one of the two causes."""

    problem: PlanProblem | None = None
    """The designer's last turn produced no plan the ledger accepts."""
    attribution: AttributionFailure | None = None
    """The profile-guided attribution could not steer the designer."""

    @model_validator(mode="after")
    def one_cause(self) -> GiveUp:
        """Refuse a give-up without a cause or with two."""
        if (self.problem is None) == (self.attribution is None):
            message = "a give-up has exactly one cause"
            raise ValueError(message)
        return self


def _problem_reason(problem: PlanProblem) -> str:
    match problem.kind:
        case ProblemKind.UNPARSEABLE:
            return f"the designer's plan broke its schema in {len(problem.errors)} places"
        case ProblemKind.REJECTED:
            fault = problem.fault.value if problem.fault else "unknown"
            return f"the designer's plan was rejected ({fault}: {problem.hypothesis_id})"
        case ProblemKind.NO_REPLY:
            how = problem.turn_failure or problem.ended
            return f"the designer's turn ended without a plan ({how.value if how else 'no reason'})"


def giveup_reason(giveup: GiveUp, attribution: AttributionConfig | None) -> str:
    """The operator-facing reason designing stopped."""
    if giveup.attribution is not None and attribution is not None:
        return attribution_reason(giveup.attribution, attribution)
    if giveup.problem is not None:
        return _problem_reason(giveup.problem)
    return "the attribution failed"
