"""What a finished slot asserts to core: the shared candidate policy plus evolve's own cases.

Candidate settlement (assessments, eligibility and retention) is `vibesys.candidates`; the
judge's verdict is its `LOCAL_VALIDATION` assessment, and evolve has no input baseline to beat.
Evolve adds two cases of its own. A child that passes is winner-eligible and retained as a
candidate. A cold start that failed after leaving a checkpoint keeps it as work in progress,
because the next cold start builds on it. Nothing else is retained for adoption: a failed
child's tree is discarded and only its feedback survives.
"""

from vibesys.candidates import api as candidates
from vibesys.orchestration.evolve.search._config import EvolveConfig
from vibesys.orchestration.evolve.search._values import Finding, SlotPlan
from vs_core.api.search import Settle


def settle_for(plan: SlotPlan, finding: Finding, config: EvolveConfig) -> Settle:
    """The assessments, eligibility and retention to propose for a finished slot."""
    settled = candidates.settle_for(finding, candidates.NotConfigured(), config)
    if settled.eligible and not finding.passed:
        # Every gate is satisfied in the evidence, yet the slot did not pass (for example the
        # review never produced a verdict): no independent verdict supports it.
        settled = settled.model_copy(update={"eligible": False})
    if (
        plan.cold_start
        and settled.retention == "discard"
        and finding.candidate is not None
        and not finding.stopped
    ):
        return settled.model_copy(
            update={"retention": "wip", "candidate": finding.candidate, "eligible": False}
        )
    return settled
