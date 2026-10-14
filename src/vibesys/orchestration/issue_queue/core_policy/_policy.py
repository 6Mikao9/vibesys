"""The issue-queue core policy: who may vouch for a candidate."""

from __future__ import annotations

from vibesys.orchestration.issue_queue.search.api import JUDGE, issue_queue
from vs_core.api import AssessmentAuthority, AssessmentKind, EvidenceRequirements


def requirements_for() -> EvidenceRequirements:
    """The judge vouches for local validation, and a closed issue needs that vouching.

    Core grants no role implicit authority, so without the judge's declared authority its
    verdict is not a valid assessment and no candidate is ever winner-eligible. The loop has
    no trusted gates of its own per issue, so the judge's assessment is the only one required;
    the accuracy proof adoption needs is measured once, on the final candidate.
    """
    return EvidenceRequirements(
        assessment_authorities=(
            AssessmentAuthority(
                kind=AssessmentKind.LOCAL_VALIDATION,
                role_id=issue_queue.ids.role_id(JUDGE.name),
                output_schema=JUDGE.reply_schema,
            ),
        ),
        required_assessments=(AssessmentKind.LOCAL_VALIDATION,),
    )
