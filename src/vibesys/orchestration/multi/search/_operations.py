"""Operations the multi search declares; execution belongs to the owning libraries.

The request and outcome shapes are the shared bases of `vs_core.api.search`; this module pins
multi's namespace (the persisted kind names) and its prompt-context and decoded-reading types. A
runtime executor performs each one and reports an outcome the search reads back as
`OperationFinished`. Capture is declared for the profile-guided preset and validation for the
reviewed round; each is optional, because a run without isolated candidate workspaces cannot
offer them. Rendering a prompt is core's: the agent pipeline asks for it, so the search never does.
"""

from typing import ClassVar, Literal

from pydantic import BaseModel

from vibesys.metrics import EvidenceReading
from vibesys.orchestration.multi.search._prompts import PromptContext
from vs_core.api.search import (
    CaptureCommandBase,
    CommandCapture,
    EvidenceReadingsBase,
    InterpretEvidenceBase,
    LocalValidationReport,
    ParentVerification,
    RenderedArtifacts,
    RenderRoleArtifactsBase,
    StrategyOperations,
    ValidateLocalBase,
    VerifyParentRevisionBase,
)

RENDER_KIND = "multi.render_role_artifacts"
VERIFY_KIND = "multi.verify_parent_revision"
INTERPRET_KIND = "multi.interpret_evidence"
CAPTURE_KIND = "multi.capture_command"
VALIDATE_LOCAL_KIND = "multi.validate_local"

__all__ = [
    "CAPTURE_KIND",
    "INTERPRET_KIND",
    "OPERATIONS",
    "RENDER_KIND",
    "VALIDATE_LOCAL_KIND",
    "VERIFY_KIND",
    "CaptureCommand",
    "CommandCapture",
    "EvidenceReadings",
    "InterpretEvidence",
    "LocalValidationReport",
    "ParentVerification",
    "RenderRoleArtifacts",
    "RenderedArtifacts",
    "ValidateLocal",
    "VerifyParentRevision",
]


class RenderRoleArtifacts(RenderRoleArtifactsBase[PromptContext]):
    """Render one role prompt from a template and store it as artifacts."""

    kind: Literal["multi.render_role_artifacts"] = "multi.render_role_artifacts"


class VerifyParentRevision(VerifyParentRevisionBase):
    """Verify that a parent revision exports to its digest (declared; multi builds on core's own)."""

    kind: Literal["multi.verify_parent_revision"] = "multi.verify_parent_revision"


class EvidenceReadings(EvidenceReadingsBase[EvidenceReading]):
    """Decoded readings, one per requested evidence ID the owner could decode."""


class InterpretEvidence(InterpretEvidenceBase):
    """Decode accepted evidence into typed metrics; the search never parses artifacts."""

    kind: Literal["multi.interpret_evidence"] = "multi.interpret_evidence"
    outcome_model: ClassVar[type[BaseModel]] = EvidenceReadings


class CaptureCommand(CaptureCommandBase):
    """Run the profile-guided attribution command at a retained revision."""

    kind: Literal["multi.capture_command"] = "multi.capture_command"


class ValidateLocal(ValidateLocalBase):
    """Run the validation recipes the implementer wrote against its retained candidate."""

    kind: Literal["multi.validate_local"] = "multi.validate_local"


OPERATIONS = StrategyOperations(
    namespace="multi",
    render=RenderRoleArtifacts,
    verify=VerifyParentRevision,
    interpret=InterpretEvidence,
    capture=CaptureCommand,
    validate_local=ValidateLocal,
)
