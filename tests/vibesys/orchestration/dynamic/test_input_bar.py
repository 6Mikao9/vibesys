"""A candidate is compared with the best evidence of the input, not with its first measurement.

The input is measured once before any agent works, and core takes more runs of it while
candidates are compared. One draw is one point of the input's noise, so settlement compares a
candidate with the mean of every trusted benchmark of the input the ledger holds at the
decision. These stories play the search with a ledger that holds further samples of the input.
"""

from __future__ import annotations

from statistics import fmean

from hypothesis import assume, example, given, settings
from hypothesis import strategies as st
from tests.vibesys.orchestration.dynamic._evidence_host import (
    EvidenceHost,
    begin_with_evidence,
    finish_workstream,
    plan,
)
from tests.vibesys.orchestration.dynamic._host import INPUT_THROUGHPUT, PLANNER_LABEL, config
from tests.vibesys.orchestration.dynamic._replies import implement
from tests.vibesys.vibesys_flows import across_restarts

from vibesys.orchestration.dynamic.search.api import RoundRecord, RunSnapshot
from vs_sim.api.testing import examples

MODES = across_restarts


def _round_of(host: EvidenceHost) -> RoundRecord:
    record = host.run.published(RunSnapshot)
    assert record is not None
    (row,) = [row for hypothesis in record.hypotheses for row in hypothesis.rounds]
    return row


def _eligible(*, restarting: bool, others: list[float], candidate: float) -> bool:
    """Whether a candidate of throughput ``candidate`` is eligible against an input whose ledger
    holds its own measurement (50) and ``others``."""
    host = begin_with_evidence(
        config(max_rounds=1),
        restarting=restarting,
        input_samples=others,
        throughputs={"c1": candidate},
    )
    host.drain_until_turn(PLANNER_LABEL)
    plan(host, implement("h1"))
    finish_workstream(host, "h1", "c1")
    host.drain()
    return _round_of(host).eligible


@MODES
@settings(max_examples=examples(pr=12, nightly=60), deadline=None, derandomize=True, database=None)
@given(
    others=st.lists(st.integers(20, 120).map(float), max_size=4),
    candidate=st.integers(10, 130).map(float),
)
# the first sample is lucky low: 50 against later 70s. A candidate of 60 beat the first sample
# and is not better than the input's typical reading.
@example(others=[70.0, 70.0], candidate=60.0)
@example(others=[70.0, 70.0], candidate=80.0)
def test_a_candidate_is_eligible_exactly_when_it_beats_the_mean_of_every_sample_of_the_input(
    *, restarting: bool, others: list[float], candidate: float
) -> None:
    bar = fmean([INPUT_THROUGHPUT, *others])
    assume(abs(candidate - bar) > 1e-6)
    assert _eligible(restarting=restarting, others=others, candidate=candidate) is (candidate > bar)
