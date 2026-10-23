"""A comparison reads the noise its task declares for the metric it compares, wherever it declared it.

A task states its noise per objective (``TaskObjective.noise``, the objective's tolerance) or for
all of them (``TaskTerms.relative_noise``); the most specific declaration wins for a metric, as it
does for the product's ``MetricSpace``. A metric that has either is a metric with a declared
floor: its comparisons give a verdict from one run a side and core takes no run of the candidate
for them. These properties hold for any tolerance and any counts of runs.
"""

from __future__ import annotations

import math
from typing import Literal

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

import vs_core.api as core

from .test_comparison_statistic import ANCHOR, SCOPE, VALUES
from .test_measurement_comparison import METRIC, Lab, terms
from .test_measurement_reuse import reloaded, submissions

type Direction = Literal["max", "min"]

TOLERANCES = st.floats(min_value=0.01, max_value=0.2, allow_nan=False)
COUNTS = st.integers(min_value=1, max_value=4)
DIRECTIONS = st.sampled_from(["max", "min"])
RATIOS = st.floats(min_value=0.5, max_value=2.0, allow_nan=False)


def sample(value: float, *, direction: Direction = "max", measured_input: str = "x") -> core.Sample:
    """A run of ``measured_input`` reading the task's headline metric (the one it declares)."""
    return core.Sample(
        run=core.RequestId(root=f"run-{measured_input}-{value!r}"),
        scope=SCOPE,
        commit=core.RevisionRef.of_git_commit("candidate"),
        measured_input=measured_input,
        evaluation="evaluation",
        gates=("accuracy", "benchmark"),
        host="host",
        dedicated_resources=True,
        readings=(core.Reading(metric=METRIC, value=value, direction=direction),),
        observed_at=0.0,
        base=ANCHOR if measured_input != "base-input" else None,
    )


def compared(
    mine: list[float], theirs: list[float], policy: core.ComparisonPolicy
) -> core.Comparison:
    result = core.compare(
        tuple(sample(v) for v in mine),
        tuple(sample(v, measured_input="base-input") for v in theirs),
        scope=SCOPE,
        anchor=ANCHOR,
        policy=policy,
    )
    assert result is not None
    return result


def tolerated(tolerance: float) -> core.ComparisonPolicy:
    """The comparison policy of a task that declares only its headline objective's tolerance."""
    return core.terms_policy(terms(floor=0.0, tolerance=tolerance))


def uniform(
    value: float, runs: int, *, base: bool = False, direction: Direction = "max"
) -> tuple[core.Sample, ...]:
    """``runs`` samples reading ``value`` (no spread), of the candidate's input or the base's."""
    return tuple(
        sample(value, direction=direction, measured_input="base-input" if base else "x")
        for _ in range(runs)
    )


def verdict_of(
    ratio: float, mine: int, theirs: int, policy: core.ComparisonPolicy, direction: Direction
) -> core.Comparison:
    result = core.compare(
        uniform(100.0 * ratio, mine, direction=direction),
        uniform(100.0, theirs, base=True, direction=direction),
        scope=SCOPE,
        anchor=ANCHOR,
        policy=policy,
    )
    assert result is not None
    return result


@pytest.mark.regression
@given(
    tolerance=TOLERANCES,
    mine=COUNTS,
    theirs=COUNTS,
    ratio=RATIOS,
    direction=DIRECTIONS,
)
@example(tolerance=0.05, mine=1, theirs=1, ratio=1.02, direction="min")
@example(tolerance=0.05, mine=1, theirs=1, ratio=0.5, direction="min")
def test_a_declared_tolerance_is_the_floor_of_every_run_count_so_one_run_a_side_decides(
    tolerance: float, mine: int, theirs: int, ratio: float, direction: Direction
) -> None:
    """The verdict is the declared-floor statistic with the tolerance as the floor.

    With no spread among the runs the noise is ``ratio * tolerance * sqrt(1/m + 1/n)`` and a
    verdict needs the gain to clear ``VERDICT_NOISES`` of it: ``better`` or ``worse`` by the
    metric's direction past that, ``within_noise`` inside it, and never ``inconclusive``.
    """
    result = verdict_of(ratio, mine, theirs, tolerated(tolerance), direction)
    bar = core.VERDICT_NOISES * ratio * tolerance * math.sqrt(1 / mine + 1 / theirs)
    gain = ratio - 1 if direction == "max" else 1 - ratio
    assert result.verdict is not core.ComparisonVerdict.INCONCLUSIVE
    if abs(abs(gain) - bar) > 1e-9:
        expected = (
            core.ComparisonVerdict.BETTER
            if gain > bar
            else core.ComparisonVerdict.WORSE
            if gain < -bar
            else core.ComparisonVerdict.WITHIN_NOISE
        )
        assert result.verdict is expected


@given(tolerance=TOLERANCES, ratio=st.floats(min_value=-1.0, max_value=1.0), direction=DIRECTIONS)
def test_one_run_a_side_within_the_tolerance_is_within_noise_never_inconclusive(
    tolerance: float, ratio: float, direction: Direction
) -> None:
    within = 1.0 + ratio * tolerance
    result = verdict_of(within, 1, 1, tolerated(tolerance), direction)
    assert result.verdict is core.ComparisonVerdict.WITHIN_NOISE
    assert core.close_call(result)


@given(
    mine=st.lists(VALUES, min_size=1, max_size=5), theirs=st.lists(VALUES, min_size=1, max_size=5)
)
def test_a_tolerance_equal_to_the_tasks_relative_noise_changes_nothing(
    mine: list[float], theirs: list[float]
) -> None:
    """Declaring the floor on the objective is the same declaration as on the task."""
    floor = 0.05
    by_task = core.terms_policy(terms(floor=floor))
    by_objective = core.terms_policy(terms(floor=0.0, tolerance=floor))
    for direction in ("max", "min"):
        runs = (
            tuple(sample(v, direction=direction) for v in mine),
            tuple(sample(v, direction=direction, measured_input="base-input") for v in theirs),
        )
        assert core.compare(*runs, scope=SCOPE, anchor=ANCHOR, policy=by_task) == core.compare(
            *runs, scope=SCOPE, anchor=ANCHOR, policy=by_objective
        )


@given(floor=st.floats(min_value=0.0, max_value=0.2), tolerance=st.none() | TOLERANCES)
def test_the_objectives_own_tolerance_wins_for_its_metric_and_relative_noise_is_the_default(
    floor: float, tolerance: float | None
) -> None:
    policy = core.terms_policy(terms(floor=floor, tolerance=tolerance))
    assert policy.floor_of(METRIC) == (floor if tolerance is None else tolerance)
    assert policy.floor_of("another-metric") == floor
    assert policy.declares_floor(METRIC) == ((floor if tolerance is None else tolerance) > 0)


@given(
    mine=st.lists(VALUES, min_size=1, max_size=5), theirs=st.lists(VALUES, min_size=1, max_size=5)
)
def test_a_task_with_only_relative_noise_compares_as_it_always_did(
    mine: list[float], theirs: list[float]
) -> None:
    for floor in (0.0, 0.03):
        policy = core.terms_policy(terms(floor=floor))
        assert policy == core.ComparisonPolicy(
            noise_floor=floor,
            base_max_age=policy.base_max_age,
            directions=policy.directions,
        )
        assert compared(mine, theirs, policy) == compared(
            mine, theirs, core.ComparisonPolicy(noise_floor=floor)
        )


def test_a_tolerance_of_zero_declares_nothing_so_the_stated_default_applies() -> None:
    """A tolerance of zero is no declaration: the comparison is the undeclared one, never exact."""
    zero = verdict_of(1.5, 1, 1, tolerated(0.0), "max")
    undeclared = verdict_of(1.5, 1, 1, core.ComparisonPolicy(), "max")
    assert zero == undeclared
    assert zero.verdict is core.ComparisonVerdict.BETTER
    assert zero.objectives[0].noise > 0


@pytest.mark.regression
def test_a_close_call_under_a_declared_tolerance_takes_no_run_of_the_candidate() -> None:
    """Without a floor the close call costs two more candidate runs and two more base runs.

    The run this comes from declared its tolerances per objective and no ``relative_noise``, so
    every one-run comparison was ``inconclusive`` and core reran both sides.
    """
    lab = Lab.of("a", floor=0.0, tolerance=0.05)
    lab.call("a", "c1", "input-1")
    lab.measure({"input-1": 101.0, "base": 100.0})
    runs = submissions(lab.state)
    assert [r.plan.purpose for r in runs].count("local-validation") == 1, "the candidate: once"
    assert len(lab.baselines()) == 2, "a close call under a floor earns the base one more run"
    verdicts = {c.verdict for c in lab.comparisons()}
    assert verdicts == {core.ComparisonVerdict.WITHIN_NOISE}
    assert reloaded(lab.state) == lab.state


@pytest.mark.regression
def test_a_decided_comparison_under_a_declared_tolerance_schedules_no_rerun() -> None:
    for value, verdict in (
        (300.0, core.ComparisonVerdict.BETTER),
        (30.0, core.ComparisonVerdict.WORSE),
    ):
        lab = Lab.of("a", floor=0.0, tolerance=0.05)
        lab.call("a", "far", "input-1")
        lab.measure({"input-1": value, "base": 100.0})
        assert [c.verdict for c in lab.comparisons()] == [verdict]
        assert len(submissions(lab.state)) == 2, "one run of the candidate, one of the base"
        assert not core.close_call(lab.comparisons()[-1])
