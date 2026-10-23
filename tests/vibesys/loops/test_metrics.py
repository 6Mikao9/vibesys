"""Tests for the metric space shared by the optimization loops.

Guarantees under test:

* A metric space is frozen with unique axes and a validated relative noise.
* Comparison is vs-core's one rule (`vs_core.api.judge`, `dominance`) projected onto the space's
  axes: direction-aware, decided only beyond the band two standard errors wide, under the noise
  the task declares or the stated default (never a zero band); missing or mismatched evidence is
  inconclusive.
* Dominance needs every axis and one material win; the frontier keeps every tradeoff and drops
  dominated points; completeness needs a value on every configured axis.
"""

from __future__ import annotations

from typing import Literal

import pytest
from pydantic import ValidationError

from vibesys.metrics import Measurement, MetricSpace, Objective
from vs_core.api import DEFAULT_RELATIVE_NOISE, ComparisonVerdict

_OPS = Objective(name="ops", direction="max")
_LATENCY = Objective(name="latency", direction="min")


def _reading(value: float, metric: str = "ops") -> Measurement:
    return Measurement(metric=metric, value=value)


def test_relative_noise_is_validated_to_the_configurable_range() -> None:
    assert MetricSpace().relative_noise == 0.0
    assert MetricSpace(relative_noise=0.05).relative_noise == 0.05
    for invalid in (-0.01, 1.0, 1.5):
        with pytest.raises(ValidationError):
            MetricSpace(relative_noise=invalid)


def test_the_space_is_frozen_and_its_axes_are_unique() -> None:
    assert MetricSpace.model_config["frozen"] is True
    with pytest.raises(ValidationError):
        MetricSpace(objectives=(_OPS, Objective(name="ops", direction="min")))


def test_the_axis_supplies_the_direction_a_reading_omits() -> None:
    space = MetricSpace(objectives=(_OPS,))

    assert space.direction(_reading(1.0)) == "max"
    # A reading measured before its axis was configured keeps its own meaning.
    assert space.direction(Measurement(metric="ops", value=1.0, direction="min")) == "min"
    assert space.direction(_reading(1.0, "unknown")) is None
    assert space.direction(None) is None


def test_signed_primary_orients_headline_values_for_ranking() -> None:
    maximizing = MetricSpace(objectives=(_OPS,))
    minimizing = MetricSpace(objectives=(_LATENCY,))

    assert maximizing.signed_primary(12.0) == 12.0
    assert minimizing.signed_primary(12.0) == -12.0
    assert MetricSpace().signed_primary(12.0) == 12.0


def _compare(space: MetricSpace, candidate: float, baseline: float) -> ComparisonVerdict:
    return space.compare((_reading(candidate),), (_reading(baseline),))


@pytest.mark.parametrize("direction", ["max", "min"])
def test_a_difference_inside_the_band_is_within_noise_and_beyond_it_a_result(
    direction: Literal["max", "min"],
) -> None:
    """A declared noise of 5% a run makes the band about 14% of the base, either side."""
    space = MetricSpace(
        objectives=(Objective(name="ops", direction=direction),), relative_noise=0.05
    )
    higher = ComparisonVerdict.BETTER if direction == "max" else ComparisonVerdict.WORSE
    lower = ComparisonVerdict.WORSE if direction == "max" else ComparisonVerdict.BETTER

    assert _compare(space, 105.0, 100.0) is ComparisonVerdict.WITHIN_NOISE
    assert _compare(space, 95.0, 100.0) is ComparisonVerdict.WITHIN_NOISE
    assert _compare(space, 130.0, 100.0) is higher
    assert _compare(space, 70.0, 100.0) is lower


@pytest.mark.parametrize("direction", ["max", "min"])
def test_direction_decides_which_side_of_the_baseline_is_better(
    direction: Literal["max", "min"],
) -> None:
    space = MetricSpace(
        objectives=(Objective(name="ops", direction=direction),), relative_noise=0.05
    )

    higher = _compare(space, 150.0, 100.0)
    lower = _compare(space, 60.0, 100.0)

    assert higher is (ComparisonVerdict.BETTER if direction == "max" else ComparisonVerdict.WORSE)
    assert lower is (ComparisonVerdict.WORSE if direction == "max" else ComparisonVerdict.BETTER)


def test_a_space_that_declares_no_noise_is_judged_under_the_stated_default_never_strictly() -> None:
    space = MetricSpace(objectives=(_OPS,))

    assert DEFAULT_RELATIVE_NOISE == 0.05
    assert space.noise_of("ops") == 0.0  # it declares none; the rule states a default for it
    assert _compare(space, 100.000001, 100.0) is ComparisonVerdict.WITHIN_NOISE
    assert _compare(space, 99.999999, 100.0) is ComparisonVerdict.WITHIN_NOISE
    assert _compare(space, 100.0, 100.0) is ComparisonVerdict.WITHIN_NOISE
    assert _compare(space, 200.0, 100.0) is ComparisonVerdict.BETTER
    assert _compare(space, 50.0, 100.0) is ComparisonVerdict.WORSE


def test_a_zero_baseline_has_no_scale_so_only_an_equal_reading_is_judged() -> None:
    """The noise is relative, so at a zero baseline there is nothing to hold a difference against."""
    space = MetricSpace(objectives=(_OPS,), relative_noise=0.5)

    assert _compare(space, 0.0, 0.0) is ComparisonVerdict.WITHIN_NOISE
    assert _compare(space, 0.5, 0.0) is ComparisonVerdict.INCONCLUSIVE
    assert _compare(space, -0.5, 0.0) is ComparisonVerdict.INCONCLUSIVE


def test_a_negative_baseline_scales_the_noise_by_its_magnitude() -> None:
    space = MetricSpace(objectives=(_OPS,), relative_noise=0.1)
    minimizing = MetricSpace(
        objectives=(Objective(name="ops", direction="min"),), relative_noise=0.1
    )

    assert _compare(space, -95.0, -100.0) is ComparisonVerdict.WITHIN_NOISE
    assert _compare(space, -60.0, -100.0) is ComparisonVerdict.BETTER
    assert _compare(space, -160.0, -100.0) is ComparisonVerdict.WORSE
    assert _compare(minimizing, -60.0, -100.0) is ComparisonVerdict.WORSE


@pytest.mark.parametrize(
    ("candidate", "baseline"),
    [
        ((), (_reading(100.0),)),
        ((_reading(100.0),), ()),
        ((_reading(100.0, "unconfigured"),), (_reading(100.0, "unconfigured"),)),
        ((_reading(100.0),), (_reading(100.0, "latency"),)),
    ],
)
def test_missing_or_mismatched_evidence_is_inconclusive(
    candidate: tuple[Measurement, ...], baseline: tuple[Measurement, ...]
) -> None:
    space = MetricSpace(objectives=(_OPS,), relative_noise=0.05)

    assert space.compare(candidate, baseline) is ComparisonVerdict.INCONCLUSIVE


def test_best_and_compare_to_best_order_a_scalar_history() -> None:
    space = MetricSpace(objectives=(_OPS,), relative_noise=0.05)
    history = [_reading(100.0), _reading(90.0), _reading(99.0)]

    assert space.best(history, lambda item: item) == _reading(100.0)
    assert space.best([], lambda item: item) is None
    # Ties, including ties within the tolerance, keep the earlier member, so
    # callers order their own tie-break.
    assert space.best([_reading(100.0, "a"), _reading(103.0, "a")], _named_a) == _reading(
        100.0, "a"
    )
    # An empty history is advanced by anything measurable.
    assert space.compare_to_best(_reading(1.0), []) is ComparisonVerdict.BETTER
    assert space.compare_to_best(None, history) is ComparisonVerdict.INCONCLUSIVE
    assert (
        space.compare_to_best(_reading(1.0, "unconfigured"), []) is ComparisonVerdict.INCONCLUSIVE
    )
    assert space.compare_to_best(_reading(104.0), history) is ComparisonVerdict.WITHIN_NOISE
    assert space.compare_to_best(_reading(130.0), history) is ComparisonVerdict.BETTER


def _named_a(item: Measurement) -> Measurement:
    """Read every member on one axis so the space can order them."""
    return Measurement(metric="ops", value=item.value)


def test_dominance_needs_every_axis_and_one_material_win() -> None:
    space = MetricSpace(objectives=(_OPS, _LATENCY), relative_noise=0.03)
    baseline = {"ops": 100.0, "latency": 100.0}

    assert space.dominates({"ops": 110.0, "latency": 101.0}, baseline)
    # Better on one axis, materially worse on the other.
    assert not space.dominates({"ops": 110.0, "latency": 130.0}, baseline)
    # Everything within noise is not a dominating point.
    assert not space.dominates({"ops": 102.0, "latency": 101.0}, baseline)
    # A missing axis makes the rows incomparable rather than dominated.
    assert not space.dominates({"ops": 110.0}, baseline)
    assert not space.dominates({"ops": 110.0, "latency": 90.0}, {"ops": 100.0})


def test_a_space_with_no_axes_dominates_nothing() -> None:
    assert not MetricSpace().dominates({"ops": 110.0}, {"ops": 100.0})
    assert not MetricSpace().complete({"ops": 110.0})


def test_the_frontier_keeps_every_tradeoff_and_drops_dominated_points() -> None:
    space = MetricSpace(objectives=(_OPS, _LATENCY))
    rows = [
        {"ops": 100.0, "latency": 80.0},
        {"ops": 140.0, "latency": 100.0},
        {"ops": 90.0, "latency": 110.0},
    ]

    assert space.frontier(rows, lambda row: row) == rows[:2]


def test_completeness_requires_a_value_on_every_configured_axis() -> None:
    space = MetricSpace(objectives=(_OPS, _LATENCY))

    assert space.complete({"ops": 1.0, "latency": 2.0, "extra": 3.0})
    assert not space.complete({"ops": 1.0})
