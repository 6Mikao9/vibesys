"""The trusted evaluations a judge is shown for the candidate it reviews.

An agent measures its own workspace through the evaluation tool; core records the result in its
evidence ledger as trusted `local-validation` evidence of the attempt. The ledger holds
identities only, so the job asks the evidence owner to decode them (`InterpretEvidence`) before
the judge's prompt is built, and the prompt then carries each record's evidence ID, kind,
revision, verdict, metrics and failure tail as typed data.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from vibesys.metrics import accept_readings, judged_evidence
from vibesys.orchestration.issue_queue.search._operations import EvidenceReadings
from vibesys.orchestration.issue_queue.search._prompts import ReviewEvaluation

if TYPE_CHECKING:
    from vibesys.metrics import AcceptedReading
    from vs_core.api.search import EvidenceKey, EvidenceRef, RunView, Scope

_FAILURE_TAIL_CHARS = 1500
"""A failure's cause is usually stated last, so a long one keeps only its end."""


def review_refs(view: RunView, scope: Scope) -> tuple[EvidenceRef, ...]:
    """Trusted agent-submitted evaluations of the attempt's scope, oldest first."""
    return judged_evidence(view, scope)


def readings_of(outcome: object, refs: tuple[EvidenceRef, ...]) -> tuple[AcceptedReading, ...]:
    """What the owner decoded of ``refs``; a refusal leaves the evidence listed without numbers."""
    if not isinstance(outcome, EvidenceReadings) or outcome.status != "succeeded":
        return ()
    accepted = accept_readings(outcome.readings, refs)
    return () if isinstance(accepted, str) else accepted


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
        feedback="" if reading is None else reading.feedback[-_FAILURE_TAIL_CHARS:],
    )
