"""A strategy decides on a candidate only from evidence that judged it (`judged_keys`).

A failed gate of a measurement the machinery or the host spoiled (`MeasurementFailure` that does
not charge the candidate) says the gate did not pass, not that the candidate failed it. Reading
that record as the candidate's verdict rejected, and lost, a candidate nothing was proven
against. `vs_core.api.judged_keys` is the one owner of which records of a result are a verdict;
`trusted_keys` is the same list before that rule, for the one reader that carries the blame
beside the failed record: the input's measurement. This check fails when any other product code
reads `trusted_keys`, so a new strategy cannot decide a candidate on a record that judged
nothing without the rule saying why.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_READS_THE_BLAME_BESIDE_THE_RECORD = frozenset(
    {
        "src/vibesys/candidates/_input.py",
        "libs/vs-core/src/vs_core/_evidence_views.py",
    }
)


def _product_files() -> list[Path]:
    roots = [ROOT / "src", *sorted((ROOT / "libs").glob("*/src"))]
    return [path for root in roots for path in root.rglob("*.py")]


def _reads_trusted_keys(path: Path) -> bool:
    tree = ast.parse(path.read_text())
    return any(
        isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id == "trusted_keys")
            or (isinstance(node.func, ast.Attribute) and node.func.attr == "trusted_keys")
        )
        for node in ast.walk(tree)
    )


def test_only_the_inputs_measurement_reads_the_evidence_a_spoiled_measurement_left() -> None:
    readers = {
        path.relative_to(ROOT).as_posix() for path in _product_files() if _reads_trusted_keys(path)
    }

    assert readers <= _READS_THE_BLAME_BESIDE_THE_RECORD, sorted(
        readers - _READS_THE_BLAME_BESIDE_THE_RECORD
    )
