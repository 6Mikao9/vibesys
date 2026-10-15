"""The planner works while the input is measured; nothing compared with the input runs before it.

On a shared cluster the input's trusted measurement may wait hours for a slot. The search asks the
planner at once, telling it the measurement is pending, and holds what it schedules: no workstream
starts, no candidate is measured and no candidate is named the winner before the input's
measurement lands. A planning call that ends empty meanwhile is not a stuck planner: planning is
asked again when the measurement lands. A stop before it lands drops the held plans and leaves the
input standing. How each measurement outcome ends the run is unchanged
(`test_unmeasurable_input.py` plays it in both orders).
"""

from __future__ import annotations

from tests.vibesys.orchestration.dynamic._host import FACTS, PLANNER_LABEL, Host, begin, config
from tests.vibesys.orchestration.dynamic._replies import implement, plan_reply
from tests.vibesys.orchestration.dynamic.core_policy._contexts import render
from tests.vibesys.search_faults import assert_flow_closed
from tests.vibesys.vibesys_flows import across_restarts

from vibesys.orchestration.dynamic.search.api import PlannerPrompt, PromptTemplate
from vs_core.api import FailureReason, Finish, Measure, RequestTurn, StartAttempt, Wait

MODES = across_restarts
MAX_EMPTY_ASKS = 8
"""More planner turns than one planning call asks before it ends empty (corrections, a fresh ask)."""


def _starts(host: Host) -> int:
    return sum(
        isinstance(item, Wait) and isinstance(item.request, StartAttempt)
        for item in host.run.decisions
    )


def _candidate_measurements(host: Host) -> int:
    return sum(
        isinstance(item, Wait)
        and isinstance(item.request, Measure)
        and item.request.plan.purpose != "baseline"
        for item in host.run.decisions
    )


@MODES
def test_the_planner_is_asked_before_a_slow_input_measurement_lands(*, restarting: bool) -> None:
    host = begin(config(max_in_flight=2, max_rounds=2), restarting=restarting, input_first=False)

    # The first planner turn is open while the input's measurement is.
    first = host.run.expect_turn(role=PLANNER_LABEL).context
    (measuring,) = host.input_waits()
    assert isinstance(measuring.request, Measure)
    assert measuring.request.plan.purpose == "baseline"
    assert isinstance(first, PlannerPrompt)
    assert first.baseline_pending
    assert first.baseline == ()
    assert first.input_failure is None
    assert "still being measured" in render(PromptTemplate.PORTFOLIO, first)

    # What the planner schedules waits for the input, and no second call is made for its slots.
    host.run.reply_raw(
        host.run.expect_turn(role=PLANNER_LABEL), plan_reply(implement("h1"), implement("h2"))
    )
    assert host.input_waits()
    assert not host.open(StartAttempt)
    assert not host.open(RequestTurn, label=PLANNER_LABEL)
    assert _starts(host) == _candidate_measurements(host) == 0
    assert host.run.finished is None

    # When the measurement lands, the held plans start, built on the input.
    host.measure_input()
    starts = host.open(StartAttempt)
    assert len(starts) == 2
    for item in starts:
        assert isinstance(item.request, StartAttempt)
        assert item.request.workspace.base == FACTS.baseline
    host.drain()
    assert isinstance(host.run.finished, Finish)
    assert_flow_closed(host)


@MODES
def test_a_planning_call_that_ends_empty_before_the_input_lands_is_asked_again_after_it(
    *, restarting: bool
) -> None:
    host = begin(config(), restarting=restarting, input_first=False)
    for _ in range(MAX_EMPTY_ASKS):
        turns = host.open(RequestTurn, label=PLANNER_LABEL)
        if not turns:
            break
        host.run.fail(turns[0], FailureReason.TIMED_OUT)
    else:
        message = f"the planning call never ended: {host.run.timeline()}"
        raise AssertionError(message)

    # Nothing in flight but the input's measurement: the planner is not given up on.
    assert host.input_waits()
    assert host.run.finished is None
    host.measure_input()
    later = host.run.expect_turn(role=PLANNER_LABEL).context
    assert isinstance(later, PlannerPrompt)
    assert not later.baseline_pending
    assert later.baseline != ()


@MODES
def test_a_stop_before_the_input_lands_starts_nothing_and_leaves_the_input_standing(
    *, restarting: bool
) -> None:
    host = begin(config(max_in_flight=2), restarting=restarting, input_first=False)
    host.run.reply_raw(host.run.expect_turn(role=PLANNER_LABEL), plan_reply(implement("h1")))
    host.run.stop()
    host.drain()

    assert _starts(host) == _candidate_measurements(host) == 0
    finish = host.run.finished
    assert isinstance(finish, Finish)
    assert finish.result.selection is not None
    assert finish.result.selection.kind == "trusted_baseline"
    assert_flow_closed(host)
