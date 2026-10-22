"""GitRepository contract: restoring the worktree and linked worktrees."""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from vs_project.api import GitCommandError
from vs_project.api.testing import ContractProject
from vs_sim.api.testing import examples

if TYPE_CHECKING:
    from pathlib import Path

    from vs_project.api import GitRepository


def test_clean_keeps_files_under_a_directory_that_replaced_a_tracked_file(
    sandbox: ContractProject,
) -> None:
    sandbox.start({"a.txt": "1\n"})
    sandbox.delete("a.txt")
    sandbox.write("a.txt/inner.txt", "inner\n")

    assert sandbox.repo.clean_untracked(include_ignored=False, protect="protected/")

    assert (sandbox.root / "a.txt" / "inner.txt").exists()
    sandbox.repo.restore_worktree("HEAD")
    assert (sandbox.root / "a.txt").read_text(encoding="utf-8") == "1\n"


def test_reset_index_unstages_everything_and_keeps_head_and_files(sandbox: ContractProject) -> None:
    head = sandbox.start({"a.txt": "1\n"})
    sandbox.write("a.txt", "2\n")
    sandbox.write("b.txt", "b\n")
    sandbox.repo.stage_all(["."])

    sandbox.repo.reset_index()

    assert sandbox.repo.head() == head
    assert not sandbox.repo.has_staged_changes()
    assert (sandbox.root / "a.txt").read_text(encoding="utf-8") == "2\n"
    assert (sandbox.root / "b.txt").exists()


def test_restore_worktree_rebuilds_tracked_files_without_moving_head_or_index(
    sandbox: ContractProject,
) -> None:
    first = sandbox.start({"a.txt": "1\n", "keep/cfg.txt": "cfg1\n"})
    sandbox.write("a.txt", "2\n")
    sandbox.write("later.txt", "l\n")
    sandbox.write("keep/cfg.txt", "cfg2\n")
    latest = sandbox.commit_all("later work")
    repo = sandbox.repo

    repo.restore_worktree(first, [":(exclude)keep", ":(exclude)keep/**"])

    assert repo.head() == latest
    assert (sandbox.root / "a.txt").read_text(encoding="utf-8") == "1\n"
    assert not (sandbox.root / "later.txt").exists()
    assert (sandbox.root / "keep/cfg.txt").read_text(encoding="utf-8") == "cfg2\n"
    assert not repo.has_staged_changes()

    repo.restore_worktree(first)
    assert (sandbox.root / "keep/cfg.txt").read_text(encoding="utf-8") == "cfg1\n"


def test_restore_of_an_unknown_revision_is_a_command_error(sandbox: ContractProject) -> None:
    sandbox.start({"a.txt": "1\n"})
    with pytest.raises(GitCommandError):
        sandbox.repo.restore_worktree("1" * 40)


def test_clean_removes_untracked_files_but_not_ignored_or_protected_ones(
    sandbox: ContractProject,
) -> None:
    sandbox.start({"a.txt": "1\n"})
    repo = sandbox.repo
    repo.add_excludes(["*.log"])
    sandbox.write("stray.txt", "s\n")
    sandbox.write("nested/stray.txt", "s\n")
    sandbox.write("noise.log", "n\n")
    sandbox.write("cfg/settings.txt", "c\n")

    assert repo.clean_untracked(include_ignored=False, protect="cfg/")

    assert not (sandbox.root / "stray.txt").exists()
    assert not (sandbox.root / "nested").exists()
    assert (sandbox.root / "noise.log").exists()
    assert (sandbox.root / "cfg/settings.txt").exists()
    assert (sandbox.root / "a.txt").exists()

    assert repo.clean_untracked(include_ignored=True, protect="cfg/")
    assert not (sandbox.root / "noise.log").exists()
    assert (sandbox.root / "cfg/settings.txt").exists()


def test_clean_leaves_the_files_named_in_keep_even_when_ignored(sandbox: ContractProject) -> None:
    sandbox.start({"a.txt": "1\n"})
    sandbox.repo.add_excludes(["*.log"])
    sandbox.write("memory/plan.md", "plan\n")
    sandbox.write("notes.txt", "n\n")
    sandbox.write("memory.log", "m\n")
    sandbox.write("stray.txt", "s\n")
    sandbox.write("sub/stray.txt", "s\n")

    assert sandbox.repo.clean_untracked(
        include_ignored=True, protect="cfg/", keep=["/memory", "/notes.txt"]
    )

    assert (sandbox.root / "memory/plan.md").exists()
    assert (sandbox.root / "notes.txt").exists()
    assert not (sandbox.root / "memory.log").exists()
    assert not (sandbox.root / "stray.txt").exists()
    assert not (sandbox.root / "sub").exists()


def test_unversioned_paths_lists_untracked_files_and_ignored_ones_only_on_request(
    sandbox: ContractProject,
) -> None:
    sandbox.start({"a.txt": "1\n", "src/b.txt": "1\n"})
    sandbox.repo.add_excludes(["*.log", ".venv/"])
    sandbox.write("stray.txt", "s\n")
    sandbox.write("src/new.txt", "n\n")
    sandbox.write("deep/er/more.txt", "m\n")
    sandbox.write("noise.log", "n\n")
    sandbox.write(".venv/lib/pkg.py", "p\n")
    sandbox.write("a.txt", "changed\n")

    assert sandbox.repo.unversioned_paths(include_ignored=False) == (
        "deep/er/more.txt",
        "src/new.txt",
        "stray.txt",
    )
    assert sandbox.repo.unversioned_paths(include_ignored=True) == (
        ".venv/lib/pkg.py",
        "deep/er/more.txt",
        "noise.log",
        "src/new.txt",
        "stray.txt",
    )


def test_restore_then_clean_yields_exactly_the_revision(sandbox: ContractProject) -> None:
    first = sandbox.start({"a.txt": "1\n", "dir/b.txt": "1\n"})
    sandbox.write("a.txt", "2\n")
    sandbox.write("dir/c.txt", "c\n")
    sandbox.commit_all("grow")
    sandbox.write("debris.txt", "d\n")
    repo = sandbox.repo

    repo.reset_index()
    repo.clean_untracked(include_ignored=True, protect="protected-dir/")
    repo.restore_worktree(first)

    assert repo.worktree_matches(first, ["."], include_ignored=True)


def test_linked_worktrees_share_history_and_have_their_own_head(
    sandbox: ContractProject, tmp_path: Path
) -> None:
    base = sandbox.start({"a.txt": "1\n"})
    destination = tmp_path / "linked" / "candidate"
    destination.parent.mkdir()
    repo = sandbox.repo

    repo.add_worktree(destination, base)

    assert (destination / "a.txt").read_text(encoding="utf-8") == "1\n"
    assert repo.worktree_head(destination) == base

    linked = sandbox.factory(destination)
    (destination / "a.txt").write_text("edited in the candidate\n", encoding="utf-8")
    linked.stage_all(["."])
    linked.commit("candidate work")
    candidate = repo.worktree_head(destination)

    assert candidate != base
    assert repo.head() == base
    assert repo.resolve_commit(candidate) == candidate
    assert repo.is_ancestor(base, candidate)
    assert (sandbox.root / "a.txt").read_text(encoding="utf-8") == "1\n"


def test_a_removed_worktree_can_be_added_again_at_the_same_path(
    sandbox: ContractProject, tmp_path: Path
) -> None:
    base = sandbox.start({"a.txt": "1\n"})
    destination = tmp_path / "linked"
    repo = sandbox.repo
    repo.add_worktree(destination, base)

    repo.remove_worktree(destination)
    shutil.rmtree(destination, ignore_errors=True)
    repo.prune_worktrees()

    repo.add_worktree(destination, base)
    assert repo.worktree_head(destination) == base


def test_removal_and_pruning_tolerate_unknown_worktrees(
    sandbox: ContractProject, tmp_path: Path
) -> None:
    sandbox.start({"a.txt": "1\n"})

    sandbox.repo.remove_worktree(tmp_path / "never-added")
    sandbox.repo.prune_worktrees()


def test_adding_a_worktree_at_a_missing_commit_is_a_command_error(
    sandbox: ContractProject, tmp_path: Path
) -> None:
    sandbox.start({"a.txt": "1\n"})

    with pytest.raises(GitCommandError):
        sandbox.repo.add_worktree(tmp_path / "linked", "1" * 40)
    assert not (tmp_path / "linked" / "a.txt").exists()


def test_worktree_head_of_a_plain_directory_is_a_command_error(
    sandbox: ContractProject, tmp_path: Path
) -> None:
    sandbox.start({"a.txt": "1\n"})
    plain = tmp_path / "plain"
    plain.mkdir()

    with pytest.raises(GitCommandError):
        sandbox.repo.worktree_head(plain)


def test_a_linked_worktree_location_comes_from_the_parent_registration(
    sandbox: ContractProject, tmp_path: Path
) -> None:
    base = sandbox.start({"a.txt": "1\n"})
    destination = tmp_path / "linked"
    sandbox.repo.add_worktree(destination, base)

    location = sandbox.repo.linked_worktree_location(destination)
    linked = sandbox.factory(destination)
    linked.pin(location)
    (destination / "a.txt").write_text("edited\n", encoding="utf-8")
    linked.stage_all(["."])
    linked.commit("candidate work")

    assert location.work_tree == destination.resolve()
    assert linked.head() == sandbox.repo.worktree_head(destination) != base


def test_the_location_of_a_directory_that_is_no_linked_worktree_is_a_command_error(
    sandbox: ContractProject, tmp_path: Path
) -> None:
    sandbox.start({"a.txt": "1\n"})
    plain = tmp_path / "plain"
    plain.mkdir()

    with pytest.raises(GitCommandError):
        sandbox.repo.linked_worktree_location(plain)
    with pytest.raises(GitCommandError):
        sandbox.repo.linked_worktree_location(sandbox.root)


def _lose_registration(repo: GitRepository, destination: Path, backup: Path) -> None:
    """What a rollback does: Git drops the worktree, then a backup restores its directory."""
    shutil.copytree(destination, backup, symlinks=True)
    repo.remove_worktree(destination)
    shutil.rmtree(destination, ignore_errors=True)
    repo.prune_worktrees()
    shutil.move(backup, destination)


def _files(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != ".git"
    }


_NAMES = st.sampled_from(["a.txt", "b.txt", "dir/c.txt", "dir/deep/d.txt", "new.txt"])


@settings(
    max_examples=examples(pr=6, nightly=30),
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    edits=st.dictionaries(_NAMES, st.one_of(st.none(), st.text(max_size=8)), max_size=5),
    committed=st.booleans(),
)
def test_a_reattached_worktree_keeps_every_file_and_moves_head_to_the_commit(
    sandbox: ContractProject,
    tmp_path_factory: pytest.TempPathFactory,
    *,
    edits: dict[str, str | None],
    committed: bool,
) -> None:
    # One fresh project per example: the fixtures are shared across examples.
    sandbox = ContractProject(root=tmp_path_factory.mktemp("project"), factory=sandbox.factory)
    scratch = tmp_path_factory.mktemp("linked")
    base = sandbox.start({"a.txt": "1\n", "dir/c.txt": "c\n"})
    destination = scratch / "workspace"
    repo = sandbox.repo
    repo.add_worktree(destination, base)
    head = base
    if committed:
        (destination / "a.txt").write_text("committed\n", encoding="utf-8")
        linked = sandbox.factory(destination)
        linked.pin(repo.linked_worktree_location(destination))
        linked.stage_all(["."])
        linked.commit("candidate work")
        head = repo.worktree_head(destination)
    for name, content in edits.items():
        path = destination / name
        if content is None:
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
    before = _files(destination)
    _lose_registration(repo, destination, scratch / "backup")
    with pytest.raises(GitCommandError):
        repo.linked_worktree_location(destination)

    repo.reattach_worktree(destination, head)

    assert _files(destination) == before
    assert repo.worktree_head(destination) == head
    linked = sandbox.factory(destination)
    linked.pin(repo.linked_worktree_location(destination))
    assert not linked.has_staged_changes()
    linked.stage_all(["."])
    linked.commit("after reattach", allow_empty=True)
    assert repo.is_ancestor(head, repo.worktree_head(destination))


def test_reattaching_refuses_a_registered_worktree_a_missing_commit_and_a_plain_path(
    sandbox: ContractProject, tmp_path: Path
) -> None:
    base = sandbox.start({"a.txt": "1\n"})
    destination = tmp_path / "linked"
    repo = sandbox.repo
    repo.add_worktree(destination, base)

    with pytest.raises(GitCommandError):
        repo.reattach_worktree(destination, base)
    _lose_registration(repo, destination, tmp_path / "backup")
    with pytest.raises(GitCommandError):
        repo.reattach_worktree(destination, "1" * 40)
    with pytest.raises(GitCommandError):
        repo.reattach_worktree(tmp_path / "missing", base)

    repo.reattach_worktree(destination, base)
    assert repo.worktree_head(destination) == base
