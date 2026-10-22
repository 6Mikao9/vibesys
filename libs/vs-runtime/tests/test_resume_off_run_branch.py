"""Resume after the project checkout left the run branch.

Real Git is required. The fake repository has no checkout to move off
``vibesys-runs/<run id>``, and the missing manifest is that branch's committed
file. ``open_project_run_resources`` is the continue path ``vibesys --resume`` uses.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from tests.support.real_git import REAL_GIT

from vs_project.api import (
    NullGitTrackerEvents,
    OrchestrationDescriptor,
    Project,
    ProjectStateError,
    RunEnvironmentRecord,
    git_repositories,
)
from vs_project.api.testing import run_execution_record
from vs_runtime.api import OrchestrationResumeDecision
from vs_runtime.api.infrastructure import (
    ProjectRunEffects,
    ProjectRunRequest,
    open_project_run_resources,
)
from vs_sim.api.testing import ManualClock

if TYPE_CHECKING:
    from pathlib import Path

    from vs_project.api import OrchestrationRunManifest

_RUN_ID = "off-branch"


@pytest.fixture(autouse=True)
def isolated_project_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("VIBESYS_STATE_HOME", str(tmp_path / "operator-state"))


def _request(root: Path, *, existing: bool = False) -> ProjectRunRequest:
    return ProjectRunRequest(
        project_root=root,
        run_id=_RUN_ID,
        display_name="off branch",
        task_name=None,
        existing=existing,
        framework_version="1.2.3",
        run_environment=RunEnvironmentRecord(name="docker"),
        execution=run_execution_record(),
        orchestration=OrchestrationDescriptor(id="test-policy", config_version=1, options={}),
    )


def _effects() -> ProjectRunEffects:
    return ProjectRunEffects(
        git_events=NullGitTrackerEvents(),
        git_repository=git_repositories(REAL_GIT),
        log_emit=lambda _text, _writer: None,
        on_log_ready=lambda _log_dir: None,
        clock=ManualClock(),
    )


def _keep(_manifest: OrchestrationRunManifest) -> OrchestrationResumeDecision:
    return OrchestrationResumeDecision(descriptor=None)


def _git(root: Path, *args: str) -> str:
    return REAL_GIT.run_text(list(args), cwd=root).check().stdout.strip()


def _started(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "project"
    root.mkdir()
    (root / "candidate.py").write_text("VALUE = 1\n", encoding="utf-8")
    with open_project_run_resources(
        _request(root), effects=_effects(), resolve_resume=_keep
    ) as resources:
        baseline = resources.git.trusted_input_baseline
    assert baseline is not None
    return root, baseline


def _manifest(root: Path) -> Path:
    return root / Project.open(root).state.run_directory(_RUN_ID) / "run.json"


def _resume(root: Path) -> None:
    with open_project_run_resources(
        _request(root, existing=True), effects=_effects(), resolve_resume=_keep
    ) as resources:
        assert resources.project.state.load_run(_RUN_ID).run_id == _RUN_ID


@pytest.mark.parametrize("place", ["detached", "main"])
def test_resume_checks_out_the_run_branch(tmp_path: Path, place: str) -> None:
    root, baseline = _started(tmp_path)
    if place == "detached":
        _git(root, "switch", "--detach", baseline)
        assert _git(root, "rev-parse", "HEAD") == baseline
    else:
        _git(root, "switch", "main")
    assert _git(root, "branch", "--show-current") != f"vibesys-runs/{_RUN_ID}"
    assert not _manifest(root).is_file()

    _resume(root)

    assert _git(root, "branch", "--show-current") == f"vibesys-runs/{_RUN_ID}"
    assert _manifest(root).is_file()


def test_resume_refuses_a_dirty_tree_and_leaves_it(tmp_path: Path) -> None:
    root, baseline = _started(tmp_path)
    _git(root, "switch", "--detach", baseline)
    (root / "candidate.py").write_text("DIRTY\n", encoding="utf-8")
    head = _git(root, "rev-parse", "HEAD")

    with pytest.raises(ValueError, match=rf"vibesys-runs/{_RUN_ID}"):
        _resume(root)

    assert _git(root, "rev-parse", "HEAD") == head
    assert _git(root, "branch", "--show-current") == ""
    assert (root / "candidate.py").read_text(encoding="utf-8") == "DIRTY\n"


def test_resume_keeps_a_dirty_checkout_already_on_the_run_branch(tmp_path: Path) -> None:
    root, _baseline = _started(tmp_path)
    (root / "candidate.py").write_text("DIRTY\n", encoding="utf-8")

    _resume(root)

    assert _git(root, "branch", "--show-current") == f"vibesys-runs/{_RUN_ID}"
    assert (root / "candidate.py").read_text(encoding="utf-8") == "DIRTY\n"


def test_missing_manifest_names_the_run_branch(tmp_path: Path) -> None:
    root, baseline = _started(tmp_path)
    _git(root, "switch", "--detach", baseline)
    project = Project.open(root)

    with pytest.raises(ProjectStateError, match=rf"branch vibesys-runs/{_RUN_ID}"):
        project.state.load_run(_RUN_ID)

    project.state.require_recorded_run(_RUN_ID)
    assert _git(root, "branch", "--show-current") == ""
