"""Operations the issue-queue search declares; execution belongs to the owning libraries.

Rendering a prompt is core's (the agent pipeline asks for it under the `plain` namespace,
`vs_core.api.search.StrategyOperations`). The search reads and changes the issue board through
the operations `vs_issue_tracker` owns. Core registers only a codec for each; a runtime owner
performs them and reports an outcome the search reads back as `OperationFinished`.

The verify and interpret operations are declared because the shared namespace registers them
beside render. The search requires verify to measure the final tip and interpret to read what
the final measurement reported; it builds on revisions it retained itself, so it needs no other.
"""

from typing import ClassVar, Literal

from pydantic import BaseModel

from vibesys.metrics import EvidenceReading
from vibesys.orchestration.issue_queue.search._prompts import PromptContext
from vs_core.api.search import (
    EvidenceReadingsBase,
    InterpretEvidenceBase,
    OperationSchemaRef,
    RenderedArtifacts,
    RenderRoleArtifactsBase,
    StrategyOperations,
    VerifyParentRevisionBase,
)
from vs_issue_tracker.api import (
    APPLY_KIND,
    LOG_KIND,
    READ_KIND,
    AppliedChanges,
    ApplyIssueChanges,
    BoardSnapshot,
    LoggedProgress,
    LogProgress,
    ReadBoard,
    board_operation_registrations,
)

RENDER_KIND = "plain.render_role_artifacts"
VERIFY_KIND = "plain.verify_parent_revision"
INTERPRET_KIND = "plain.interpret_evidence"


class RenderRoleArtifacts(RenderRoleArtifactsBase[PromptContext]):
    """Render one role prompt from a template and store it as artifacts."""

    kind: Literal["plain.render_role_artifacts"] = "plain.render_role_artifacts"


class VerifyParentRevision(VerifyParentRevisionBase):
    """Declared with the namespace; the issue queue builds only on revisions it retained itself."""

    kind: Literal["plain.verify_parent_revision"] = "plain.verify_parent_revision"


class EvidenceReadings(EvidenceReadingsBase[EvidenceReading]):
    """Decoded readings, one per requested evidence ID the owner could decode."""


class InterpretEvidence(InterpretEvidenceBase):
    """Declared with the namespace; the issue queue reads no evidence."""

    kind: Literal["plain.interpret_evidence"] = "plain.interpret_evidence"
    outcome_model: ClassVar[type[BaseModel]] = EvidenceReadings


OPERATIONS = StrategyOperations(
    namespace="plain",
    render=RenderRoleArtifacts,
    verify=VerifyParentRevision,
    interpret=InterpretEvidence,
)


def board_schema_ref(kind: str) -> OperationSchemaRef:
    """The declared schema reference of one board operation kind."""
    for registration in board_operation_registrations():
        descriptor = registration.descriptor
        if descriptor.kind == kind:
            return OperationSchemaRef(
                kind=kind,
                request_schema=descriptor.request_schema,
                outcome_schema=descriptor.outcome_schema,
                lifecycle=descriptor.lifecycle,
            )
    raise KeyError(kind)


__all__ = [
    "APPLY_KIND",
    "INTERPRET_KIND",
    "LOG_KIND",
    "OPERATIONS",
    "READ_KIND",
    "RENDER_KIND",
    "VERIFY_KIND",
    "AppliedChanges",
    "ApplyIssueChanges",
    "BoardSnapshot",
    "EvidenceReadings",
    "InterpretEvidence",
    "LogProgress",
    "LoggedProgress",
    "ReadBoard",
    "RenderRoleArtifacts",
    "RenderedArtifacts",
    "VerifyParentRevision",
    "board_schema_ref",
]
