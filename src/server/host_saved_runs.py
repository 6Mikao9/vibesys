"""Saved-run discovery projection independent of history conversion.

The project catalog supplies metadata; this module never derives run facts.
``page_saved_runs`` fences pages by that catalog without reading or importing journals. This command module loads only for saved-run discovery.
"""

from __future__ import annotations

import hashlib
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from vs_project.api import Project
    from vs_proto.api import host_payloads as pb

__all__ = ["page_saved_runs"]


def _availability(project: Project, run: pb.SavedRun) -> None:
    """Read one bounded head or refusal; conversion and publication have their own owner."""
    wire = import_module("vs_proto.api")
    pb = wire.host_payloads
    path = project.run_log_path_for(project.root, run.run_id)
    head = path.with_name("run.head")
    refusal = path.with_name("run.conversion.pb")
    try:
        if head.is_file():
            value = wire.RunHead.from_binary(head.read_bytes())
            wire.validate(value)
            if path.is_file():
                run.imported = pb.SavedRunImport.AVAILABLE
                return
        run.imported = pb.SavedRunImport.UNAVAILABLE
        if refusal.is_file():
            value = wire.RunConversionRefusal.from_binary(refusal.read_bytes())
            wire.validate(value)
            run.detail = value.reason
        else:
            run.detail = (
                "History conversion pending"
                if path.with_name("core-events.jsonl").is_file()
                else "journal is missing"
            )
    except (OSError, ValueError) as error:
        run.imported = pb.SavedRunImport.UNAVAILABLE
        run.detail = str(error)


def page_saved_runs(
    request: pb.ListSavedRunsRequest,
    root: Path,
    runs: list[pb.SavedRun],
) -> pb.ListSavedRunsResponse:
    """Return at most 200 rows and 1 MiB; refuse continuation after catalog drift.

    Listing reads at most one head or refusal per run, never the journal or
    conversion. Unavailable history stays listed with a reason.
    """
    project_type = import_module("vs_project.api").Project
    pb = import_module("vs_proto.api").host_payloads

    digest = hashlib.sha256(str(root).encode())
    for run in runs:
        encoded = run.to_binary()
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    revision = digest.hexdigest()
    if request.revision and request.revision != revision:
        message = "revision: saved-run catalog changed; restart the listing"
        raise ValueError(message)
    if request.offset > len(runs):
        message = "offset: exceeds saved-run catalog"
        raise ValueError(message)
    response = pb.ListSavedRunsResponse(
        request_id=request.request_id, project_root=str(root), revision=revision
    )
    project = project_type.open(root)
    for index in range(request.offset, min(request.offset + 200, len(runs))):
        run = pb.SavedRun.from_binary(runs[index].to_binary())
        _availability(project, run)
        response.runs.append(run)
        if index + 1 < len(runs):
            response.next_offset = index + 1
        else:
            response.clear_field("next_offset")
        if len(response.to_binary()) > 1 << 20:
            response.runs.pop()
            response.next_offset = index
            if not response.runs:
                message = "saved run exceeds response byte budget"
                raise ValueError(message)
            break
    return response
