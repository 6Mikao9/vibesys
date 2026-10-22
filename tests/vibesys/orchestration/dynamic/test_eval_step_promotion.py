"""Main moves only when one revision has both trusted facts, whatever order they arrived in."""

from __future__ import annotations

from hypothesis import example, given, settings
from hypothesis import strategies as st
from tests.vibesys.orchestration.dynamic._host import TERMS
from tests.vibesys.orchestration.dynamic._values import (
    BASE,
    BASELINE_THROUGHPUT,
    CANDIDATE,
    evidence_of,
    settlement_of,
    workstream_start,
)

from vibesys.orchestration.dynamic.search.api import (
    MainLine,
    MergeOutcome,
    MergeReport,
    WorkKind,
    WorkstreamDone,
)
from vs_sim.api.testing import examples

_GATES = ("accuracy", "benchmark")


def _facts(updates: tuple[tuple[str, bool, int], ...]) -> tuple[bool | None, bool | None, float]:
    """Last write of each gate wins; a gate that never arrived stays unknown."""
    accuracy: bool | None = None
    benchmark: bool | None = None
    throughput = 0.0
    for gate, passed, value in updates:
        if gate == "accuracy":
            accuracy = passed
        else:
            benchmark = passed
            throughput = float(value)
    return accuracy, benchmark, throughput


def _may_become_main(*, accuracy: bool | None, benchmark: bool | None, throughput: float) -> bool:
    """Both facts, accuracy passing, and a benchmark no worse than main's."""
    return accuracy is True and benchmark is True and throughput >= BASELINE_THROUGHPUT


@settings(max_examples=examples(pr=40, nightly=200))
@example(updates=(("benchmark", True, 200),))
@example(updates=(("accuracy", True, 0), ("benchmark", True, 100)))
@example(updates=(("benchmark", True, 10), ("accuracy", False, 0)))
@given(
    updates=st.lists(
        st.tuples(st.sampled_from(_GATES), st.booleans(), st.integers(0, 200)),
        max_size=8,
    ).map(tuple)
)
def test_main_advances_only_with_both_facts_on_the_same_revision(
    updates: tuple[tuple[str, bool, int], ...],
) -> None:
    accuracy, benchmark, throughput = _facts(updates)
    start = workstream_start(WorkKind.MERGE)
    done = WorkstreamDone(
        start=start,
        failure=None,
        settlement=settlement_of(eligible=True, retention="candidate", candidate=CANDIDATE),
        evidence=evidence_of(
            accuracy=accuracy, benchmark=benchmark, throughput=throughput, candidate=CANDIDATE
        ),
        merge=MergeReport(outcome=MergeOutcome.MERGED, summary="merged", revision=CANDIDATE),
    )
    line = MainLine(revision=BASE, baseline=start.baseline)
    line.finished(done, TERMS)
    became_main = line.revision == CANDIDATE
    assert became_main is _may_become_main(
        accuracy=accuracy, benchmark=benchmark, throughput=throughput
    )
    if accuracy is not True:
        assert line.revision == BASE
