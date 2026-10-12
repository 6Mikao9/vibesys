"""Operations the single search declares; execution belongs to the owning libraries.

The request and outcome shapes are the shared bases of `vs_core.api.search`; this module pins
single's namespace (the persisted kind names), its prompt-context and its decoded-reading types.
A runtime executor performs each one and reports an outcome the search reads back as
`OperationFinished`. Capture is declared for the profile-guided preset and optional for the plain
one. Rendering a prompt is core's: the agent pipeline asks for it, so the search never does.
"""

from typing import ClassVar, Literal

from pydantic import BaseModel

from vibesys.metrics import EvidenceReading
from vibesys.orchestration.single.search._prompts import PromptContext
from vs_core.api.search import (
    CaptureCommandBase,
    CommandCapture,
    EvidenceReadingsBase,
    InterpretEvidenceBase,
    ParentVerification,
    RenderedArtifacts,
    RenderRoleArtifactsBase,
    StrategyOperations,
    VerifyParentRevisionBase,
)

RENDER_KIND = "single.render_role_artifacts"
VERIFY_KIND = "single.verify_parent_revision"
INTERPRET_KIND = "single.interpret_evidence"
CAPTURE_KIND = "single.capture_command"

__all__ = [
    "CAPTURE_KIND",
    "INTERPRET_KIND",
    "OPERATIONS",
    "RENDER_KIND",
    "VERIFY_KIND",
    "CaptureCommand",
    "CommandCapture",
    "EvidenceReadings",
    "InterpretEvidence",
    "ParentVerification",
    "RenderRoleArtifacts",
    "RenderedArtifacts",
    "VerifyParentRevision",
]


class RenderRoleArtifacts(RenderRoleArtifactsBase[PromptContext]):
    """Render one role prompt from a template and store it as artifacts."""

    kind: Literal["single.render_role_artifacts"] = "single.render_role_artifacts"


class VerifyParentRevision(VerifyParentRevisionBase):
    """Verify that a parent revision exports to its digest (declared; single builds on core's own)."""

    kind: Literal["single.verify_parent_revision"] = "single.verify_parent_revision"


class EvidenceReadings(EvidenceReadingsBase[EvidenceReading]):
    """Decoded readings, one per requested evidence ID the owner could decode."""


class InterpretEvidence(InterpretEvidenceBase):
    """Decode accepted evidence into typed metrics; the search never parses artifacts."""

    kind: Literal["single.interpret_evidence"] = "single.interpret_evidence"
    outcome_model: ClassVar[type[BaseModel]] = EvidenceReadings


class CaptureCommand(CaptureCommandBase):
    """Run the profile-guided attribution command at a retained revision."""

    kind: Literal["single.capture_command"] = "single.capture_command"


OPERATIONS = StrategyOperations(
    namespace="single",
    render=RenderRoleArtifacts,
    verify=VerifyParentRevision,
    interpret=InterpretEvidence,
    capture=CaptureCommand,
)
