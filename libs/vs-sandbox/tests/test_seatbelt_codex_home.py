"""Seatbelt grants the parent of a Codex file leaf, and denies named operator entries.

Bubblewrap makes that parent an empty writable directory, so a CLI can create its
state database beside ``auth.json`` without seeing a sibling checkout. Seatbelt
has no overlay: the real parent has to be writable. The profile is generated
again on the next turn, so a child the CLI created after the first profile
(its state database, logs, installation id, skill tree) stays writable.
``config.toml`` and ``worktrees`` stay write-denied. The home directory itself
is never opened up.
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from vs_sandbox.api import SeatbeltSandbox
from vs_sim.api.testing import examples

#: Operator-owned names the profile keeps write-denied inside a file-leaf parent.
_PROTECTED = frozenset({"config.toml", "worktrees"})
#: Leaves the grant itself names, so a regenerated profile does not deny them.
_GRANTED = frozenset({"auth.json", "sessions"})
_CHILD_NAME = st.from_regex(r"[A-Za-z0-9][A-Za-z0-9._-]{0,24}", fullmatch=True)
_KIND = st.sampled_from(("file", "directory"))
type _Kind = Literal["file", "directory"]


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


def _deny(path: Path, *, directory: bool) -> str:
    kind = "subpath" if directory else "literal"
    return f"(deny file-write* ({kind} {_quoted(path.resolve())}))"


def _codex_home(tmp_path: Path) -> tuple[Path, Path, Path]:
    state = tmp_path / "agent-homes" / "codex"
    state.mkdir(parents=True)
    (state / "auth.json").write_text("{}\n", encoding="utf-8")
    (state / "sessions").mkdir()
    (state / "config.toml").write_text('model = "x"\n', encoding="utf-8")
    (state / "worktrees").mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return state, state / "auth.json", state / "sessions"


@pytest.mark.regression
@settings(max_examples=examples(pr=20, nightly=100), deadline=None)
@given(name=_CHILD_NAME, kind=_KIND)
# Codex writes these beside auth.json on the first turn and rewrites them on resume.
@example("state_5.sqlite", "file")
@example("state_5.sqlite-wal", "file")
@example("state_5.sqlite-shm", "file")
@example("log", "directory")
@example("installation_id", "file")
@example(".tmp", "directory")
@example("skills", "directory")
@example("config.toml", "file")
@example("worktrees", "directory")
def test_a_regenerated_profile_keeps_new_codex_state_writable_and_denies_protected_entries(
    name: str, kind: _Kind
) -> None:
    """A child created after the first profile stays writable; operator entries do not.

    The profile is rebuilt for every turn. Denying every ungranted child that
    exists at generation time makes the files Codex created on turn 1
    (``state_5.sqlite`` and its journals, ``log``, ``installation_id``,
    ``.tmp``, ``skills``) unwritable on resume.
    """
    with TemporaryDirectory() as directory:
        state, auth, sessions = _codex_home(Path(directory))
        workspace = state.parent.parent / "workspace"
        first = _rules(_profile(workspace, auth, sessions))
        created = state / name
        fresh = name not in _PROTECTED and name not in _GRANTED
        if fresh:
            if kind == "directory":
                created.mkdir()
            else:
                created.write_text("state\n", encoding="utf-8")
            assert _deny(created, directory=kind == "directory") not in first

        second = _rules(_profile(workspace, auth, sessions))

        assert f"(subpath {_quoted(state.resolve())})" in second
        assert f"(subpath {_quoted(Path.home().resolve())})" not in second
        assert _deny(state / "config.toml", directory=False) in second
        assert _deny(state / "worktrees", directory=True) in second
        assert _deny(auth, directory=False) not in second
        assert _deny(sessions, directory=True) not in second
        if fresh:
            assert _deny(created, directory=kind == "directory") not in second
