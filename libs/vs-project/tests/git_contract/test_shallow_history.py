"""History walks on a shallow clone, including a linked worktree of one.

A property over generated histories cannot see this: the fake repository has no
``.git/shallow`` file, and a local ``git clone --depth`` ignores ``--depth``
unless the source is a ``file://`` URL. The cases are the real depths (1 and
deeper) and the linked worktree workstream opening creates. A shallow-boundary
commit still records a parent whose object was not fetched; the walk stops at
that boundary, matching ``git rev-list --max-parents=0``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from tests.support.real_git import REAL_GIT

if TYPE_CHECKING:
    from vs_project.api.testing import RepositoryFactory

REQUIRES_ON_DISK_REPOSITORY = True

_IDENTITY = (
    "-c",
    "user.name=vibesys",
    "-c",
    "user.email=vibesys@example.invalid",
)


def _git(root: Path, *args: str) -> str:
    return REAL_GIT.run_text([*_IDENTITY, *args], cwd=root).check().stdout.strip()


def _source(tmp_path: Path) -> Path:
    """A short history whose tip is a merge, so a shallow clone has grafted roots."""
    root = tmp_path / "source"
    root.mkdir()
    _git(root, "init", "-b", "main")
    (root / "file.txt").write_text("0\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "c0")
    (root / "file.txt").write_text("1\n", encoding="utf-8")
    _git(root, "commit", "-am", "c1")
    _git(root, "switch", "-c", "side")
    (root / "side.txt").write_text("side\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "side")
    _git(root, "switch", "main")
    (root / "file.txt").write_text("2\n", encoding="utf-8")
    _git(root, "commit", "-am", "c2")
    _git(root, "merge", "side", "-m", "merge")
    return root


def _clone(tmp_path: Path, source: Path, depth: int) -> Path:
    destination = tmp_path / f"clone-{depth}"
    _git(tmp_path, "clone", "--depth", str(depth), source.as_uri(), str(destination))
    return destination


@pytest.mark.parametrize("depth", [1, 2, 3])
@pytest.mark.parametrize("linked", [False, True])
def test_history_stops_at_the_shallow_boundary(
    factory: RepositoryFactory, tmp_path: Path, depth: int, *, linked: bool
) -> None:
    clone = _clone(tmp_path, _source(tmp_path), depth)
    root = clone
    if linked:
        root = tmp_path / f"worktree-{depth}"
        _git(clone, "worktree", "add", "--detach", str(root), "HEAD")
    repo = factory(root)

    expected = _git(root, "rev-list", "--max-parents=0", "--reverse", "HEAD").splitlines()[0]
    assert repo.root_commit("HEAD") == expected
    shallow = Path(_git(root, "rev-parse", "--git-path", "shallow"))
    if not shallow.is_absolute():
        shallow = root / shallow
    boundary = {
        line.strip() for line in shallow.read_text(encoding="utf-8").splitlines() if line.strip()
    }
    assert expected in boundary
    parents = [
        line.split()[1]
        for line in _git(root, "cat-file", "-p", expected).splitlines()
        if line.startswith("parent ")
    ]
    assert parents
    for parent in parents:
        assert REAL_GIT.run_text(["cat-file", "-t", parent], cwd=root).returncode != 0

    head = _git(root, "rev-parse", "HEAD")
    assert repo.is_ancestor(expected, "HEAD") is True
    if head != expected:
        assert repo.is_ancestor("HEAD", expected) is False
    assert repo.recent_subjects(20)[0].sha == head
    assert repo.has_ref_containing(expected, "refs/heads/") is True
