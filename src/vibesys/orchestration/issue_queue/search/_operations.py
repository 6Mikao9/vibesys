"""Operations the issue-queue search names; execution belongs to the owning libraries.

Core registers only a codec for each operation. The render, verify and interpret operations are
the shared bases of `vs_core.api.search` pinned to this search's namespace (the persisted kind
names) and prompt-context type; a runtime executor performs each one. The issue board's three
operations are the tracker library's own (`vs_issue_tracker`): the search lists them as owned,
so the host must provide the board's owner before the run starts, and the flows ask it through
`ctx.owners`.

Verify and interpret are part of the kit but unused here: the search builds on revisions it
retained itself and reads the evaluations its judge cites through the interpret operation only.
"""

from typing import ClassVar, Literal

from pydantic import BaseModel

from vibesys.metrics import EvidenceReading
from vibesys.orchestration.issue_queue.search._prompts import PromptContext
from vs_core.api.search import (
    EvidenceReadingsBase,
    InterpretEvidenceBase,
    ParentVerification,
    RenderedArtifacts,
    RenderRoleArtifactsBase,
    StrategyOperations,
    VerifyParentRevisionBase,
)
from vs_issue_tracker.api import board_operation_registrations

RENDER_KIND = "plain.render_role_artifacts"
VERIFY_KIND = "plain.verify_parent_revision"
INTERPRET_KIND = "plain.interpret_evidence"

__all__ = [
    "BOARD",
    "INTERPRET_KIND",
    "OPERATIONS",
    "RENDER_KIND",
    "VERIFY_KIND",
    "EvidenceReadings",
    "InterpretEvidence",
    "ParentVerification",
    "RenderRoleArtifacts",
    "RenderedArtifacts",
    "VerifyParentRevision",
]


class RenderRoleArtifacts(RenderRoleArtifactsBase[PromptContext]):
    """Render one role prompt from a template and store it as artifacts."""

    kind: Literal["plain.render_role_artifacts"] = "plain.render_role_artifacts"


class VerifyParentRevision(VerifyParentRevisionBase):
    """Declared with the kit; the issue queue builds only on revisions it retained itself."""

    kind: Literal["plain.verify_parent_revision"] = "plain.verify_parent_revision"


class EvidenceReadings(EvidenceReadingsBase[EvidenceReading]):
    """Decoded readings, one per requested evidence ID the owner could decode."""


class InterpretEvidence(InterpretEvidenceBase):
    """Decode accepted evidence into typed metrics; the search never parses artifacts."""

    kind: Literal["plain.interpret_evidence"] = "plain.interpret_evidence"
    outcome_model: ClassVar[type[BaseModel]] = EvidenceReadings


OPERATIONS = StrategyOperations(
    namespace="plain",
    render=RenderRoleArtifacts,
    verify=VerifyParentRevision,
    interpret=InterpretEvidence,
)
"""The operations the search's namespace owns."""

BOARD = board_operation_registrations()
"""The issue board's operations, owned by the tracker library (read, apply changes, log)."""
