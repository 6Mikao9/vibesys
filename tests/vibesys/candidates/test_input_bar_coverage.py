"""The input's bar states the runs each objective rests on, whichever samples reported it.

An objective some samples of the input did not report is the mean of those that did, and its row
says so: the bar must not rest it on more runs than it has, since a comparison narrows its noise
by the runs a row states. A record written before rows stated their runs is the exception it
always was: its bar is the mean of ``samples`` readings in rows that state one.
"""

from __future__ import annotations

from hypothesis import example, given
from hypothesis import strategies as st

from vibesys.candidates.api import InputReading, Measured, input_metrics, with_samples
from vibesys.metrics import AcceptedReading, MetricRow
from vs_core.api import EvidenceId, EvidenceKind, RequestId

NAMES = ("tpot_ms_avg", "ttft_ms_avg", "completed_requests")


def benchmark(serial: int, reported: tuple[str, ...]) -> AcceptedReading:
    """A passed trusted benchmark that reports the objectives ``reported``."""
    return AcceptedReading(
        source_request=RequestId(root=f"request-{serial}"),
        evidence_id=EvidenceId(root=f"benchmark-{serial}"),
        kind=EvidenceKind.BENCHMARK,
        passed=True,
        stage="benchmark",
        metrics=tuple(
            MetricRow(name=name, value=10.0 * (serial + 1), direction="min") for name in reported
        ),
    )


def first_sample(reported: tuple[str, ...]) -> Measured:
    return Measured(
        reading=InputReading(
            accuracy_passed=True,
            benchmark_passed=True,
            metrics=tuple(MetricRow(name=name, value=10.0, direction="min") for name in reported),
        )
    )


COVERAGE = st.lists(
    st.lists(st.sampled_from(NAMES), min_size=1, max_size=3, unique=True).map(tuple),
    min_size=2,
    max_size=5,
)


@given(coverage=COVERAGE)
@example(coverage=[("tpot_ms_avg",), ("tpot_ms_avg", "ttft_ms_avg")])
def test_a_row_of_the_bar_rests_on_exactly_the_samples_that_reported_it(
    coverage: list[tuple[str, ...]],
) -> None:
    bar = input_metrics(
        with_samples(
            first_sample(coverage[0]),
            tuple(benchmark(serial, reported) for serial, reported in enumerate(coverage)),
        )
    )

    assert {row.name: row.runs for row in bar} == {
        name: sum(name in reported for reported in coverage)
        for name in NAMES
        if any(name in reported for reported in coverage)
    }


def test_a_bar_written_before_rows_stated_their_runs_is_the_mean_of_its_samples() -> None:
    legacy = Measured(
        reading=InputReading(
            accuracy_passed=True,
            benchmark_passed=True,
            metrics=(MetricRow(name="tpot_ms_avg", value=50.0, direction="min"),),
            samples=3,
        )
    )

    assert [row.runs for row in input_metrics(legacy)] == [3]
