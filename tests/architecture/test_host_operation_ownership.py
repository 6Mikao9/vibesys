"""Shared host reads and failures survive removal of the helper adapter."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("name", "owner", "retired_owner"),
    [
        ("ProjectLookupError", "server/host_examples.py", "server/host_helper_requests.py"),
        ("ComputeError", "server/host_compute.py", "server/host_helper_compute.py"),
        ("HostReadSource", "server/host_operation_reads.py", "server/host_helper_requests.py"),
    ],
)
def test_shared_host_behavior_has_a_native_owner(name: str, owner: str, retired_owner: str) -> None:
    native = ast.parse((ROOT / "src" / owner).read_text())
    retired = ROOT / "src" / retired_owner
    legacy = ast.parse(retired.read_text() if retired.exists() else "")
    assert any(isinstance(node, ast.ClassDef) and node.name == name for node in native.body)
    assert not any(
        isinstance(node, ast.ClassDef) and node.name in {name, "HelperReadSource"}
        for node in legacy.body
    )


def test_native_read_adapter_does_not_depend_on_the_helper() -> None:
    tree = ast.parse((ROOT / "src/server/host_operation_reads.py").read_text())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "import_module"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            imported.add(node.args[0].value)
    assert not {
        module
        for module in imported
        if module.startswith(("server.host_helper", "entrypoints.host_helper"))
    }
