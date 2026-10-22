"""The bar a candidate must beat is the mean of every trusted benchmark of the input.

One measurement of the input is one draw from its noise. The first draw of a run read TPOT 49.63 ms
and the later, identical-code ones 53.5 ms; a bar at the first made a candidate at 51 to 52 ms flip
between within noise and worse on a single sample.
"""

from __future__ import annotations

import itertools
from statistics import fmean

from hypothesis import given
from hypothesis import strategies as st

from vibesys.candidates.api import (
    Baseline,
    InputReading,
    Measured,
    declared_directions,
    input_metrics,
    samples_of,
    space_of,
    with_samples,
)
from vibesys.metrics import (
    AcceptedReading,
    Direction,
    MetricComparison,
    MetricRow,
    with_declared_directions,
)
from vs_core.api import (
    ArtifactId,
    ArtifactRef,
    EvidenceId,
    EvidenceKind,
    RequestId,
)
from vs_core.api.search import TaskObjective, TaskTerms
from vs_core.api.values import ObjectiveNoise


def _noise(relative: float) -> ObjectiveNoise:
    return ObjectiveNoise(floor=relative, relative_noise=relative, provenance="test")


TERMS = TaskTerms(
    recipe=ArtifactRef(artifact_id=ArtifactId(root="recipe"), digest="recipe"),
    stages=(),
    measured=(),
    queue_allowance=0,
    operation_seconds=1,
    objectives=(
        TaskObjective(name="tpot_ms_avg", direction="min", noise=_noise(0.05)),
        TaskObjective(name="ttft_ms_avg", direction="min", noise=_noise(0.0772)),
    ),
)
SPACE = space_of(TERMS)
DIRECTIONS: tuple[Direction, ...] = ("max", "min")


def rows(tpot: float, ttft: float) -> tuple[MetricRow, ...]:
    """A benchmark's metrics, headline first."""
    return (
        MetricRow(name="tpot_ms_avg", value=tpot, direction="min", unit="ms"),
        MetricRow(name="ttft_ms_avg", value=ttft, direction="min", unit="ms"),
    )


def reading(index: int, tpot: float, ttft: float) -> AcceptedReading:
    """The decoded trusted benchmark record number ``index`` of the input."""
    return AcceptedReading(
        evidence_id=EvidenceId(root=f"b{index}"),
        kind=EvidenceKind.BENCHMARK,
        passed=True,
        stage="benchmark",
        metrics=rows(tpot, ttft),
        source_request=RequestId(root=f"r{index}"),
    )


def first_sample(tpot: float, ttft: float) -> Measured:
    """The input as its own measurement stated it."""
    return Measured(
        reading=InputReading(accuracy_passed=True, benchmark_passed=True, metrics=rows(tpot, ttft))
    )


def compare(candidate: tuple[MetricRow, ...], baseline: Baseline) -> MetricComparison:
    """How a candidate relates to the input's bar."""
    return SPACE.compare_metrics(candidate, input_metrics(baseline))


SAMPLES = st.lists(st.tuples(st.floats(40, 70), st.floats(20_000, 40_000)), min_size=1, max_size=5)


@given(samples=SAMPLES)
def test_the_bar_is_the_mean_of_the_recorded_samples_whichever_came_first(
    samples: list[tuple[float, float]],
) -> None:
    readings = tuple(reading(i, *pair) for i, pair in enumerate(samples))
    expected = (fmean(v[0] for v in samples), fmean(v[1] for v in samples))
    for start in range(len(samples)):
        baseline = with_samples(first_sample(*samples[start]), readings)
        assert [row.value for row in input_metrics(baseline)] == list(expected)
        assert samples_of(baseline) == len(samples)


def test_the_reproduced_first_draw_bar_flips_a_winner_that_the_mean_bar_keeps() -> None:
    """Base TPOT 49.63 ms then 53.59 and 53.50 ms; a robust TTFT win at TPOT 52.25 ms."""
    samples = ((49.63, 29_800.0), (53.59, 30_400.0), (53.50, 30_200.0))
    winner = rows(52.25, 29_800.0 * 0.67)
    first_only = first_sample(*samples[0])
    assert compare(winner, first_only) is not MetricComparison.BETTER
    refined = with_samples(first_only, tuple(reading(i, *s) for i, s in enumerate(samples)))
    assert compare(winner, refined) is MetricComparison.BETTER


@given(
    samples=SAMPLES,
    ttft_gain=st.floats(0.15, 0.6),
    tpot_slack=st.floats(-0.04, 0.04),
)
def test_a_candidate_that_wins_within_tolerance_of_the_mean_is_better_whichever_sample_came_first(
    samples: list[tuple[float, float]], ttft_gain: float, tpot_slack: float
) -> None:
    mean_tpot = fmean(s[0] for s in samples)
    mean_ttft = fmean(s[1] for s in samples)
    winner = rows(mean_tpot * (1 + tpot_slack), mean_ttft * (1 - ttft_gain))
    readings = tuple(reading(i, *pair) for i, pair in enumerate(samples))
    for pair in itertools.islice(samples, 3):
        assert (
            compare(winner, with_samples(first_sample(*pair), readings)) is MetricComparison.BETTER
        )


@given(samples=SAMPLES, worse_by=st.floats(0.12, 0.6))
def test_a_candidate_worse_than_the_mean_is_never_better(
    samples: list[tuple[float, float]], worse_by: float
) -> None:
    mean_tpot = fmean(s[0] for s in samples)
    mean_ttft = fmean(s[1] for s in samples)
    loser = rows(mean_tpot * (1 + worse_by), mean_ttft * (1 + worse_by))
    readings = tuple(reading(i, *pair) for i, pair in enumerate(samples))
    for pair in samples:
        assert (
            compare(loser, with_samples(first_sample(*pair), readings))
            is not MetricComparison.BETTER
        )


@given(samples=SAMPLES, decoded=st.sampled_from(DIRECTIONS))
def test_every_row_on_a_declared_axis_states_the_declared_direction_whatever_it_was_decoded_with(
    samples: list[tuple[float, float]], decoded: Direction
) -> None:
    """The decoder states `max` for a non-headline row; TTFT was shown to agents as `better: max`."""
    readings = tuple(
        reading(i, *pair).model_copy(
            update={
                "metrics": (
                    *rows(*pair),
                    MetricRow(name="startup_s", value=1.0, direction=decoded, unit="s"),
                )
            }
        )
        for i, pair in enumerate(samples)
    )
    directed = with_declared_directions(readings, declared_directions(TERMS))
    for item in directed:
        by_name = {row.name: row.direction for row in item.metrics}
        assert by_name["tpot_ms_avg"] == by_name["ttft_ms_avg"] == "min"
        assert by_name["startup_s"] == decoded  # on no declared axis: left as decoded
