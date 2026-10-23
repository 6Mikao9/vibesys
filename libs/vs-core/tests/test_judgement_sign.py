"""A judgement reads a difference against the magnitude of the base, whatever its sign.

A reading is better by how far it moved in the direction that is better, relative to the size of
the base. Negating every reading and the direction therefore changes nothing about who wins, and
a base below zero is judged like the mirror of the same base above it: a ``max`` objective whose
base is -100 gains by reading -60, not by -160.
"""

from __future__ import annotations

from typing import Literal

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

import vs_core.api as core
from vs_sim.api.testing import examples

_MAGNITUDES = st.floats(min_value=0.001, max_value=1e6, allow_nan=False, allow_infinity=False)
_RUNS = st.integers(min_value=1, max_value=6)
_SPREADS = st.sampled_from([0.0, 0.01, 0.05, 0.2])
_DECLARED = st.sampled_from([0.0, 0.02, 0.1])


def _runs(mean: float, count: int, spread: float) -> core.Runs:
    return core.Runs(mean=mean, count=count, spread=spread if count > 1 else 0.0)


def _flipped(direction: Literal["max", "min"]) -> Literal["max", "min"]:
    return "min" if direction == "max" else "max"


@pytest.mark.regression
@example(mine=60.0, base=100.0, shape=(1, 0.0), declared=0.1, direction="max")
@settings(max_examples=examples(pr=60, nightly=2000), deadline=None)
@given(
    mine=_MAGNITUDES,
    base=_MAGNITUDES,
    shape=st.tuples(_RUNS, _SPREADS),
    declared=_DECLARED,
    direction=st.sampled_from(["max", "min"]),
)
def test_negating_every_reading_and_the_direction_changes_no_verdict(
    mine: float,
    base: float,
    shape: tuple[int, float],
    declared: float,
    direction: Literal["max", "min"],
) -> None:
    count, spread = shape
    positive = core.judge(
        _runs(mine, count, spread),
        _runs(base, count, spread),
        direction=direction,
        declared=declared,
    )
    negative = core.judge(
        _runs(-mine, count, spread),
        _runs(-base, count, spread),
        direction=_flipped(direction),
        declared=declared,
    )

    assert negative.verdict is positive.verdict
    assert negative.noise == pytest.approx(positive.noise)


def test_a_max_objective_below_zero_gains_by_rising_toward_zero() -> None:
    base = core.Runs(mean=-100.0, count=1)
    toward = core.judge(core.Runs(mean=-60.0, count=1), base, direction="max", declared=0.1)
    away = core.judge(core.Runs(mean=-160.0, count=1), base, direction="max", declared=0.1)

    assert toward.verdict is core.ComparisonVerdict.BETTER
    assert away.verdict is core.ComparisonVerdict.WORSE
