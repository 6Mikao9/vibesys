"""What a multi run needs core to know about its task: who may vouch for a candidate."""

from __future__ import annotations

from vibesys.orchestration.multi.search.api import JUDGE, multi
from vs_core.api import AssessmentAuthority, AssessmentKind, EvidenceRequirements, TaskTerms


def requirements_for(terms: TaskTerms) -> EvidenceRequirements:
    """The judge vouches for local validation; each configured gate must be satisfied.

    Core grants no role implicit authority, so without the judge's declared authority its
    verdict is not a valid assessment and no candidate is ever winner-eligible.
    """
    return EvidenceRequirements(
        assessment_authorities=(
            AssessmentAuthority(
                kind=AssessmentKind.LOCAL_VALIDATION,
                role_id=multi.ids.role_id(JUDGE.name),
                output_schema=JUDGE.reply_schema,
            ),
        ),
        required_assessments=(
            *((AssessmentKind.CORRECTNESS,) if "accuracy" in terms.measured else ()),
            *((AssessmentKind.BENCHMARK,) if "benchmark" in terms.measured else ()),
        ),
    )
