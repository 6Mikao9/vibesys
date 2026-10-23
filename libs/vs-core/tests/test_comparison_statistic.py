"""The statistic behind a comparison: one judgement per objective and the dominance over them.

A comparison is of the candidate's samples with the base's, objective by objective. These
properties hold for any samples: the order they were taken in changes nothing, a unit change
(scaling every value) changes nothing, flipping which way is better flips better and worse, a
higher declared noise never turns an undecided result into a verdict, and the noise is never
zero, so every comparison with a base that has a scale has a verdict. An independent oracle
restates the rule (`vs_core._dominance`): a side's relative variation is the declared noise, else
its spread over runs that differ, else the stated default; the objective is judged by two
standard errors of the ratio (the Student-t quantile of the Welch-Satterthwaite degrees when
a spread is what it rests on); and the comparison is `better` exactly when no objective is worse
and one is better.
"""

from __future__ import annotations

import math
from typing import Literal

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

import vs_core.api as core

type Direction = Literal["max", "min"] | None

SCOPE = core.Scope(owner=core.AttemptId(root="attempt"), generation=0)
ANCHOR = core.BaseRef(commit=core.RevisionRef.of_git_commit("base"), measured_input="base-input")
VALUES = st.floats(min_value=1.0, max_value=1e4, allow_nan=False, allow_infinity=False)
FLOORS = st.one_of(st.just(0.0), st.floats(min_value=1e-4, max_value=0.2, allow_nan=False))
CRITICAL = 2.0
DEFAULT = 0.05


def sample(
    value: float,
    *,
    direction: Direction = "max",
    measured_input: str = "x",
    others: tuple[tuple[str, float, Direction], ...] = (),
) -> core.Sample:
    """One run of ``measured_input``: the headline ``rate`` and any ``others`` (name, value, direction)."""
    return core.Sample(
        run=core.RequestId(
            root="run-" + "-".join([measured_input, repr(value), *(repr(v) for _, v, _ in others)])
        ),
        scope=SCOPE,
        commit=core.RevisionRef.of_git_commit("candidate"),
        measured_input=measured_input,
        evaluation="evaluation",
        gates=("accuracy", "benchmark"),
        host="host",
        dedicated_resources=True,
        readings=(
            core.Reading(metric="rate", value=value, direction=direction),
            *(core.Reading(metric=name, value=v, direction=d) for name, v, d in others),
        ),
        observed_at=0.0,
        base=ANCHOR if measured_input != "base-input" else None,
    )


def compared(
    mine: list[float],
    theirs: list[float],
    *,
    floor: float = 0.0,
    direction: Direction = "max",
) -> core.Comparison:
    """The headline-only comparison of ``mine`` with ``theirs`` under a task declaring ``floor``."""
    result = core.compare(
        tuple(sample(v, direction=direction) for v in mine),
        tuple(sample(v, direction=direction, measured_input="base-input") for v in theirs),
        scope=SCOPE,
        anchor=ANCHOR,
        policy=core.ComparisonPolicy(noise_floor=floor),
    )
    assert result is not None
    return result


# -- the oracle: the rule restated independently of `vs_core._dominance` -----------------------


_STUDENT_975 = {
    1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262,
    10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131, 16: 2.120, 17: 2.110,
    18: 2.101, 19: 2.093, 20: 2.086, 21: 2.080, 22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060,
    26: 2.056, 27: 2.052, 28: 2.048, 29: 2.045, 30: 2.042,
}  # fmt: skip


def _variation(values: list[float], declared: float) -> tuple[float, float | None]:
    """A side's relative variation of one run and the degrees it rests on (None when known)."""
    if declared > 0:
        return declared, None
    if len(values) >= 2 and min(values) != max(values):
        mean = sum(values) / len(values)
        spread = math.sqrt(sum(((v - mean) / mean) ** 2 for v in values) / (len(values) - 1))
        return spread, len(values) - 1
    return DEFAULT, None


def oracle_objective(
    mine: list[float], theirs: list[float], direction: str, declared: float
) -> core.ComparisonVerdict:
    """The verdict of one objective by the stated rule, from the raw runs."""
    c, b = sum(mine) / len(mine), sum(theirs) / len(theirs)
    ratio = c / b
    sides = [(*_variation(values, declared), len(values)) for values in (mine, theirs)]
    parts = [(v**2 / n, df) for v, df, n in sides]
    error = abs(ratio) * math.sqrt(sum(part for part, _ in parts))
    estimated = [(part, df) for part, df in parts if df is not None]
    if estimated:
        degrees = sum(part for part, _ in parts) ** 2 / sum(part**2 / df for part, df in estimated)
        critical = _STUDENT_975[min(max(math.floor(degrees + 1e-9), 1), 30)]
    else:
        critical = CRITICAL
    gain = ratio - 1 if direction == "max" else 1 - ratio
    if gain > critical * error:
        return core.ComparisonVerdict.BETTER
    if gain < -critical * error:
        return core.ComparisonVerdict.WORSE
    return core.ComparisonVerdict.WITHIN_NOISE


def oracle_dominance(verdicts: list[core.ComparisonVerdict]) -> core.ComparisonVerdict:
    """No objective worse and one better is better; any worse is worse; else undecided."""
    if core.ComparisonVerdict.WORSE in verdicts:
        return core.ComparisonVerdict.WORSE
    if core.ComparisonVerdict.BETTER in verdicts:
        return core.ComparisonVerdict.BETTER
    return core.ComparisonVerdict.WITHIN_NOISE


@given(
    mine=st.lists(VALUES, min_size=1, max_size=6),
    theirs=st.lists(VALUES, min_size=1, max_size=6),
    floor=FLOORS,
    direction=st.sampled_from(("max", "min")),
)
def test_a_single_objective_comparison_is_the_oracles_verdict(
    mine: list[float], theirs: list[float], floor: float, direction: Direction
) -> None:
    result = compared(mine, theirs, floor=floor, direction=direction)
    assert direction is not None
    expected = oracle_objective(mine, theirs, direction, floor)
    assert result.objectives[0].verdict is expected
    assert result.verdict is expected


@given(
    mine=st.lists(VALUES, min_size=1, max_size=6), theirs=st.lists(VALUES, min_size=1, max_size=6)
)
def test_the_ratio_is_the_mean_of_the_candidate_over_the_mean_of_the_base(
    mine: list[float], theirs: list[float]
) -> None:
    result = compared(mine, theirs)
    headline = result.objectives[0]
    assert math.isclose(headline.candidate_value, sum(mine) / len(mine))
    assert math.isclose(headline.base_value, sum(theirs) / len(theirs))
    assert math.isclose(headline.ratio, headline.candidate_value / headline.base_value)
    assert (result.candidate_samples, result.base_samples) == (len(mine), len(theirs))


@given(
    data=st.data(),
    mine=st.lists(VALUES, min_size=1, max_size=6),
    theirs=st.lists(VALUES, min_size=1, max_size=6),
    floor=FLOORS,
)
def test_the_order_of_the_samples_changes_nothing(
    data: st.DataObject, mine: list[float], theirs: list[float], floor: float
) -> None:
    shuffled_mine = data.draw(st.permutations(mine))
    shuffled_theirs = data.draw(st.permutations(theirs))
    first = compared(mine, theirs, floor=floor)
    second = compared(shuffled_mine, shuffled_theirs, floor=floor)
    assert second.verdict is first.verdict
    assert math.isclose(second.objectives[0].noise, first.objectives[0].noise, rel_tol=1e-9)


@given(
    mine=st.lists(VALUES, min_size=1, max_size=6),
    theirs=st.lists(VALUES, min_size=1, max_size=6),
    floor=FLOORS,
    scale=st.floats(min_value=0.001, max_value=1000.0),
)
def test_a_unit_change_changes_no_ratio_noise_or_verdict(
    mine: list[float], theirs: list[float], floor: float, scale: float
) -> None:
    first = compared(mine, theirs, floor=floor)
    second = compared([v * scale for v in mine], [v * scale for v in theirs], floor=floor)
    assert second.verdict is first.verdict
    assert math.isclose(second.objectives[0].ratio, first.objectives[0].ratio, rel_tol=1e-9)
    assert math.isclose(
        second.objectives[0].noise, first.objectives[0].noise, rel_tol=1e-9, abs_tol=1e-12
    )


_FLIPPED = {
    core.ComparisonVerdict.BETTER: core.ComparisonVerdict.WORSE,
    core.ComparisonVerdict.WORSE: core.ComparisonVerdict.BETTER,
    core.ComparisonVerdict.WITHIN_NOISE: core.ComparisonVerdict.WITHIN_NOISE,
    core.ComparisonVerdict.INCONCLUSIVE: core.ComparisonVerdict.INCONCLUSIVE,
}


@given(
    mine=st.lists(VALUES, min_size=1, max_size=6),
    theirs=st.lists(VALUES, min_size=1, max_size=6),
    floor=FLOORS,
)
def test_which_way_is_better_flips_better_and_worse_and_nothing_else(
    mine: list[float], theirs: list[float], floor: float
) -> None:
    high = compared(mine, theirs, floor=floor, direction="max")
    low = compared(mine, theirs, floor=floor, direction="min")
    assert low.verdict is _FLIPPED[high.verdict]
    assert low.objectives[0].noise == high.objectives[0].noise
    assert low.objectives[0].ratio == high.objectives[0].ratio


@given(
    mine=st.lists(VALUES, min_size=1, max_size=6),
    theirs=st.lists(VALUES, min_size=1, max_size=6),
    low=FLOORS,
    extra=FLOORS,
    direction=st.sampled_from(("max", "min")),
)
def test_a_higher_declared_noise_never_turns_an_undecided_result_into_a_verdict(
    mine: list[float], theirs: list[float], low: float, extra: float, direction: Direction
) -> None:
    quiet = compared(mine, theirs, floor=low, direction=direction)
    noisy = compared(mine, theirs, floor=low + extra, direction=direction)
    if quiet.verdict is core.ComparisonVerdict.WITHIN_NOISE and low > 0:
        assert noisy.verdict is core.ComparisonVerdict.WITHIN_NOISE


@given(
    mine=st.lists(VALUES, min_size=1, max_size=6),
    theirs=st.lists(VALUES, min_size=1, max_size=6),
    floor=FLOORS,
)
def test_the_noise_is_never_zero_so_every_comparison_has_a_verdict(
    mine: list[float], theirs: list[float], floor: float
) -> None:
    result = compared(mine, theirs, floor=floor)
    assert result.objectives[0].noise > 0
    assert result.verdict is not core.ComparisonVerdict.INCONCLUSIVE


@given(
    mine=st.lists(VALUES, min_size=1, max_size=4), theirs=st.lists(VALUES, min_size=1, max_size=4)
)
def test_a_measurement_without_a_known_direction_or_a_base_is_not_compared(
    mine: list[float], theirs: list[float]
) -> None:
    undirected = core.compare(
        tuple(sample(v, direction=None) for v in mine),
        tuple(sample(v, direction=None, measured_input="base-input") for v in theirs),
        scope=SCOPE,
        anchor=ANCHOR,
        policy=core.ComparisonPolicy(),
    )
    assert undirected is None
    directed = tuple(sample(v) for v in mine)
    none = core.compare(directed, (), scope=SCOPE, anchor=ANCHOR, policy=core.ComparisonPolicy())
    assert none is None


@pytest.mark.regression
@example(ratio=0.57, direction="min")
@given(
    ratio=st.floats(min_value=0.0001, max_value=0.8, allow_nan=False),
    direction=st.sampled_from(("max", "min")),
)
def test_one_run_a_side_and_no_declared_noise_still_decide_a_large_effect(
    ratio: float, direction: Direction
) -> None:
    """The base case of the live run: one run a side and an undeclared noise used to decide
    nothing, because the verdict waited for three runs a side; a large effect now decides at once."""
    base = 100.0
    # A candidate at ``ratio`` below the base is better on a ``min`` objective and worse on ``max``;
    # at under 0.8 the gain exceeds the default's two standard errors (about 14% at one run a side).
    result = compared([base * ratio], [base], direction=direction)
    expected = core.ComparisonVerdict.BETTER if direction == "min" else core.ComparisonVerdict.WORSE
    assert result.verdict is expected


@pytest.mark.regression
@example(runs=[3.0, 3.0], direction="max")
@given(
    runs=st.lists(VALUES, min_size=2, max_size=6),
    direction=st.sampled_from(("max", "min")),
)
def test_runs_that_read_alike_show_no_spread_so_the_default_applies_and_never_zero(
    runs: list[float], direction: Direction
) -> None:
    same = runs[0]
    result = compared([same] * len(runs), [same] * len(runs), direction=direction)
    assert result.verdict is core.ComparisonVerdict.WITHIN_NOISE
    assert result.objectives[0].noise > 0


def multi(
    mine: list[tuple[float, float]],
    theirs: list[tuple[float, float]],
    *,
    directions: tuple[Direction, Direction] = ("max", "max"),
    floor: float = 0.0,
) -> core.Comparison:
    """The comparison of runs that read ``rate`` and ``other`` (the pairs), headline first."""
    result = core.compare(
        tuple(
            sample(a, direction=directions[0], others=(("other", b, directions[1]),))
            for a, b in mine
        ),
        tuple(
            sample(
                a,
                direction=directions[0],
                measured_input="base-input",
                others=(("other", b, directions[1]),),
            )
            for a, b in theirs
        ),
        scope=SCOPE,
        anchor=ANCHOR,
        policy=core.ComparisonPolicy(noise_floor=floor),
    )
    assert result is not None
    return result


PAIRS = st.lists(st.tuples(VALUES, VALUES), min_size=1, max_size=5)


@given(
    mine=PAIRS,
    theirs=PAIRS,
    floor=FLOORS,
    directions=st.tuples(st.sampled_from(("max", "min")), st.sampled_from(("max", "min"))),
)
def test_the_dominance_over_two_objectives_is_the_oracles_whatever_the_headline_did(
    mine: list[tuple[float, float]],
    theirs: list[tuple[float, float]],
    floor: float,
    directions: tuple[Direction, Direction],
) -> None:
    result = multi(mine, theirs, directions=directions, floor=floor)
    assert [item.metric for item in result.objectives] == ["rate", "other"]
    verdicts = [
        oracle_objective(
            [pair[i] for pair in mine], [pair[i] for pair in theirs], str(directions[i]), floor
        )
        for i in range(2)
    ]
    assert [item.verdict for item in result.objectives] == verdicts
    assert result.verdict is oracle_dominance(verdicts)


@pytest.mark.regression
def test_a_large_gain_on_a_second_objective_with_the_headline_unchanged_dominates() -> None:
    """The live run: TTFT fell 44% and TPOT did not move, yet the comparison read within noise."""
    result = multi(
        [(52.86, 19.84)],
        [(53.39, 35.06)],
        directions=("min", "min"),
        floor=0.05,
    )
    assert [item.verdict for item in result.objectives] == [
        core.ComparisonVerdict.WITHIN_NOISE,
        core.ComparisonVerdict.BETTER,
    ]
    assert result.verdict is core.ComparisonVerdict.BETTER
    assert not core.close_call(result)


@given(
    mine=PAIRS,
    theirs=PAIRS,
    floor=FLOORS,
    directions=st.tuples(st.sampled_from(("max", "min")), st.sampled_from(("max", "min"))),
)
def test_a_result_that_dominates_or_regresses_is_never_a_close_call(
    mine: list[tuple[float, float]],
    theirs: list[tuple[float, float]],
    floor: float,
    directions: tuple[Direction, Direction],
) -> None:
    result = multi(mine, theirs, directions=directions, floor=floor)
    decided = {core.ComparisonVerdict.BETTER, core.ComparisonVerdict.WORSE}
    assert core.close_call(result) is (result.verdict not in decided)
    if any(item.verdict is core.ComparisonVerdict.WORSE for item in result.objectives):
        assert not core.close_call(result)


@given(
    verdicts=st.lists(st.sampled_from(list(core.ComparisonVerdict)), max_size=6),
)
def test_dominance_over_verdicts_is_the_oracles(verdicts: list[core.ComparisonVerdict]) -> None:
    got = core.dominance(verdicts)
    undecidable = not verdicts or core.ComparisonVerdict.INCONCLUSIVE in verdicts
    if undecidable and core.ComparisonVerdict.WORSE not in verdicts:
        assert got is core.ComparisonVerdict.INCONCLUSIVE
    else:
        assert got is oracle_dominance(verdicts)
