"""Shell path resolution needs ancestor metadata without access to sibling contents."""

from __future__ import annotations

import json
import re
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from vs_sandbox.api import ProjectPathPolicy, SeatbeltSandbox

type _Operation = Literal["file-read-metadata", "file-read-data", "file-write-data"]
_PATH = r'\((literal|subpath)\s+("(?:[^"\\]|\\.)*")\)'
_RULE = re.compile(
    r"\((allow|deny)\s+((?:file-[a-z*-]+\s*)+)"
    rf"((?:{_PATH}\s*)*)\)"
)
_NAME = st.from_regex(r"[a-z][a-z0-9-]{0,20}", fullmatch=True)


class _FakeSeatbeltFilesystem:
    """Interpret emitted filesystem rules, in order, without launching a process.

    Only literal/subpath file rules are modeled; process and network rules have
    no bearing on these filesystem operations. Unmatched operations fail closed.
    """

    def __init__(self, profile: str) -> None:
        self._rules = tuple(_RULE.finditer(profile))

    def permits(self, operation: _Operation, path: Path) -> bool:
        path = path.resolve()
        allowed = False
        for rule in self._rules:
            action, operations, paths = rule.group(1, 2, 3)
            if not any(
                operation.startswith(op[:-1]) if op.endswith("*") else operation == op
                for op in operations.split()
            ):
                continue
            predicates = re.findall(_PATH, paths)
            if not predicates or any(
                path == Path(json.loads(value))
                or (kind == "subpath" and path.is_relative_to(Path(json.loads(value))))
                for kind, value in predicates
            ):
                allowed = action == "allow"
        return allowed

    def enter(self, directory: Path) -> None:
        # The macOS POSIX shell stats every ancestor while resolving cd.
        for ancestor in (directory, *directory.parents):
            if not self.permits("file-read-metadata", ancestor):
                raise NotADirectoryError(directory)


@pytest.mark.regression
@given(attempt=_NAME, task=_NAME)
@example(attempt="m-attempt-profiler", task="flint-m3pro")
def test_shell_can_enter_task_without_reading_siblings_or_writing_task_inputs(
    attempt: str, task: str
) -> None:
    with TemporaryDirectory() as directory:
        workspace = Path(directory).resolve() / "worktrees" / attempt / "workspace"
        task_root = workspace / "task-inputs"
        task_directory = task_root / task
        task_directory.mkdir(parents=True)
        hidden = workspace / "run-record"
        hidden.mkdir()
        confinement = SeatbeltSandbox(
            workspace=workspace,
            sandbox_exec_path="/usr/bin/sandbox-exec",
            system_read_roots=(),
            project_path_policy=ProjectPathPolicy(
                read_only_paths=(Path("task-inputs"),), hidden_paths=(Path("run-record"),)
            ),
        )
        argv = confinement.wrap(["/bin/sh", "-c", f"cd task-inputs/{task}"])
        filesystem = _FakeSeatbeltFilesystem(argv[2])

        filesystem.enter(task_directory)

        assert filesystem.permits("file-read-data", task_directory / "setup.sh")
        assert not filesystem.permits("file-write-data", task_directory / "setup.sh")
        for ancestor in (workspace.parent, workspace.parent.parent):
            assert filesystem.permits("file-read-metadata", ancestor)
            assert not filesystem.permits("file-read-data", ancestor)
            assert not filesystem.permits("file-write-data", ancestor)
            for operation in ("file-read-metadata", "file-read-data", "file-write-data"):
                assert not filesystem.permits(operation, ancestor / "sibling")
        for operation in ("file-read-metadata", "file-read-data", "file-write-data"):
            assert not filesystem.permits(operation, hidden)
            assert not filesystem.permits(operation, confinement.operator_root)


def test_operator_root_denial_overrides_ancestor_metadata(tmp_path: Path) -> None:
    workspace = tmp_path.resolve() / "attempt" / "workspace"
    workspace.mkdir(parents=True)
    confinement = SeatbeltSandbox(
        workspace=workspace,
        sandbox_exec_path="/usr/bin/sandbox-exec",
        operator_root=workspace.parent,
    )
    filesystem = _FakeSeatbeltFilesystem(confinement.profile())

    assert not filesystem.permits("file-read-metadata", workspace.parent)
