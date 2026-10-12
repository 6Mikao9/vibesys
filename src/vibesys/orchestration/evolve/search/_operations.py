"""Operations the evolve search declares; execution belongs to the owning libraries.

The request and outcome shapes are the shared bases of `vs_core.api.search`; this module pins
evolve's namespace (the persisted kind names) and its prompt-context and decoded-reading types.
Rendering a prompt is core's: the agent pipeline asks for it, so the search never does.
"""

from typing import ClassVar, Literal

from pydantic import BaseModel

from vibesys.metrics import EvidenceReading
from vibesys.orchestration.evolve.search._prompts import PromptContext
from vs_core.api.search import (
    EvidenceReadingsBase,
    ExportPatchBase,
    InterpretEvidenceBase,
    ParentVerification,
    PatchExport,
    RenderedArtifacts,
    RenderRoleArtifactsBase,
    StrategyOperations,
    VerifyParentRevisionBase,
)

RENDER_KIND = "evolve.render_role_artifacts"
VERIFY_KIND = "evolve.verify_parent_revision"
INTERPRET_KIND = "evolve.interpret_evidence"
EXPORT_PATCH_KIND = "evolve.export_patch"

__all__ = [
    "EXPORT_PATCH_KIND",
    "INTERPRET_KIND",
    "OPERATIONS",
    "RENDER_KIND",
    "VERIFY_KIND",
    "EvidenceReadings",
    "ExportPatch",
    "InterpretEvidence",
    "ParentVerification",
    "PatchExport",
    "RenderRoleArtifacts",
    "RenderedArtifacts",
    "VerifyParentRevision",
]


class RenderRoleArtifacts(RenderRoleArtifactsBase[PromptContext]):
    """Render one role prompt from a template and store it as artifacts."""

    kind: Literal["evolve.render_role_artifacts"] = "evolve.render_role_artifacts"


class VerifyParentRevision(VerifyParentRevisionBase):
    """Verify that a parent revision exports to its digest (declared; evolve builds on core's own)."""

    kind: Literal["evolve.verify_parent_revision"] = "evolve.verify_parent_revision"


class EvidenceReadings(EvidenceReadingsBase[EvidenceReading]):
    """Decoded readings, one per requested evidence ID the owner could decode."""


class InterpretEvidence(InterpretEvidenceBase):
    """Decode accepted evidence into typed metrics; the search never parses artifacts."""

    kind: Literal["evolve.interpret_evidence"] = "evolve.interpret_evidence"
    outcome_model: ClassVar[type[BaseModel]] = EvidenceReadings


class ExportPatch(ExportPatchBase):
    """Export a passing candidate's patch for a selector that ranks candidates by their code."""

    kind: Literal["evolve.export_patch"] = "evolve.export_patch"


OPERATIONS = StrategyOperations(
    namespace="evolve",
    render=RenderRoleArtifacts,
    verify=VerifyParentRevision,
    interpret=InterpretEvidence,
    export_patch=ExportPatch,
)
