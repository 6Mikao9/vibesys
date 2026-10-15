"""The Git metadata bound for a linked worktree is the one Git registered, whatever ``.git`` says.

The agent can write its workspace's ``.git`` file. These tests lay out a repository the
way ``git worktree add`` does, then let the agent rewrite the pointer (and plant fake
registrations in a directory it can write) and check that no host path outside the
genuine registration is ever returned for binding.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

import vs_sandbox.api as sandbox_api
from vs_sim.api.testing import examples

_SHARED = ("objects", "refs", "HEAD", "info")


def _layout(root: Path) -> tuple[Path, set[Path]]:
    """A repository with one linked worktree, as ``git worktree add`` leaves it.

    Returns the workspace and the paths its genuine registration binds.
    """
    common = root / "project" / ".git"
    for name in ("objects", "refs", "info", "hooks", "logs"):
        (common / name).mkdir(parents=True)
    (common / "HEAD").write_text("ref: refs/heads/main\n")
    (common / "config").write_text("[remote]\n\turl = https://user:token@example.invalid\n")
    workspace = root / "run" / "workspace"
    workspace.mkdir(parents=True)
    _register(common, "workspace", workspace)
    other = root / "run" / "other"
    other.mkdir()
    _register(common, "other", other)
    (root / "secret").mkdir()
    (root / "secret" / "HEAD").write_text("private\n")
    (root / "secret" / "objects").mkdir()
    gitdir = common / "worktrees" / "workspace"
    return workspace, {gitdir, *(common / name for name in _SHARED)}


def _register(common: Path, name: str, worktree: Path) -> None:
    gitdir = common / "worktrees" / name
    gitdir.mkdir(parents=True)
    (gitdir / "commondir").write_text("../..\n")
    (gitdir / "gitdir").write_text(f"{worktree / '.git'}\n")
    (gitdir / "HEAD").write_text("0" * 40 + "\n")
    (worktree / ".git").write_text(f"gitdir: {gitdir}\n")


def test_the_genuine_registration_binds_the_gitdir_and_the_shared_entries(
    tmp_path: Path,
) -> None:
    workspace, genuine = _layout(tmp_path)

    paths = sandbox_api.linked_worktree_git_paths(workspace)

    assert paths[0] == tmp_path / "project" / ".git" / "worktrees" / "workspace"
    assert set(paths) == genuine


def _fake_registration(root: Path, common: Path) -> Path:
    """A registration the agent wrote in a host directory it can write."""
    gitdir = root / "agent-writable" / "x" / "worktrees" / "w"
    gitdir.mkdir(parents=True)
    (gitdir / "commondir").write_text(f"{common}\n")
    (gitdir / "gitdir").write_text(f"{root / 'run' / 'workspace' / '.git'}\n")
    return gitdir


_TARGETS = (
    "host directory",
    "home",
    "shared repository",
    "worktrees directory",
    "another worktree",
    "relative escape",
    "fake registration of secret",
    "missing",
)


def _pointer_targets(root: Path) -> dict[str, str]:
    """Gitdirs the agent may write into its ``.git`` file, by name."""
    common = root / "project" / ".git"
    return {
        "host directory": str(root / "secret"),
        "home": str(root),
        "shared repository": str(common),
        "worktrees directory": str(common / "worktrees"),
        "another worktree": str(common / "worktrees" / "other"),
        "relative escape": "../../secret",
        "fake registration of secret": str(_fake_registration(root, root / "secret")),
        "missing": str(root / "missing" / "worktrees" / "w"),
    }


@settings(max_examples=examples(pr=20, nightly=300), deadline=None)
@given(
    target=st.sampled_from(_TARGETS),
    symlinked_pointer=st.booleans(),
)
def test_an_agent_written_pointer_never_chooses_a_host_path_to_bind(
    *, target: str, symlinked_pointer: bool
) -> None:
    """Regression: the gitdir named by the agent-writable ``.git`` was bound as-is."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        workspace, genuine = _layout(root)
        pointer = workspace / ".git"
        named = _pointer_targets(root)[target]
        pointer.unlink()
        if symlinked_pointer:
            (root / "agent-writable" / "pointer").write_text(f"gitdir: {named}\n")
            pointer.symlink_to(root / "agent-writable" / "pointer")
        else:
            pointer.write_text(f"gitdir: {named}\n")

        paths = set(sandbox_api.linked_worktree_git_paths(workspace))

        assert paths <= genuine
        assert not any(path.is_relative_to(root / "secret") for path in paths)
        assert root not in paths
        assert root / "project" / ".git" / "config" not in paths
