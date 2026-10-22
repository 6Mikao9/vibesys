"""Seatbelt grants the parent of a Codex file leaf, and denies the siblings already there.

Bubblewrap makes that parent an empty writable directory, so a CLI can create its
state database beside ``auth.json`` without seeing a sibling checkout. Seatbelt
has no overlay: the real parent has to be writable, and every child that already
exists and was not granted stays write-denied. The home directory itself is never
opened up.
"""

from __future__ import annotations

from pathlib import Path

from vs_sandbox.host_sandbox import SeatbeltSandbox


def _profile(workspace: Path, *write_paths: Path) -> str:
    return SeatbeltSandbox(
        workspace=workspace,
        write_paths=write_paths,
        sandbox_exec_path="/usr/bin/sandbox-exec",
    ).profile()


def _quoted(path: Path) -> str:
    return f'"{path}"'


def _rules(text: str) -> set[str]:
    return {line.strip() for line in text.splitlines()}


def test_a_file_leaf_opens_its_parent_and_denies_existing_siblings(tmp_path: Path) -> None:
    state = tmp_path / "agent-homes" / "codex"
    state.mkdir(parents=True)
    auth = state / "auth.json"
    auth.write_text("{}\n", encoding="utf-8")
    sessions = state / "sessions"
    sessions.mkdir()
    worktrees = state / "worktrees"
    worktrees.mkdir()
    config = state / "config.toml"
    config.write_text('model = "x"\n', encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    rules = _rules(_profile(workspace, auth, sessions))

    assert f"(subpath {_quoted(state.resolve())})" in rules
    assert f"(deny file-write* (subpath {_quoted(worktrees.resolve())}))" in rules
    assert f"(deny file-write* (literal {_quoted(config.resolve())}))" in rules
    assert f"(subpath {_quoted(Path.home().resolve())})" not in rules
    assert f"(deny file-write* (subpath {_quoted(sessions.resolve())}))" not in rules
    assert f"(deny file-write* (literal {_quoted(auth.resolve())}))" not in rules


def test_an_auth_symlink_is_writable_without_its_real_directory(tmp_path: Path) -> None:
    login = tmp_path / "login" / "auth.json"
    login.parent.mkdir()
    login.write_text("{}\n", encoding="utf-8")
    state = tmp_path / "agent-homes" / "codex"
    state.mkdir(parents=True)
    link = state / "auth.json"
    link.symlink_to(login)
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    rules = _rules(_profile(workspace, link))

    assert f"(subpath {_quoted(state.resolve())})" in rules
    assert f"(subpath {_quoted(login.resolve())})" in rules
    assert f"(subpath {_quoted(login.parent.resolve())})" not in rules


def test_a_directory_grant_does_not_open_its_parent(tmp_path: Path) -> None:
    config = tmp_path / ".config" / "codex"
    config.mkdir(parents=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    rules = _rules(_profile(workspace, config))

    assert f"(subpath {_quoted(config.parent.resolve())})" not in rules
