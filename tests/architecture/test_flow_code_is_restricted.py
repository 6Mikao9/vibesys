"""Flow code reaches the world only through ``ctx``: no clock, randomness, threads, I/O, or blind except."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BANNED_MODULES = frozenset(
    {"asyncio", "time", "random", "threading", "subprocess", "os", "socket", "vs_sim"}
)
FLOW_GLOBS = ("src/vibesys/orchestration/*/flow/**/*.py", "src/vibesys/candidates/flows.py")
SEARCH_API = "vs_core.api.search"


def _parsed(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imports_flow(tree: ast.Module) -> bool:
    return any(
        isinstance(node, ast.ImportFrom)
        and node.module == SEARCH_API
        and any(alias.name == "Flow" for alias in node.names)
        for node in ast.walk(tree)
    )


def flow_modules() -> list[Path]:
    """Modules that import ``Flow`` plus the explicit flow packages."""
    found = {path for pattern in FLOW_GLOBS for path in ROOT.glob(pattern)}
    for path in (ROOT / "src").rglob("*.py"):
        if _imports_flow(_parsed(path)):
            found.add(path)
    return sorted(found)


def violations(tree: ast.Module) -> list[str]:
    """What a flow module does that flow code may not: banned imports and blind ``except``."""
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            names = []
        found += [
            f"line {node.lineno}: imports {name}"
            for name in names
            if name.split(".")[0] in BANNED_MODULES
        ]
        if isinstance(node, ast.ExceptHandler) and (
            node.type is None
            or (isinstance(node.type, ast.Name) and node.type.id in {"Exception", "BaseException"})
        ):
            found.append(f"line {node.lineno}: blind except")
    return found


@pytest.mark.parametrize("path", flow_modules(), ids=lambda path: str(path.relative_to(ROOT)))
def test_flow_modules_use_only_ctx_for_the_outside_world(path: Path) -> None:
    assert violations(_parsed(path)) == []


@pytest.mark.parametrize(
    "source",
    [
        "import asyncio",
        "from time import sleep",
        "import os.path",
        "try:\n  pass\nexcept:\n  pass",
        "try:\n  pass\nexcept Exception:\n  pass",
    ],
)
def test_the_scan_catches_what_it_bans(source: str) -> None:
    assert violations(ast.parse(source)) != []


def test_the_scan_allows_typed_except() -> None:
    assert violations(ast.parse("try:\n  pass\nexcept ValueError:\n  pass")) == []
