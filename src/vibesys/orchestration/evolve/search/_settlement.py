"""What a finished child asserts to core: the shared candidate policy plus evolve's own cases.

Candidate settlement (assessments, eligibility, retention) is `vibesys.candidates`; the judge's
verdict is its `LOCAL_VALIDATION` assessment. Evolve adds two cases of its own. A child that
passes is winner-eligible and retained as a candidate. A cold start that failed after leaving a
checkpoint keeps it as work in progress, because the next cold start builds on it. Nothing else
is retained for adoption: a failed child's tree is discarded and only its feedback survives.
"""

from vibesys.candidates.api import STOPPED, gates_of, settle_for
from vibesys.orchestration.evolve.search._values import ChildStart, Closing
from vs_core.api.search import Settle, TaskTerms


def assertion(start: ChildStart, closing: Closing, terms: TaskTerms) -> Settle:
    """The assessments, eligibility and retention to propose for a finished child.

    Evolve does not measure the input, so a candidate has nothing to beat but its gates.
    """
    settled = settle_for(closing, gates=gates_of(terms), beats_input=True, unsettled=frozenset())
    if settled.eligible and not closing.passed:
        # Every gate is satisfied in the evidence, yet the child did not pass (for example the
        # review never produced a verdict): no independent verdict supports it.
        settled = settled.model_copy(update={"eligible": False})
    if (
        start.cold_start
        and settled.retention == "discard"
        and closing.revision is not None
        and closing.failure != STOPPED
    ):
        return settled.model_copy(
            update={"retention": "wip", "candidate": closing.revision, "eligible": False}
        )
    return settled
