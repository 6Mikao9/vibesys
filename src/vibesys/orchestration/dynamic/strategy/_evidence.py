"""Which readings the strategy accepts back.

The strategy embeds the ledger's own `EvidenceRef` values (chosen with
`vs_core.api.trusted_keys` and `ledger_refs`) in an `InterpretEvidence` request and accepts
back only readings for exactly the records it asked about. A reading that names another
record is refused with a typed reason; it never reaches a decision.
"""

from vibesys.orchestration.dynamic.strategy._operations import EvidenceReadings
from vibesys.orchestration.dynamic.strategy._rows import AcceptedReading
from vs_core.api import EvidenceRef, ObservationStatus


def accept_readings(
    outcome: EvidenceReadings, requested: tuple[EvidenceRef, ...]
) -> tuple[AcceptedReading, ...] | str:
    """Bind each reading to the one requested record it decodes, or say why not.

    A reading must name a requested evidence ID that no other requested record
    shares, at most once, with the record's own kind, and may claim a pass only for
    a record whose observation succeeded.
    """
    accepted: list[AcceptedReading] = []
    for reading in outcome.readings:
        matches = tuple(item for item in requested if item.evidence_id == reading.evidence_id)
        if len(matches) != 1:
            return f"reading {reading.evidence_id.root} names no single requested record"
        (ref,) = matches
        if reading.kind is not ref.kind:
            return f"reading {reading.evidence_id.root} has kind {reading.kind.value}"
        if reading.passed and ref.status is not ObservationStatus.SUCCEEDED:
            return f"reading {reading.evidence_id.root} passed over a {ref.status.value} record"
        if any(item.evidence_id == reading.evidence_id for item in accepted):
            return f"reading {reading.evidence_id.root} repeated"
        accepted.append(AcceptedReading(**reading.model_dump(), source_request=ref.source_request))
    return tuple(accepted)
