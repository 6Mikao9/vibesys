"""Main moves at settlement to a candidate built on it whose one measurement beats it.

The loop is: implement, submit a commit, measure it once, and if it beats the current main it
becomes main. No merge workstream and no second measurement are needed for a candidate whose
parent is main now. A candidate that is worse, or was built on an older main, does not move main
(the latter still goes through a merge).
"""

from __future__ import annotations

from tests.vibesys.orchestration.dynamic._evidence_host import (
    EvidenceHost,
    begin_with_evidence,
    finish_workstream,
    plan,
)
from tests.vibesys.orchestration.dynamic._host import FACTS, PLANNER_LABEL, config
from tests.vibesys.orchestration.dynamic._replies import implement
from tests.vibesys.orchestration.dynamic._slots import planner_context
from tests.vibesys.vibesys_flows import across_restarts

from vibesys.orchestration.dynamic.search.api import MergeOutcome, RunSnapshot
from vs_core.api import Measure, Operation, RevisionRef, Withdraw

MODES = across_restarts
INPUT = FACTS.baseline


def _settle(host: EvidenceHost) -> None:
    """Answer the measurement, the reading of its evidence and the close, as the world would."""
    for _ in range(20):
        pending = [*host.open(Measure), *host.open(Operation), *host.open(Withdraw)]
        if not pending:
            return
        host.answer(pending[0])


def _snapshot(host: EvidenceHost) -> RunSnapshot:
    record = host.run.published(RunSnapshot)
    assert record is not None
    return record


def _run_of(host: EvidenceHost, hypothesis: str, candidate: str) -> None:
    finish_workstream(host, hypothesis, candidate)
    _settle(host)


@MODES
def test_a_candidate_built_on_main_whose_one_measurement_beats_it_becomes_main(
    *, restarting: bool
) -> None:
    host = begin_with_evidence(
        config(max_rounds=5), restarting=restarting, throughputs={"c1": 100.0}
    )
    host.drain_until_turn(PLANNER_LABEL)
    plan(host, implement("h1"))
    _run_of(host, "h1", "c1")

    record = _snapshot(host)
    assert record.main == RevisionRef.of_git_commit("c1")
    assert record.main_measurement is not None
    assert [row.value for row in record.main_measurement.metrics] == [100.0]
    assert [row.value for row in record.main_measurement.previous] == [50.0]  # the input's
    (merged,) = record.merges
    assert merged.outcome is MergeOutcome.MERGED
    assert merged.revision == RevisionRef.of_git_commit("c1")
    # one official measurement of the candidate and none of any merge
    assert host.unlisted_merges == 0
    assert not host.merger_sessions


@MODES
def test_a_candidate_that_does_not_beat_the_input_leaves_main_where_it_was(
    *, restarting: bool
) -> None:
    host = begin_with_evidence(
        config(max_rounds=5), restarting=restarting, throughputs={"c1": 10.0}
    )
    host.drain_until_turn(PLANNER_LABEL)
    plan(host, implement("h1"))
    _run_of(host, "h1", "c1")

    record = _snapshot(host)
    assert record.main == INPUT
    assert record.main_measurement is None
    assert record.merges == ()


@MODES
def test_a_candidate_built_on_an_older_main_does_not_move_main_and_waits_for_a_merge(
    *, restarting: bool
) -> None:
    host = begin_with_evidence(
        config(max_rounds=5, max_in_flight=2),
        restarting=restarting,
        throughputs={"c1": 100.0, "c2": 150.0},
    )
    host.drain_until_turn(PLANNER_LABEL)
    plan(host, implement("h1"), implement("h2"))  # both start from the input
    _run_of(host, "h1", "c1")
    assert _snapshot(host).main == RevisionRef.of_git_commit("c1")
    _run_of(host, "h2", "c2")  # better than main, but built on the input main has left

    record = _snapshot(host)
    assert record.main == RevisionRef.of_git_commit("c1")
    assert [item.merge_id for item in record.merges] == ["h1"]
    host.drain_until_turn(PLANNER_LABEL)
    plan(host, implement("h3"))  # answers the turn asked before h2 settled
    host.drain_until_turn(PLANNER_LABEL)
    context = planner_context(host)
    assert [row.hypothesis_id for row in context.mergeable] == ["h2"]
