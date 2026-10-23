"""The system has one comparison: vs-core's judgement of an objective and its dominance rule.

Whether a reading is better, worse or within noise is decided by ``vs_core.api.judge`` per
objective and ``vs_core.api.dominance`` over them. Outside vs-core, one module projects it onto
what the product holds (`vibesys.metrics`, which turns rows and the task's declared noise into
what the judgement takes); nothing else calls either, so no second statistic, tolerance or
dominance can grow beside it. The old copies, ``MetricComparison`` and ``compare_metrics``, stay
gone for the same reason.

Enforced by an AST scan of the product (``src/``) and every library but vs-core: a call to
``judge`` or ``dominance``, or an import of them from the core, outside the one projection fails.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from tests.support.parsed_source import STATIC_SCAN_GROUP, parse_file, python_files, walk_nodes

pytestmark = pytest.mark.xdist_group(STATIC_SCAN_GROUP)

REPO = Path(__file__).resolve().parents[2]
PROJECTION = "src/vibesys/metrics.py"
"""The one module outside vs-core that may call the judgement."""
THE_RULE = frozenset({"judge", "dominance"})
GONE = frozenset({"MetricComparison", "compare_metrics", "objective_comparisons"})
"""Names of the product's former comparison, which must not come back."""


def _product_files() -> list[Path]:
    roots = [REPO / "src", *sorted((REPO / "libs").glob("*/src"))]
    return [
        path
        for root in roots
        if root.parent.name != "vs-core"
        for path in python_files(root)
        if "_generated" not in path.parts and "_core_values" not in path.parts
    ]


def _uses_of_the_rule(tree: ast.AST) -> list[tuple[int, str]]:
    """Where ``tree`` imports the judgement from vs-core, or reaches it through a core alias.

    ``judge`` is also the name of a review role, so only what comes from vs-core counts.
    """
    found: list[tuple[int, str]] = []
    aliases: set[str] = set()
    for node in walk_nodes(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "vs_core":
            found.extend((node.lineno, a.name) for a in node.names if a.name in THE_RULE)
        elif isinstance(node, ast.Import):
            aliases.update(
                a.asname or a.name for a in node.names if a.name.split(".")[0] == "vs_core"
            )
    found.extend(
        (node.lineno, node.attr)
        for node in walk_nodes(tree)
        if isinstance(node, ast.Attribute)
        and node.attr in THE_RULE
        and isinstance(node.value, ast.Name)
        and node.value.id in aliases
    )
    return found


def _declared_or_named(tree: ast.AST) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for node in walk_nodes(tree):
        if isinstance(node, ast.ClassDef | ast.FunctionDef) and node.name in GONE:
            found.append((node.lineno, node.name))
        elif isinstance(node, ast.Name) and node.id in GONE:
            found.append((node.lineno, node.id))
        elif isinstance(node, ast.Attribute) and node.attr in GONE:
            found.append((node.lineno, node.attr))
        elif isinstance(node, ast.alias) and node.name in GONE:
            found.append((getattr(node, "lineno", 0), node.name))
    return found


def test_only_the_projection_outside_vs_core_calls_the_judgement() -> None:
    offenders = [
        f"{path.relative_to(REPO)}:{line}: {name}"
        for path in _product_files()
        if path.relative_to(REPO).as_posix() != PROJECTION
        for line, name in _uses_of_the_rule(parse_file(path))
    ]
    assert not offenders, (
        "decide better, worse or within noise through MetricSpace.compare (it is the one "
        "projection of vs-core's rule), never by judging readings here: " + "; ".join(offenders)
    )


def test_the_projection_does_call_the_judgement() -> None:
    """The scan is not vacuous: the one allowed site exists and uses both halves of the rule."""
    called = {name for _, name in _uses_of_the_rule(parse_file(REPO / PROJECTION))}
    assert called == THE_RULE


def test_the_product_comparison_the_core_replaced_is_not_defined_or_used_again() -> None:
    offenders = [
        f"{path.relative_to(REPO)}:{line}: {name}"
        for path in _product_files()
        for line, name in _declared_or_named(parse_file(path))
    ]
    assert not offenders, "; ".join(offenders)
