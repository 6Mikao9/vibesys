"""The product has no comparison of its own: a metric space projects vs-core's one rule.

Whether a reading is better, worse or within noise is decided by ``vs_core.api.judge`` per
objective and ``dominance`` over them. A `MetricSpace` only says which objectives are judged,
which way is better on each and how much noise the task declares for it, so these properties
state the space's answers from vs-core's rule alone: there is no second rule to disagree with the
first. A candidate that moved its headline within noise but improved another objective far beyond
it dominates the base (run 142822: TPOT 53.39 -> 52.86 ms within 5%, TTFT 35.06 -> 19.84 s).
"""

from __future__ import annotations

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from vibesys.metrics import Measurement, MetricRow, MetricSpace, Objective, mean_metrics
from vs_core.api import (
    DEFAULT_RELATIVE_NOISE,
    ComparisonPolicy,
    ComparisonVerdict,
    Runs,
    dominance,
    judge,
)
from vs_sim.api.testing import examples

_NAMES = ("tpot_ms_avg", "ttft_ms_avg", "completed_requests", "accuracy")
_VALUES = st.floats(min_value=1.0, max_value=1000.0, allow_nan=False, allow_infinity=False)
_NOISES = st.one_of(st.none(), st.floats(min_value=0.0, max_value=0.5))
_VERDICTS = st.sampled_from(list(ComparisonVerdict))


@st.composite
def _spaces(draw: st.DrawFn) -> MetricSpace:
    names = draw(st.lists(st.sampled_from(_NAMES), min_size=1, max_size=4, unique=True))
    return MetricSpace(
        objectives=tuple(
            Objective(name, draw(st.sampled_from(["max", "min"])), draw(_NOISES)) for name in names
        ),
        relative_noise=draw(st.floats(min_value=0.0, max_value=0.5)),
    )


@st.composite
def _runs(draw: st.DrawFn, mean: float) -> Runs:
    count = draw(st.integers(min_value=1, max_value=6))
    spread = draw(st.sampled_from([0.0, 0.01, 0.05, 0.2])) if count > 1 else 0.0
    return Runs(mean=mean, count=count, spread=spread)


@st.composite
def _cases(draw: st.DrawFn) -> tuple[MetricSpace, dict[str, Runs], dict[str, Runs]]:
    space = draw(_spaces())
    # Bases at the candidate's value, a hair away or far away, so every verdict is drawn.
    scale = st.sampled_from([0.4, 0.9, 0.99, 1.0, 1.01, 1.1, 2.5])
    candidate = {axis.name: draw(_runs(draw(_VALUES))) for axis in space.objectives}
    base = {name: draw(_runs(runs.mean * draw(scale))) for name, runs in candidate.items()}
    return space, candidate, base


def _rows(space: MetricSpace, side: dict[str, Runs]) -> tuple[MetricRow, ...]:
    return tuple(
        MetricRow(
            name=axis.name,
            value=side[axis.name].mean,
            direction=axis.direction,
            runs=side[axis.name].count,
            spread=side[axis.name].spread,
        )
        for axis in space.objectives
    )


def _by_the_rule(
    space: MetricSpace, candidate: dict[str, Runs], base: dict[str, Runs]
) -> ComparisonVerdict:
    """The space's answer, stated from vs-core's rule alone."""
    declared = ComparisonPolicy(
        noise_floor=space.relative_noise,
        metric_floors=tuple(
            (axis.name, axis.relative_noise)
            for axis in space.objectives
            if axis.relative_noise is not None
        ),
    )
    return dominance(
        judge(
            candidate[axis.name],
            base[axis.name],
            direction=axis.direction,
            declared=declared.floor_of(axis.name),
        ).verdict
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
        {
            "tpot_ms_avg": Runs(mean=52.859, count=1),
            "ttft_ms_avg": Runs(mean=19840.0, count=1),
            "completed_requests": Runs(mean=2.0, count=1),
        },
        {
            "tpot_ms_avg": Runs(mean=53.393, count=1),
            "ttft_ms_avg": Runs(mean=35060.0, count=1),
            "completed_requests": Runs(mean=2.0, count=1),
        },
    )
)
def test_every_comparison_of_rows_is_the_result_of_the_one_rule(
    case: tuple[MetricSpace, dict[str, Runs], dict[str, Runs]],
) -> None:
    space, candidate, base = case
    expected = _by_the_rule(space, candidate, base)
    mine, theirs = _rows(space, candidate), _rows(space, base)

    assert space.compare_rows(mine, theirs) is expected
    assert space.compare([row.reading() for row in mine], [row.reading() for row in theirs]) is (
        expected
    )
    assert space.no_worse_metrics(mine, theirs) is (
        expected in {ComparisonVerdict.BETTER, ComparisonVerdict.WITHIN_NOISE}
    )


@settings(max_examples=examples(pr=60, nightly=2000), deadline=None)
@given(case=_cases())
def test_dominance_of_rows_is_the_rule_on_one_run_each_and_never_both_ways(
    case: tuple[MetricSpace, dict[str, Runs], dict[str, Runs]],
) -> None:
    space, candidate, base = case
    one = {name: Runs(mean=runs.mean, count=1) for name, runs in candidate.items()}
    other = {name: Runs(mean=runs.mean, count=1) for name, runs in base.items()}
    values, against = (
        {name: runs.mean for name, runs in one.items()},
        {name: runs.mean for name, runs in other.items()},
    )

    assert space.dominates(values, against) is (
        _by_the_rule(space, one, other) is ComparisonVerdict.BETTER
    )
    if space.dominates(values, against):
        assert not space.dominates(against, values)


@settings(max_examples=examples(pr=60, nightly=1000), deadline=None)
@given(
    space=_spaces(),
    names=st.lists(st.sampled_from((*_NAMES, "other")), unique=True, max_size=5),
)
def test_the_noise_a_space_declares_is_the_one_the_task_policy_declares(
    space: MetricSpace, names: list[str]
) -> None:
    policy = ComparisonPolicy(
        noise_floor=space.relative_noise,
        metric_floors=tuple(
            (axis.name, axis.relative_noise)
            for axis in space.objectives
            if axis.relative_noise is not None
        ),
    )

    assert all(space.noise_of(name) == policy.floor_of(name) for name in names)


@settings(max_examples=examples(pr=40, nightly=500), deadline=None)
@given(
    values=st.lists(
        st.floats(min_value=1.0, max_value=1000.0, allow_nan=False, allow_infinity=False),
        min_size=1,
        max_size=6,
    )
)
def test_a_mean_of_rows_states_how_many_runs_it_rests_on_and_how_they_spread(
    values: list[float],
) -> None:
    rows = [(MetricRow(name="ops", value=value, direction="max"),) for value in values]

    (pooled,) = mean_metrics(rows)

    assert pooled.runs == len(values)
    assert pooled.spread == Runs.of(values).spread
    assert pooled.reading() == Measurement(
        metric="ops",
        value=pooled.value,
        direction="max",
        runs=len(values),
        spread=Runs.of(values).spread,
    )
    if len(values) == 1:
        assert pooled == rows[0][0]  # a single reading is itself, not a mean of one


def test_a_gain_on_one_objective_is_not_lost_to_a_headline_within_its_noise() -> None:
    """Run 142822: TPOT within its 5%, TTFT 43% lower: the candidate dominates."""
    space = MetricSpace(
        objectives=(Objective("tpot_ms_avg", "min", 0.05), Objective("ttft_ms_avg", "min", 0.0772))
    )
    mine = (
        MetricRow(name="tpot_ms_avg", value=52.859, direction="min"),
        MetricRow(name="ttft_ms_avg", value=19840.0, direction="min"),
    )
    theirs = (
        MetricRow(name="tpot_ms_avg", value=53.393, direction="min"),
        MetricRow(name="ttft_ms_avg", value=35060.0, direction="min"),
    )

    assert space.compare_rows(mine, theirs) is ComparisonVerdict.BETTER
    assert space.compare_rows(theirs, mine) is ComparisonVerdict.WORSE


def test_a_tradeoff_is_a_regression_on_the_objective_that_lost() -> None:
    space = MetricSpace(
        objectives=(Objective("tpot_ms_avg", "min", 0.05), Objective("ttft_ms_avg", "min", 0.05))
    )
    gained_and_lost = (
        MetricRow(name="tpot_ms_avg", value=30.0, direction="min"),
        MetricRow(name="ttft_ms_avg", value=200.0, direction="min"),
    )
    base = (
        MetricRow(name="tpot_ms_avg", value=60.0, direction="min"),
        MetricRow(name="ttft_ms_avg", value=100.0, direction="min"),
    )

    assert space.compare_rows(gained_and_lost, base) is ComparisonVerdict.WORSE
    assert not space.no_worse_metrics(gained_and_lost, base)


def test_a_task_that_declares_no_noise_is_judged_under_the_stated_default() -> None:
    space = MetricSpace(objectives=(Objective("ops", "max"),))
    read = (MetricRow(name="ops", value=104.0, direction="max"),)
    base = (MetricRow(name="ops", value=100.0, direction="max"),)

    assert space.noise_of("ops") == 0.0
    assert DEFAULT_RELATIVE_NOISE > 0
    assert space.compare_rows(read, base) is ComparisonVerdict.WITHIN_NOISE
