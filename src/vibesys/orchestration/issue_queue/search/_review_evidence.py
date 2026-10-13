"""The trusted evaluations a judge is shown for the candidate it reviews.

An agent measures its own workspace through the evaluation tool; core records the result in its
evidence ledger as trusted `local-validation` evidence of the attempt (`ctx.evaluation.taken`).
The ledger holds identities only, so the flow asks the evidence owner to decode them before the
judge's prompt is built, and the prompt then carries each record's evidence ID, kind, revision,
verdict, metrics and failure tail as typed data. The judge never searches the filesystem for
results.
"""

from vibesys.metrics import AcceptedReading
from vibesys.orchestration.issue_queue.search._prompts import ReviewEvaluation
from vs_core.api.search import EvidenceKey, EvidenceRef

FAILURE_TAIL_CHARS = 1500
"""A failure's cause is usually stated last, so a long one keeps only its end."""


def evaluations(
    refs: tuple[EvidenceRef, ...], readings: tuple[AcceptedReading, ...]
) -> tuple[ReviewEvaluation, ...]:
    """Each trusted evaluation record, with its reading when the owner decoded it."""
    by_key: dict[EvidenceKey, AcceptedReading] = {item.key: item for item in readings}
    return tuple(_evaluation(ref, by_key.get(ref.key)) for ref in refs)


def _evaluation(ref: EvidenceRef, reading: AcceptedReading | None) -> ReviewEvaluation:
    return ReviewEvaluation(
        evidence_id=ref.evidence_id,
        kind=ref.kind,
        revision=ref.candidate,
        status=ref.status,
        passed=None if reading is None else reading.passed,
        metrics=() if reading is None else reading.metrics,
        partial=None if reading is None else reading.partial,
        feedback="" if reading is None else reading.feedback[-FAILURE_TAIL_CHARS:],
    )
