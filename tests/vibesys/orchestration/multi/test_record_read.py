"""A multi run's published ledger is read back through its projector from the stored record."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from tests.support.multi_search import SearchRecord
from tests.vibesys.orchestration.test_projection import _search_state

from vibesys.hypothesis.readmodel import project_hypothesis_state
from vibesys.multi_core import multi_projector
from vibesys.run.contracts import RunStatus
from vs_project.api import OrchestrationDescriptor, Project, RunEnvironmentRecord
from vs_project.api.testing import run_execution_record

if TYPE_CHECKING:
    from pathlib import Path


def _project_with_run(root: Path) -> tuple[Project, str]:
    project = Project.open(root)
    project.state.create_project("projection", now=datetime(2026, 1, 1, tzinfo=UTC))
    manifest = project.state.new_run_manifest(
        "projection",
        branch="test",
        vibesys_version="test",
        run_environment=RunEnvironmentRecord(name="docker"),
        execution=run_execution_record(),
        orchestration=OrchestrationDescriptor(id="multi", config_version=1, options={}),
        trusted_input_baseline="a" * 40,
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )
    project.state.create_run(manifest)
    return project, manifest.run_id


def test_the_saved_ledger_is_what_the_projector_reads(tmp_path: Path) -> None:
    project, run_id = _project_with_run(tmp_path)
    search = _search_state()
    SearchRecord(project, run_id).save(search)

    view = multi_projector("multi").view(project, run_id, status=RunStatus.ACTIVE, loop="multi")

    assert view.rounds == project_hypothesis_state(search).rounds
