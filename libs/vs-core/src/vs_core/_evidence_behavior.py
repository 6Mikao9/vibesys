"""Pure projection of the run-wide identity of generated evidence."""

from __future__ import annotations

from typing import TYPE_CHECKING

from vs_core._generated.vibesys.core.v1.leaves_cv import EvidenceKey

if TYPE_CHECKING:
    from vs_core._generated.vibesys.core.v1.common_cv import EvidenceId, RequestId


class EvidenceIdentity:
    """Evidence IDs are unique within a source request, not across the run."""

    __slots__ = ()
    source_request: RequestId
    evidence_id: EvidenceId

    @property
    def key(self) -> EvidenceKey:
        """The complete identity used by ledgers, settlement and continuations."""
        return EvidenceKey(source_request=self.source_request, evidence_id=self.evidence_id)
