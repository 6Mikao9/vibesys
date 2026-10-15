"""The owner of the dynamic search's handoff files: one Markdown file per planning call.

The search asks for a `WriteHandoff` when a planning call schedules a plan. This owner renders
the request's data with the dynamic templates (`handoff/call.j2`) and writes the file into the
run's portable state, opened through `Project`, so it is kept with the run and survives a
restart or a resume. It answers with the file's path relative to the project, which is where
the planner's workspace (the run's project checkout) sees it.

The planner itself never writes: its role reads only (see docs/contributing/sandboxing.md), and
its reply is the one source of what the file says. Writing the same request again writes the
same bytes, so a replay is safe, and `inspect` says the write landed when the file holds them.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Protocol, cast

from vibesys.orchestration.dynamic.core_policy.api import prompts
from vibesys.orchestration.dynamic.search.api import HANDOFF, HandoffWritten, WriteHandoff
from vs_prompts.api import TemplateRenderer
from vs_runtime.api.core import Applied, NotApplied, OperationEntry

if TYPE_CHECKING:
    from pydantic import BaseModel

    from vs_core.api import OperationRequest
    from vs_runtime.api import OwnedOperationResources
    from vs_runtime.api.core import ExecutionContext, Inspection

HANDOFF_NAMESPACE = "orchestrator-handoff"
"""The run-state namespace that holds the handoff files."""

_RENDERER = TemplateRenderer(Path(prompts.__file__).parent)


class HandoffDocuments(Protocol):
    """Where the handoff files are kept: named documents in one run-state namespace."""

    def read_bytes(self, relative_path: str | PurePosixPath) -> bytes | None:
        """One document's bytes, or None when it is absent."""
        ...

    def write_bytes(self, relative_path: str | PurePosixPath, contents: bytes) -> None:
        """Replace one document atomically."""
        ...

    def agent_visible_path(self, relative_path: str | PurePosixPath | None = None) -> str:
        """The document's path relative to the project, as an agent's prompt names it."""
        ...


def handoff_filename(call: int) -> str:
    """The file of the ``call``-th planning call; names sort in call order."""
    return f"call-{call:04d}.md"


def render_handoff(request: WriteHandoff) -> str:
    """The text of one handoff file, from the request's data only."""
    return _RENDERER.render_template(
        "handoff/call.j2",
        call=request.call,
        reasoning=request.reasoning,
        scheduled=request.scheduled,
        updates=request.updates,
        handoff=request.handoff,
    )


class HandoffOwner:
    """Perform and inspect the handoff write over one run's documents."""

    def __init__(self, documents: HandoffDocuments) -> None:
        """Bind the run-state namespace the files are written to."""
        self._documents = documents

    async def execute(self, request: OperationRequest, context: ExecutionContext) -> BaseModel:
        """Write the file; a failed write propagates as an unknown effect."""
        del context
        return self.perform(cast("WriteHandoff", request))

    async def inspect(self, request: OperationRequest, context: ExecutionContext) -> Inspection:
        """Applied when the file already holds exactly what the request renders."""
        del context
        typed = cast("WriteHandoff", request)
        name = handoff_filename(typed.call)
        if self._documents.read_bytes(name) == render_handoff(typed).encode("utf-8"):
            return Applied(self._written(name))
        return NotApplied()

    def perform(self, request: WriteHandoff) -> HandoffWritten:
        """Write one handoff file now and say where it is."""
        name = handoff_filename(request.call)
        self._documents.write_bytes(name, render_handoff(request).encode("utf-8"))
        return self._written(name)

    def _written(self, name: str) -> HandoffWritten:
        return HandoffWritten(path=self._documents.agent_visible_path(name))


def handoff_entries(resources: OwnedOperationResources) -> tuple[OperationEntry, ...]:
    """The handoff write, owned by an owner over the run's handoff namespace."""
    namespace = resources.project.state.portable_namespace(resources.run_id, HANDOFF_NAMESPACE)
    return (OperationEntry.owned(HANDOFF, HandoffOwner(namespace)),)


__all__ = [
    "HANDOFF_NAMESPACE",
    "HandoffDocuments",
    "HandoffOwner",
    "handoff_entries",
    "handoff_filename",
    "render_handoff",
]
