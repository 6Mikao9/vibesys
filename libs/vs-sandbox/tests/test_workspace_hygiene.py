"""A candidate snapshot taken after the sandbox ran holds no VibeSys-owned file.

The agent's workspace is the project. The sandbox records bookkeeping about the
container it started (image, mounts, symlink commands); if that record sat in
the workspace, the next candidate snapshot commit would sweep it into the
candidate's history, so every candidate diff would carry it and candidates would
conflict on it. The observable rule: whatever the sandbox wrote, a snapshot
commits exactly the project's own files.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from hypothesis import given, settings
from hypothesis import strategies as st
from tests.support import run_test_command
from tests.support.real_git import REAL_GIT

from vs_project.api import CliGitRepository, GitTracker, NullGitTrackerEvents
from vs_sandbox.api.testing import ScriptedDockerCli, docker_result
from vs_sandbox.docker_sandbox import DockerSandbox
from vs_sim.api.testing import SimThreads, examples

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _committed_files(root: Path) -> list[str]:
    result = run_test_command(
        ["git", "ls-tree", "-r", "--name-only", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    return sorted(result.stdout.splitlines())


@settings(max_examples=examples(pr=5, nightly=20), deadline=None)
@given(
    logged=st.booleans(),
    symlink_commands=st.lists(st.text(alphabet="abc /-", min_size=1, max_size=8), max_size=3),
)
def test_snapshot_after_sandbox_run_commits_only_project_files(
    tmp_path_factory: pytest.TempPathFactory, *, logged: bool, symlink_commands: list[str]
) -> None:
    base = tmp_path_factory.mktemp("hygiene")
    workspace = base / "workspace"
    workspace.mkdir()
    (workspace / "a.txt").write_text("1\n")
    events = NullGitTrackerEvents()
    tracker = GitTracker(
        workspace,
        run_id="run",
        events=events,
        repository=CliGitRepository(workspace, git=REAL_GIT, faults=events),
    )
    tracker.init(existing=False)

    docker = ScriptedDockerCli()
    docker.on(("docker", "run"), docker_result(stdout="c1\n"))
    docker.on(
        ("docker", "exec", "c1", "sh", "-c", "id -u agent && id -g agent"),
        docker_result(stdout=f"{os.getuid()}\n{os.getgid()}\n"),
    )
    docker.always(docker_result())
    sandbox = DockerSandbox(
        str(workspace),
        image="img",
        docker=docker,
        threads=SimThreads(),
        log_path=base / "logs" / "docker.log" if logged else None,
    )
    sandbox.start()
    sandbox.save_symlink_commands(symlink_commands)
    sandbox.stop()
    (workspace / "a.txt").write_text("2\n")

    tracker.snapshot("candidate")

    assert _committed_files(workspace) == ["a.txt"]
