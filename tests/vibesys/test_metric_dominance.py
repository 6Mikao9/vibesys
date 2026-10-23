"""One dominance rule over objective rows: no objective worse and at least one better.

A candidate that moved its headline within noise but improved another objective far beyond its
noise dominates the base (run 142822: TPOT 53.39 -> 52.86 ms within 5%, TTFT 35.06 -> 19.84 s
against 7.7%). Every decision that reads a comparison of rows (eligibility, fast-forward, merge,
the frontier) reads the same rule, so none of them can lose such a gain to the headline.
"""

from __future__ import annotations

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from vibesys.metrics import MetricComparison, MetricRow, MetricSpace, Objective
from vs_sim.api.testing import examples

_NAMES = ("tpot_ms_avg", "ttft_ms_avg", "completed_requests", "accuracy")
_VALUES = st.floats(min_value=1.0, max_value=1000.0, allow_nan=False, allow_infinity=False)
_NOISES = st.one_of(st.none(), st.floats(min_value=0.0, max_value=0.5))


@st.composite
def _cases(draw: st.DrawFn) -> tuple[MetricSpace, dict[str, float], dict[str, float]]:
    names = draw(st.lists(st.sampled_from(_NAMES), min_size=1, max_size=4, unique=True))
    space = MetricSpace(
        objectives=tuple(
            Objective(name, draw(st.sampled_from(["max", "min"])), draw(_NOISES)) for name in names
        ),
        relative_noise=draw(st.floats(min_value=0.0, max_value=0.5)),
    )
    candidate = {name: draw(_VALUES) for name in names}
    # Baselines at the candidate's value, a hair away or far away, so every class is drawn.
    scale = st.sampled_from([0.4, 0.9, 0.99, 1.0, 1.01, 1.1, 2.5])
    baseline = {name: candidate[name] * draw(scale) for name in names}
    return space, candidate, baseline


def _expected(
    space: MetricSpace, candidate: dict[str, float], baseline: dict[str, float]
) -> dict[str, MetricComparison]:
    """The per-objective verdict from the definition: improvement against the axis tolerance."""
    found = {}
    for axis in space.objectives:
        fraction = space.relative_noise if axis.relative_noise is None else axis.relative_noise
        gain = candidate[axis.name] - baseline[axis.name]
        gain = gain if axis.direction == "max" else -gain
        tolerance = abs(baseline[axis.name]) * fraction
        found[axis.name] = (
            MetricComparison.BETTER
            if gain > tolerance
            else MetricComparison.WORSE
            if gain < -tolerance
            else MetricComparison.WITHIN_NOISE
        )
    return found


def _rows(space: MetricSpace, values: dict[str, float]) -> tuple[MetricRow, ...]:
    return tuple(
        MetricRow(name=axis.name, value=values[axis.name], direction=axis.direction)
        for axis in space.objectives
    )


@pytest.mark.regression
@settings(max_examples=examples(pr=60, nightly=2000), deadline=None)
@given(case=_cases())
@example(
    case=(
        MetricSpace(
            objectives=(
                Objective("tpot_ms_avg", "min", 0.05),
                Objective("ttft_ms_avg", "min", 0.0772),
                Objective("completed_requests", "max", 0.05),
            )
        ),
        {"tpot_ms_avg": 52.859, "ttft_ms_avg": 19840.0, "completed_requests": 2.0},
        {"tpot_ms_avg": 53.393, "ttft_ms_avg": 35060.0, "completed_requests": 2.0},
    )
)
def test_dominance_is_no_objective_worse_and_at_least_one_better_beyond_tolerance(
    case: tuple[MetricSpace, dict[str, float], dict[str, float]],
) -> None:
    space, candidate, baseline = case
    each = _expected(space, candidate, baseline)
    dominates = (
        MetricComparison.WORSE not in each.values() and MetricComparison.BETTER in each.values()
    )
    mine, theirs = _rows(space, candidate), _rows(space, baseline)

    assert space.objective_comparisons(candidate, baseline) == each
    assert space.dominates(candidate, baseline) is dominates
    assert (space.compare_metrics(mine, theirs) is MetricComparison.BETTER) is dominates
    assert space.no_worse_metrics(mine, theirs) is (MetricComparison.WORSE not in each.values())
    if dominates:
        assert not space.dominates(baseline, candidate)
