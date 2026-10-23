"""A run saved by an older VibeSys resumes: its envelope migrates to the current version.

``tests/envelopes`` holds one saved fake run per supported old version (``v<version>-<case>.json``),
written by the code of that version (a small run built through the kernel's own test lab and
encoded by that version's registry), so a migration is held to what a real old save looks like
and not to what a later model would write. Every one is brought to the current version by the
chain and decoded strictly. The registry's rule is checked here too: every version from the
oldest supported one to the one before the current has a registered migration and a fixture, so
raising ``ENVELOPE_SCHEMA_VERSION`` without them fails (`vs_core._envelope_migrations`).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

import pytest
from hypothesis import given
from hypothesis import strategies as st

import vs_core.api as core
from vs_core.api.testing import FAKE_REGISTRY

from .test_comparison_statistic import VALUES, compared

type Json = dict[str, Json] | list[Json] | str | float | bool | None
"""A JSON document."""

_FIXTURES = Path(__file__).parent / "envelopes"
_SUPPORTED_SINCE = 34
"""The oldest version a run was promised to resume from; lowering support edits this line."""
_REWRITES_SINCE = 42
"""From this version on a compatible shape change keeps the version
(``scripts/check_persisted_schema_compat.py``), so every later migration rewrites data."""
_FILES = sorted(_FIXTURES.glob("v*-*.json"))
_Envelope = core.RunEnvelope[core.FlowState]


def _version(path: Path) -> int:
    return int(path.name.removeprefix("v").split("-", 1)[0])


def _upgraded(source: str) -> dict[str, Json]:
    return json.loads(core.upgrade_envelope(source, ("schema_version",)))


def _comparisons(envelope: dict[str, Json]) -> list[dict[str, Json]]:
    core_state = envelope["core"]
    assert isinstance(core_state, dict)
    evaluation = core_state["evaluation"]
    assert isinstance(evaluation, dict)
    comparisons = evaluation["comparisons"]
    assert isinstance(comparisons, list)
    found = [item for item in comparisons if isinstance(item, dict)]
    assert len(found) == len(comparisons)
    return found


@pytest.mark.parametrize("path", _FILES, ids=lambda path: path.stem)
def test_a_saved_envelope_of_each_supported_version_decodes_at_the_current_version(
    path: Path,
) -> None:
    decoded = FAKE_REGISTRY.decode_envelope(_Envelope, path.read_text(encoding="utf-8"))
    assert decoded.schema_version == core.ENVELOPE_SCHEMA_VERSION
    assert decoded.core.run.run_id == core.initial_state().run.run_id
    assert (
        FAKE_REGISTRY.decode_envelope(_Envelope, FAKE_REGISTRY.encode_envelope(decoded)) == decoded
    )


_OBJECTIVE_KEYS = (
    "metric",
    "unit",
    "direction",
    "candidate_value",
    "base_value",
    "ratio",
    "noise",
)


def _before_version_43(value: Json) -> Json:
    """``value`` with every version 43 reshaping undone: the oracle of what the migration must keep.

    A comparison's one objective returns to the comparison's top (its verdict is the comparison's),
    a sample's or record's single reading returns to ``reading``, and nothing else moves.
    """
    if isinstance(value, list):
        return [_before_version_43(item) for item in value]
    if not isinstance(value, dict):
        return value
    node = {key: _before_version_43(item) for key, item in value.items()}
    objectives = node.get("objectives")
    if isinstance(objectives, list) and "candidate_input" in node:
        node.pop("objectives")
        (objective,) = objectives
        assert isinstance(objective, dict)
        node |= {key: objective[key] for key in _OBJECTIVE_KEYS if key in objective}
    readings = node.pop("readings", None)
    if isinstance(readings, list) and readings:
        (node["reading"],) = readings
    return node


def _object(value: Json) -> dict[str, Json]:
    assert isinstance(value, dict)
    return value


def _headline_verdict(comparison: dict[str, Json]) -> Json:
    objectives = comparison["objectives"]
    assert isinstance(objectives, list)
    (headline,) = objectives
    assert isinstance(headline, dict)
    return headline["verdict"]


def test_a_comparison_with_no_estimate_of_noise_stays_inconclusive_after_the_migration() -> None:
    """The 34 save without a floor held ``within_noise`` of one to three runs a side.

    An earlier migration read it as ``inconclusive`` (it had no estimate of noise); version 43
    keeps that, and the headline objective states the same verdict: past settlements are not
    decided again.
    """
    source = (_FIXTURES / "v34-no-floor.json").read_text(encoding="utf-8")
    before, after = _comparisons(json.loads(source)), _comparisons(_upgraded(source))
    assert {c["verdict"] for c in before} == {"within_noise"}
    assert {c["verdict"] for c in after} == {"inconclusive"}
    assert {c["verdict"] for c in after} == {"inconclusive"}
    assert {_headline_verdict(c) for c in after} == {"inconclusive"}
    assert [{**c, "verdict": "x"} for c in before] == [
        {**_object(_before_version_43(c)), "verdict": "x"} for c in after
    ]


def test_a_measured_within_noise_is_kept_and_nothing_else_of_the_envelope_changes() -> None:
    source = (_FIXTURES / "v34-floor.json").read_text(encoding="utf-8")
    stored = json.loads(source)
    assert {c["verdict"] for c in _comparisons(stored)} == {"within_noise"}
    upgraded = _upgraded(source)
    assert {c["verdict"] for c in _comparisons(upgraded)} == {"within_noise"}
    assert _before_version_43(upgraded) == {
        **stored,
        "schema_version": core.ENVELOPE_SCHEMA_VERSION,
    }


def test_an_envelope_of_the_current_version_is_returned_as_it_is() -> None:
    source = FAKE_REGISTRY.encode_envelope(
        FAKE_REGISTRY.decode_envelope(
            _Envelope, (_FIXTURES / "v34-floor.json").read_text(encoding="utf-8")
        )
    )
    assert core.upgrade_envelope(source, ("schema_version",)) == source


def test_an_envelope_nested_in_a_record_is_migrated_in_place() -> None:
    source = (_FIXTURES / "v34-no-floor.json").read_text(encoding="utf-8")
    record = json.dumps({"schema_version": 1, "envelope": json.loads(source), "wakes": []})
    migrated = json.loads(core.upgrade_envelope(record, ("envelope", "schema_version")))
    assert migrated["wakes"] == []
    assert migrated["envelope"] == _upgraded(source)


@given(
    mine=st.lists(VALUES, min_size=1, max_size=6),
    theirs=st.lists(VALUES, min_size=1, max_size=6),
    floor=st.sampled_from((0.0, 0.01, 0.05)),
    direction=st.sampled_from(("max", "min")),
    decided=st.sampled_from(list(core.ComparisonVerdict)),
)
def test_the_migration_keeps_the_verdict_a_version_42_run_decided_and_states_it_per_objective(
    mine: list[float],
    theirs: list[float],
    floor: float,
    direction: Literal["max", "min"],
    decided: core.ComparisonVerdict,
) -> None:
    """A version 42 comparison judged its headline alone: it becomes that one objective, undecided again by nobody."""
    current = compared(mine, theirs, floor=floor, direction=direction).model_dump(mode="json")
    headline = current.pop("objectives")[0]
    stored = json.loads((_FIXTURES / "v42-floor.json").read_text(encoding="utf-8"))
    flat = {**current, **{key: headline[key] for key in _OBJECTIVE_KEYS}}
    stored["core"]["evaluation"]["comparisons"] = [{**flat, "verdict": decided.value}]
    migrated = _comparisons(_upgraded(json.dumps(stored)))
    assert migrated == [
        {
            **current,
            "verdict": decided.value,
            "objectives": [{**headline, "verdict": decided.value}],
        }
    ]


def test_every_version_from_the_oldest_supported_has_a_migration_and_a_fixture() -> None:
    """Raising ``ENVELOPE_SCHEMA_VERSION`` fails here until the old version has both."""
    assert core.OLDEST_SUPPORTED_ENVELOPE_VERSION <= _SUPPORTED_SINCE
    chain = range(core.OLDEST_SUPPORTED_ENVELOPE_VERSION, core.ENVELOPE_SCHEMA_VERSION)
    missing = [v for v in chain if v not in core.ENVELOPE_MIGRATIONS]
    assert not missing, f"register the migration of envelope version {missing} to the next"
    assert set(core.ENVELOPE_MIGRATIONS) == set(chain), "a migration for a version not in the chain"
    uncovered = [v for v in chain if not any(_version(p) == v for p in _FILES)]
    assert not uncovered, (
        f"save a fixture envelope of version {uncovered} with the code of that version "
        f"(tests/envelopes/v<version>-<case>.json)"
    )


def test_every_migration_since_compatible_changes_keep_the_version_rewrites_its_fixture() -> None:
    """A step that only renumbers is a compatible change that should have kept the version.

    A meaning change (the same shape read differently) is no exception: its fixture holds the
    data the step rewrites.
    """
    renumbering = []
    for version, step in core.ENVELOPE_MIGRATIONS.items():
        if version < _REWRITES_SINCE:
            continue
        fixtures = [
            json.loads(p.read_text(encoding="utf-8")) for p in _FILES if _version(p) == version
        ]
        if not any({**step(dict(f)), "schema_version": version} != f for f in fixtures):
            renumbering.append(version)
    assert not renumbering, (
        f"the migrations of envelope versions {renumbering} rewrite none of their fixtures: drop "
        "the version raise if every saved envelope decodes as is, or save a fixture holding the "
        "data the step rewrites"
    )
