"""A judge that asks to measure the candidate its implementer measured gets that evaluation.

Run on the production bridge and core, with scripted agents. The implementer submits an
evaluation of its candidate and ends its turn with the job still running (the v13 AMD run);
the judge, a read-only turn in the same workstream, asks for the same candidate. The run
admits one evaluation per exact candidate and scope, so the judge is handed the
implementer's handle and may wait on it. Before the fix its call was refused for budget, it
passed the candidate while saying so, and the round recorded no metric.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from tests.vibesys.orchestration.dynamic.flow._host import assert_flow_closed, begin, play
from tests.vibesys.orchestration.dynamic.search._run import config, dynamic_state
from tests.vibesys.orchestration.dynamic.search.test_judge_waits import (
    JUDGE_WAITS,
    waiting_world,
)

from vs_core.api import AttemptPhase
from vs_dst.api import SimulatedRun, simulate

SHARED = replace(
    JUDGE_WAITS,
    outcomes=("nominated",),
    roles=("judge",),
    submit_only=("implementer",),
)


def assert_one_evaluation_per_workstream(
    finished: SimulatedRun, asked: list[tuple[str, str, dict]]
) -> None:
    """Every ask of a workstream got its first handle; the judges asked and were never refused."""
    assert finished.finished
    assert not finished.liveness_violations
    assert all(a.phase == AttemptPhase.TERMINAL for a in finished.core.attempts.attempts)
    assert all("handle_id" in reply for _, _, reply in asked), [r for *_, r in asked]
    by_attempt: dict[str, list[tuple[str, dict]]] = {}
    for attempt, role, reply in asked:
        by_attempt.setdefault(attempt, []).append((role, reply))
    for asks in by_attempt.values():
        assert len({reply["handle_id"] for _, reply in asks}) == 1, "one candidate, one handle"
        assert asks[0][1]["repeat"] is False
        assert all(reply["repeat"] for _, reply in asks[1:])


def assert_every_review_passed_round_has_its_metric(finished: SimulatedRun) -> None:
    """A round whose review passed was measured: no null metric, no failure."""
    state = dynamic_state(finished)
    rounds = [r for h in state.hypotheses for r in h.rounds]
    assert rounds
    for record in rounds:
        if record.review_passed:
            assert record.failure is None, record.failure
            assert record.metrics, "a passed review with no measured metric"


def test_a_judge_asking_for_the_implementers_evaluation_gets_its_handle() -> None:
    world = waiting_world(SHARED)
    finished = simulate(world)
    asked = world.hosting.asked

    assert_one_evaluation_per_workstream(finished, asked)
    assert_every_review_passed_round_has_its_metric(finished)
    judge_asks = [reply for _, role, reply in asked if role.endswith("judge")]
    assert judge_asks
    assert all(reply["repeat"] for reply in judge_asks)


_KNOBS = st.fixed_dictionaries(
    {
        "polls": st.lists(st.integers(0, 4), min_size=1, max_size=3).map(tuple),
        "delays": st.lists(st.integers(0, 4), min_size=1, max_size=3).map(tuple),
        "rounds": st.integers(1, 2),
        "in_flight": st.integers(1, 2),
        "waits": st.lists(st.booleans(), min_size=1, max_size=3).map(tuple),
    }
)


@settings(max_examples=10, deadline=None, derandomize=True, database=None)
@given(knobs=_KNOBS)
def test_judges_and_implementers_share_one_evaluation_whatever_runs_when(
    knobs: dict[str, Any],
) -> None:
    world = waiting_world(replace(SHARED, **knobs))
    finished = simulate(world)

    assert_one_evaluation_per_workstream(finished, world.hosting.asked)
    assert_every_review_passed_round_has_its_metric(finished)


@pytest.mark.parametrize("restarting", [False, True], ids=["live", "restart-every-step"])
@settings(max_examples=40, deadline=None, derandomize=True, database=None)
@given(
    picks=st.lists(st.integers(0, 5), max_size=40),
    rounds=st.integers(1, 2),
    in_flight=st.integers(1, 2),
)
def test_a_round_whose_review_passed_has_its_metric_whatever_runs_when(
    *, restarting: bool, picks: list[int], rounds: int, in_flight: int
) -> None:
    """The flow-level form of the test above, on the flow alone with a fake core.

    Which wait core answers next is generated. Sharing one evaluation per candidate is core's
    rule; the flow's part is that a passed review is never settled without its measurement.
    """
    host = begin(config(max_rounds=rounds, max_in_flight=in_flight), restarting=restarting)
    play(host, [(pick, 0) for pick in picks])

    assert_flow_closed(host)
    assert host.record is not None
    rounds_run = [row for item in host.record.hypotheses for row in item.rounds]
    assert rounds_run
    for row in rounds_run:
        if row.review_passed:
            assert row.failure is None, row.failure
            assert row.metrics, "a passed review with no measured metric"
