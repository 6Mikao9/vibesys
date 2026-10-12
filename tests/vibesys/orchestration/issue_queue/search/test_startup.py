"""Production startup accepts the search's own declaration, not a mirror of it."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tests.vibesys.orchestration.issue_queue.search.world import issue_queue_config

from vibesys.orchestration.issue_queue.core_policy.api import build_search, plain_operation_registry
from vibesys.orchestration.issue_queue.search.api import OPERATIONS
from vibesys.prompts import CORE_PROTOCOL_DIR
from vs_core.api import Capabilities, validate_startup
from vs_issue_tracker.api import (
    FakeIssueTracker,
    LogProgress,
    MemoryProgressLog,
    board_operation_registrations,
)
from vs_project.api import Project
from vs_prompts.api import TemplateRenderer
from vs_runtime.api import ArtifactStore
from vs_runtime.api.core import (
    IssueBoardOwner,
    OperationCatalog,
    OperationEntry,
    OperationPorts,
    bind_operations,
    build_operation_catalog,
    operation_roles,
    production_owners,
)
from vs_runtime.api.testing import FakeEvidenceLedger, FakeWorkspace, FakeWorkspaces

if TYPE_CHECKING:
    from pathlib import Path


class _Texts:
    def issue_body(self, template: str) -> str:
        return template

    def progress_entry(self, request: LogProgress) -> str:
        return request.step


def test_production_owners_cover_every_operation_the_search_requires(tmp_path: Path) -> None:
    (tmp_path / "project").mkdir()
    namespace = Project.open(tmp_path / "project").state.state_store_namespace("run")
    registrations = operation_roles(OPERATIONS)
    workspaces = FakeWorkspaces(FakeWorkspace())
    ports = OperationPorts(
        renderer=TemplateRenderer(tmp_path, fallback_roots=(CORE_PROTOCOL_DIR,)),
        artifacts=ArtifactStore(namespace),
        workspaces=workspaces,
        ledger=workspaces,
        evidence=FakeEvidenceLedger(),
        commit_of=lambda revision: revision.git_commit,
        retention_label="verified",
    )
    kit = build_operation_catalog(registrations, production_owners(registrations, ports))
    owner = IssueBoardOwner(FakeIssueTracker(), MemoryProgressLog(), _Texts())
    board = tuple(
        OperationEntry.owned(registration, owner)
        for registration in board_operation_registrations()
    )
    catalog = OperationCatalog(plain_operation_registry(), (*kit.entries, *board))
    declaration = build_search(issue_queue_config()).declaration
    catalog.require_owned(declaration)
    validate_startup(declaration, Capabilities(operations=catalog.offered_operations))
    bind_operations(declaration, catalog, namespace)
