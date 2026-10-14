"""What a finished issue job asserts when its workspace closes.

A closed issue whose attempt changed the tree is a retained, eligible candidate vouched for by
the judge, and the next job builds on it. Nothing else is kept: an issue that closed without a
change, a blocked or failed one, and a performance evaluation settle discarded.
"""

from vibesys.orchestration.issue_queue.search._values import Closing
from vs_core.api.search import Assessment, AssessmentKind, AssessmentProposal


def assessment_of(closing: Closing) -> Assessment:
    """How the workspace settles: the outcome, the judge's vouching and what is retained."""
    if closing.failure is None and closing.closed:
        if closing.candidate is not None and closing.judge is not None:
            vouched = AssessmentProposal(
                kind=AssessmentKind.LOCAL_VALIDATION,
                verdict="satisfied",
                sources=(closing.judge,),
                candidate=closing.candidate,
                schema_version=1,
            )
            return Assessment(
                outcome="succeeded", assessments=(vouched,), eligible=True, retention="candidate"
            )
        return Assessment(outcome="succeeded", retention="discard")
    return Assessment(outcome="failed", retention="discard")


def retained(closing: Closing) -> bool:
    """Whether the closing keeps its candidate (see `assessment_of`)."""
    return assessment_of(closing).retention == "candidate"
