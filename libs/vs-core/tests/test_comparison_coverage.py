"""A comparison judges every objective the candidate reports, whichever samples report it.

A sample taken before an objective was reported, or by a benchmark that never did, must neither
hide the objective from the comparison nor be read as a value it did not take. Each side's runs
of an objective are the samples that report it. An objective the base has no run of cannot be
judged: the comparison is inconclusive until the base reports it (never better on the objectives
that could be judged), and none exists while the headline is the one unjudged. The verdict is a
pure function of the samples held, so no order of arrival changes it.

The shape that went wrong in a live run: a candidate reporting TPOT and TTFT, and base samples
that reported TPOT only. TTFT dropped out of every comparison for as long as one such sample was
fresh, and the candidate's 40% TTFT gain never entered the verdict.
"""

from __future__ import annotations

import random
from typing import Literal

from hypothesis import example, given
from hypothesis import strategies as st

import vs_core.api as core

type Way = Literal["max", "min"]

SCOPE = core.Scope(owner=core.AttemptId(root="attempt"), generation=0)
ANCHOR = core.BaseRef(commit=core.RevisionRef.of_git_commit("base"), measured_input="base-input")
AXES: tuple[tuple[str, Way], ...] = (("tpot", "min"), ("ttft", "min"), ("done", "max"))
NOISE = 0.05
POLICY = core.ComparisonPolicy(noise_floor=NOISE, directions=AXES)
"""A task that declares its noise and its objectives, so every verdict is known."""


def sample(
    reading: dict[str, float], *, base: bool = False, serial: int = 0, tell: bool = True
) -> core.Sample:
    """One run that reports the axes ``reading`` holds, headline first."""
    return core.Sample(
        run=core.RequestId(root=f"run-{'base' if base else 'candidate'}-{serial}"),
        scope=SCOPE,
        commit=core.RevisionRef.of_git_commit("candidate"),
        measured_input="base-input" if base else "input",
        evaluation="evaluation",
        gates=("accuracy", "benchmark"),
        host="host",
        dedicated_resources=True,
        readings=tuple(
            core.Reading(metric=name, value=reading[name], direction=way if tell else None)
            for name, way in AXES
            if name in reading
        ),
        observed_at=0.0,
        base=None if base else ANCHOR,
    )


def compared(
    mine: list[dict[str, float]], theirs: list[dict[str, float]], *, tell: bool = True
) -> core.Comparison | None:
    return core.compare(
        tuple(sample(r, serial=i, tell=tell) for i, r in enumerate(mine)),
        tuple(sample(r, base=True, serial=i, tell=tell) for i, r in enumerate(theirs)),
        scope=SCOPE,
        anchor=ANCHOR,
        policy=POLICY,
    )


# -- the exact shape --------------------------------------------------------------------------------

TPOT_ONLY = {"tpot": 53.4, "done": 2.0}
CANDIDATE = {"tpot": 52.9, "ttft": 19845.0, "done": 2.0}
"""TPOT within noise of the base's, TTFT far better, the same completions."""


def test_an_objective_the_base_has_a_sample_of_is_judged_though_older_samples_lack_it() -> None:
    """The base's old samples report TPOT only; its newest reports TTFT too: TTFT is judged.

    Before, one old sample hid TTFT from the comparison for as long as it was fresh, and the
    comparison was a close call on TPOT alone.
    """
    result = compared([CANDIDATE], [TPOT_ONLY, TPOT_ONLY, {**TPOT_ONLY, "ttft": 35000.0}])

    assert result is not None
    judged = {item.metric: item for item in result.objectives}
    assert set(judged) == {"tpot", "ttft", "done"}
    assert judged["ttft"].verdict is core.ComparisonVerdict.BETTER
    assert judged["tpot"].verdict is core.ComparisonVerdict.WITHIN_NOISE
    assert result.verdict is core.ComparisonVerdict.BETTER


def test_an_objective_no_sample_of_the_base_reports_makes_the_result_inconclusive_not_a_close_call() -> (
    None
):
    """With no TTFT on the base, the candidate cannot be called better, and is not within noise."""
    result = compared([CANDIDATE], [TPOT_ONLY, TPOT_ONLY])

    assert result is not None
    assert {item.metric for item in result.objectives} == {"tpot", "done"}
    assert result.verdict is core.ComparisonVerdict.INCONCLUSIVE
    assert not core.close_call(result)


def test_a_regression_on_an_objective_judged_is_decided_whatever_the_base_lacks() -> None:
    slower = {**CANDIDATE, "tpot": 106.8}
    result = compared([slower], [TPOT_ONLY, TPOT_ONLY])

    assert result is not None
    assert result.verdict is core.ComparisonVerdict.WORSE


def test_there_is_no_comparison_while_the_headline_is_the_objective_the_base_lacks() -> None:
    """The headline stands for the effect (`effect_verdict`); another objective must not."""
    assert compared([CANDIDATE], [{"ttft": 35000.0, "done": 2.0}]) is None


def test_the_candidates_runs_of_an_objective_are_the_samples_that_report_it() -> None:
    result = compared(
        [TPOT_ONLY, CANDIDATE], [{**TPOT_ONLY, "ttft": 35000.0}, {**TPOT_ONLY, "ttft": 35000.0}]
    )

    assert result is not None
    judged = {item.metric: item for item in result.objectives}
    assert judged["ttft"].candidate_value == CANDIDATE["ttft"]
    assert judged["tpot"].candidate_value == (TPOT_ONLY["tpot"] + CANDIDATE["tpot"]) / 2
    assert result.candidate_samples == 2


# -- the class: any coverage, any order --------------------------------------------------------------

REPORTED = st.lists(st.sampled_from([name for name, _ in AXES]), min_size=1, unique=True)
GAP = st.sampled_from([0.5, 1.0, 2.0])
"""A candidate reads half, equal or double the base: far enough that no run count changes a
verdict (the widest noise is one run a side), or exactly equal."""
BASE_VALUE = {"tpot": 50.0, "ttft": 30000.0, "done": 2.0}


@st.composite
def worlds(
    draw: st.DrawFn,
) -> tuple[list[dict[str, float]], list[dict[str, float]]]:
    """Samples of both sides, each reporting a random set of the axes, headline first."""

    def run(*, gaps: bool) -> dict[str, float]:
        chosen = draw(REPORTED)
        return {
            name: BASE_VALUE[name] * (draw(GAP) if gaps else 1.0)
            for name, _ in AXES
            if name in chosen
        }

    return (
        [run(gaps=True) for _ in range(draw(st.integers(1, 3)))],
        [run(gaps=False) for _ in range(draw(st.integers(1, 3)))],
    )


def oracle(
    mine: list[dict[str, float]], theirs: list[dict[str, float]]
) -> tuple[core.ComparisonVerdict | None, set[str]]:
    """The rule restated: judge each objective the candidate reports over the samples that
    report it, an objective the base has no run of is inconclusive, and none judged is no
    comparison. Returns the verdict and the objectives judged."""
    reported = [name for name, _ in AXES if name in mine[-1]]
    verdicts: list[core.ComparisonVerdict] = []
    judged: set[str] = set()
    unjudged = False
    for index, name in enumerate(reported):
        base = [r[name] for r in theirs if name in r]
        candidate = [r[name] for r in mine if name in r]
        if not base:
            if index == 0:
                return None, set()
            unjudged = True
            continue
        way = dict(AXES)[name]
        verdicts.append(
            core.judge(
                core.Runs.of(tuple(candidate)),
                core.Runs.of(tuple(base)),
                direction=way,
                declared=NOISE,
            ).verdict
        )
        judged.add(name)
    if not judged:
        return None, set()
    return core.dominance(
        [*verdicts, core.ComparisonVerdict.INCONCLUSIVE] if unjudged else verdicts
    ), judged


@given(world=worlds(), tell=st.booleans())
@example(world=([CANDIDATE], [TPOT_ONLY, {**TPOT_ONLY, "ttft": 35000.0}]), tell=True)
def test_the_comparison_is_the_oracles_whichever_objectives_each_sample_reports(
    world: tuple[list[dict[str, float]], list[dict[str, float]]], *, tell: bool
) -> None:
    mine, theirs = world
    result = compared(mine, theirs, tell=tell)
    verdict, judged = oracle(mine, theirs)

    if verdict is None:
        assert result is None
        return
    assert result is not None
    assert result.verdict is verdict
    assert {item.metric for item in result.objectives} == judged


@given(world=worlds(), seed=st.integers(0, 2**32))
def test_the_order_samples_arrive_in_changes_no_comparison(
    world: tuple[list[dict[str, float]], list[dict[str, float]]], seed: int
) -> None:
    """Nothing about a verdict depends on which run came first or last."""
    mine, theirs = world
    # the candidate's latest sample names the objectives, so keep it last; the base shuffles freely
    head, last = mine[:-1], mine[-1]
    shuffled = random.Random(seed)
    other_head, other_theirs = list(head), list(theirs)
    shuffled.shuffle(other_head)
    shuffled.shuffle(other_theirs)

    one = compared(mine, theirs)
    two = compared([*other_head, last], other_theirs)

    assert (one is None) == (two is None)
    if one is not None and two is not None:
        assert one.verdict is two.verdict
        assert {(i.metric, i.verdict) for i in one.objectives} == {
            (i.metric, i.verdict) for i in two.objectives
        }


@given(world=worlds())
def test_a_candidate_is_never_better_while_an_objective_it_reports_is_unjudged(
    world: tuple[list[dict[str, float]], list[dict[str, float]]],
) -> None:
    """An objective is judged or the result is inconclusive; never better on the rest alone."""
    mine, theirs = world
    result = compared(mine, theirs)
    wanted = {name for name, _ in AXES if name in mine[-1]}
    held = {name for r in theirs for name in r}

    if result is not None and wanted - held:
        assert result.verdict in {core.ComparisonVerdict.INCONCLUSIVE, core.ComparisonVerdict.WORSE}
