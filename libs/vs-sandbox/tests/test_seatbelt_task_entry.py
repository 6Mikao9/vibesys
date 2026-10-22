"""Shell path resolution needs ancestor metadata without access to sibling contents."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from vs_sandbox.api import (
    ProjectPathPolicy,
    SandboxUnavailableError,
    SeatbeltSandbox,
    build_host_sandbox,
)
from vs_sim.api import ProbeResult
from vs_sim.api.testing import ScriptedProbe

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


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS builder owns Darwin directories")
@pytest.mark.regression
def test_metal_and_temp_writes_are_redirected_without_granting_shared_caches(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "attempts" / "one" / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "run-record").mkdir()
    # Use a synthetic host tree outside the policy's existing /tmp write grants.
    roots = (tmp_path / "darwin" / "T", tmp_path / "darwin" / "C")
    for root in roots:
        root.mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "sandbox-exec").write_text("#!/bin/sh\n")
    (bin_dir / "sandbox-exec").chmod(0o755)

    def script(argv: tuple[str, ...]) -> ProbeResult:
        index = ("DARWIN_USER_TEMP_DIR", "DARWIN_USER_CACHE_DIR").index(argv[1])
        return ProbeResult(0, str(roots[index]))

    def build(target: Path) -> SeatbeltSandbox:
        confinement = build_host_sandbox(
            target,
            env={
                "PATH": str(bin_dir),
                "TMPDIR": str(roots[0]),
                "DIRHELPER_USER_DIR_SUFFIX": "inherited-shared",
            },
            probe=ScriptedProbe(script),
            project_path_policy=ProjectPathPolicy(hidden_paths=(Path("run-record"),)),
            require_enforcement=True,
        )
        assert isinstance(confinement, SeatbeltSandbox)
        return confinement

    confinement = build(workspace)
    suffix = confinement.env["DIRHELPER_USER_DIR_SUFFIX"]
    assert suffix != "inherited-shared"
    assert "/" not in suffix
    assert confinement.env["TMPDIR"] == str(roots[0].resolve() / suffix) + "/"
    assert all(confinement.env[name] == confinement.env["TMPDIR"] for name in ("TMP", "TEMP"))
    profile = confinement.wrap(["true"])[2]
    # The synthetic roots happen to live in the test host's /tmp. Remove only
    # those pre-existing scratch grants to observe the new rule in isolation.
    profile = profile.replace('    (subpath "/private/tmp")\n', "")
    profile = profile.replace('    (subpath "/private/var/tmp")\n', "")
    filesystem = _FakeSeatbeltFilesystem(profile)
    for root in roots:
        private = root.resolve() / suffix
        assert private.is_dir()
        assert private.stat().st_mode & 0o777 == 0o700
        assert filesystem.permits("file-write-data", private / "scratch")
        assert not filesystem.permits("file-write-data", root / "other-session" / "scratch")
        for cache in ("com.apple.metalfe", "com.apple.metal", "com.apple.gpuarchiver"):
            assert filesystem.permits("file-write-data", private / cache / "module.pcm")
            assert not filesystem.permits("file-write-data", root / cache / "module.pcm")
    assert not filesystem.permits("file-write-data", workspace / "run-record" / "state")
    assert '(extension-class "com.apple.app-sandbox.read-write")' in profile
    assert "(allow mach-lookup)" in profile
    assert "(allow iokit-open)" in profile
    other = tmp_path / "other" / "workspace"
    other.mkdir(parents=True)
    (other / "run-record").mkdir()
    assert build(other).env["DIRHELPER_USER_DIR_SUFFIX"] != suffix
    assert build(workspace).env["DIRHELPER_USER_DIR_SUFFIX"] == suffix
    assert not filesystem.permits("file-write-data", tmp_path / "trusted-cache" / "uv" / "entry")
    private_cache = roots[1] / suffix
    private_cache.rmdir()
    private_cache.symlink_to(workspace, target_is_directory=True)
    with pytest.raises(SandboxUnavailableError, match="private Darwin directory is a symlink"):
        build(workspace)


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS builder owns Darwin directories")
@pytest.mark.parametrize(
    "answer", [None, ProbeResult(1, ""), ProbeResult(0, "relative"), ProbeResult(0, "/")]
)
def test_missing_darwin_directory_fails_closed(tmp_path: Path, answer: ProbeResult | None) -> None:
    with pytest.raises(SandboxUnavailableError, match="DARWIN_USER_TEMP_DIR"):
        build_host_sandbox(
            tmp_path, env={}, probe=ScriptedProbe(lambda _: answer), require_enforcement=True
        )
