"""Searches reach vs-core through ``vs_core.api.search`` and nothing else.

``vs_core.api.search`` holds the search contract: the requests a search puts in a ``Wait``,
the events it receives, ``Within`` and the watches, ``Failed``, ``RunView``, ``Children``.
Request records, ``Outbound``/``Until``, event translation and the shell's host types stay in
``vs_core.api``. A search that imports them reaches past its contract, so code under
``src/vibesys/orchestration`` may import ``vs_core`` only as ``vs_core.api.search``.

Searches not yet ported to the new contract are listed in ``UNPORTED``. The list only
shrinks: porting a directory removes its entry, and an entry whose directory no longer
breaks the rule fails the test until it is removed.
"""

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ROOT = REPO / "src/vibesys/orchestration"
FACADE = "vs_core.api.search"

UNPORTED = frozenset(
    {
        "dynamic",
        "dynamic/core_policy",
        "dynamic/strategy",
        "evolve/core_policy",
        "evolve/population",
        "evolve/strategy",
        "issue_queue",
        "issue_queue/core_policy",
        "issue_queue/strategy",
        "multi/core_policy",
        "multi/strategy",
        "single/core_policy",
        "single/strategy",
    }
)


def foreign_vs_core_imports(source: str) -> list[int]:
    """Line numbers of imports of ``vs_core`` that are not ``vs_core.api.search``."""
    lines: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            is_core = module == "vs_core" or module.startswith("vs_core.")
            if node.level == 0 and is_core and module != FACADE:
                lines.append(node.lineno)
        elif isinstance(node, ast.Import):
            lines += [
                node.lineno
                for alias in node.names
                if (alias.name == "vs_core" or alias.name.startswith("vs_core."))
                and alias.name != FACADE
            ]
    return lines


def _directory(path: Path) -> str:
    return path.parent.relative_to(ROOT).as_posix()


def test_the_checker_flags_every_other_way_into_vs_core() -> None:
    assert foreign_vs_core_imports("from vs_core.api import Wait") == [1]
    assert foreign_vs_core_imports("import vs_core.api as core") == [1]
    assert foreign_vs_core_imports("from vs_core.types.until import Within") == [1]
    assert foreign_vs_core_imports("import vs_core") == [1]
    assert foreign_vs_core_imports("from vs_core.api.search import Wait, Within") == []
    assert foreign_vs_core_imports("from vs_core_other import x\nfrom vibesys import y") == []


def test_orchestration_imports_vs_core_only_through_the_search_facade() -> None:
    offenders = {
        f"{path.relative_to(REPO)}:{line}"
        for path in sorted(ROOT.rglob("*.py"))
        if _directory(path) not in UNPORTED
        for line in foreign_vs_core_imports(path.read_text())
    }
    assert not offenders, "import from vs_core.api.search instead:\n" + "\n".join(sorted(offenders))


def test_the_unported_list_only_names_directories_that_still_break_the_rule() -> None:
    breaking = {
        _directory(path) for path in ROOT.rglob("*.py") if foreign_vs_core_imports(path.read_text())
    }
    assert breaking >= UNPORTED, f"remove from UNPORTED: {sorted(UNPORTED - breaking)}"
