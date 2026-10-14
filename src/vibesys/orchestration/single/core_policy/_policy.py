"""What a single run needs core to know about its task: who may vouch for a candidate."""

from __future__ import annotations

from vs_core.api import AssessmentKind, EvidenceRequirements, TaskTerms


def requirements_for(terms: TaskTerms) -> EvidenceRequirements:
    """Each configured gate must be satisfied; single declares no local-validation authority.

    The combined agent's self-review gates its own retries but is not independent evidence, so
    eligibility rests on the trusted gates alone.
    """
    return EvidenceRequirements(
        required_assessments=(
            *((AssessmentKind.CORRECTNESS,) if "accuracy" in terms.measured else ()),
            *((AssessmentKind.BENCHMARK,) if "benchmark" in terms.measured else ()),
        )
    )
