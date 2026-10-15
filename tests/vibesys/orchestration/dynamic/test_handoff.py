"""Each planning call that schedules a plan leaves one handoff file, which later planners read.

A planning call starts a new planner session, so what a planner meant is lost unless it is written
down. The search asks the handoff owner to write one file per scheduled planning call, from the
planner's reply; every later planning prompt lists the files' paths, newest first, and never their
contents. These tests play the search under a fake host whose owner keeps the files, live and with
restarts, and check the prompts the planner is asked.
"""

from __future__ import annotations

from hypothesis import given, note, settings
from hypothesis import strategies as st
from tests.vibesys.orchestration.dynamic._host import (
    PLANNER_LABEL,
    Host,
    begin,
    config,
    handoff_path,
)
from tests.vibesys.orchestration.dynamic._replies import expectation, implement, plan_reply
from tests.vibesys.orchestration.dynamic._slots import (
    implement_and_judge,
    open_next_slot,
    planner_context,
)
from tests.vibesys.orchestration.dynamic.core_policy._contexts import render
from tests.vibesys.search_faults import Step, play, steps
from tests.vibesys.vibesys_flows import across_restarts

from vibesys.orchestration.dynamic.search.api import (
    PlannerCorrection,
    PlannerPrompt,
    PromptTemplate,
    WriteHandoff,
)
from vs_core.api import FailureReason, Measure, Operation, RequestTurn, Wait
from vs_sim.api.testing import examples

MODES = across_restarts


def _written_newest_first(host: Host) -> tuple[str, ...]:
    """The paths of the files the owner holds, newest planning call first."""
    calls = sorted((request.call, path) for path, request in host.handoffs.items())
    return tuple(path for _, path in reversed(calls))


def _check_prompt(host: Host) -> None:
    """An open planner turn lists exactly the files written so far, newest first."""
    for asked in host.open(RequestTurn, label=PLANNER_LABEL):
        context = asked.context
        assert isinstance(context, PlannerPrompt | PlannerCorrection)
        assert context.handoff_paths == _written_newest_first(host), host.run.timeline()


def _scheduled_calls(host: Host) -> set[int]:
    """The planning calls whose plan the search scheduled: one handoff write was asked of each."""
    return {
        item.request.request.call
        for item in host.run.decisions
        if isinstance(item, Wait)
        and isinstance(item.request, Operation)
        and isinstance(item.request.request, WriteHandoff)
    }


@MODES
@settings(max_examples=examples(pr=10, nightly=60), deadline=None, derandomize=True, database=None)
@given(story=steps(), rounds=st.integers(1, 3), in_flight=st.integers(1, 2))
def test_every_planner_prompt_lists_every_earlier_handoff_file_newest_first(
    *, restarting: bool, story: list[Step], rounds: int, in_flight: int
) -> None:
    host = begin(config(max_rounds=rounds, max_in_flight=in_flight), restarting=restarting)
    try:
        play(host, story, check=lambda: _check_prompt(host))
    finally:
        note(host.run.timeline())
    # One file per scheduled call, named by the call; a restart never writes another one.
    assert set(host.handoffs) == {handoff_path(call) for call in _scheduled_calls(host)}
    assert host.handoff_writes == len(host.handoffs)


@MODES
@settings(max_examples=examples(pr=5, nightly=30), deadline=None)
@given(rounds=st.integers(2, 4), marker=st.from_regex(r"MARK[a-z0-9]{6}", fullmatch=True))
def test_each_planning_call_adds_one_file_and_the_next_prompt_names_it_but_never_its_contents(
    *, restarting: bool, rounds: int, marker: str
) -> None:
    host = begin(config(max_rounds=rounds, max_in_flight=1), restarting=restarting)
    for call in range(1, rounds + 1):
        host.drain_until_turn(PLANNER_LABEL)
        context = planner_context(host)
        assert isinstance(context, PlannerPrompt)
        listed = tuple(handoff_path(earlier) for earlier in range(call - 1, 0, -1))
        assert context.handoff_paths == listed
        prompt = render(PromptTemplate.PORTFOLIO, context)
        assert all(f"`{path}`" in prompt for path in listed)
        assert marker not in prompt  # what the files say stays in the files
        identifier = f"h{call}"
        note_of: dict[str, object] = {
            "expectations": [expectation(identifier, expected=f"{marker} {call}")],
            "doubts": [f"{marker} doubt {call}"],
        }
        reply = plan_reply(implement(identifier), reasoning=f"{marker} why", handoff=note_of)
        host.run.reply_raw(host.run.expect_turn(role=PLANNER_LABEL), reply)
        assert set(host.handoffs) == {handoff_path(n) for n in range(1, call + 1)}
        written = host.handoffs[handoff_path(call)]
        assert written.reasoning == f"{marker} why"
        assert [item.work_id for item in written.scheduled] == [identifier]
        assert written.handoff.doubts == (f"{marker} doubt {call}",)
        open_next_slot(host)
        implement_and_judge(host, identifier, f"c{call}")
        for asked in host.open(Measure):
            host.answer(asked)


@MODES
@settings(deadline=None)
@given(reason=st.sampled_from((FailureReason.TIMED_OUT, FailureReason.LOST, FailureReason.REFUSED)))
def test_a_failed_handoff_write_keeps_the_plan_and_lists_no_file(
    *, restarting: bool, reason: FailureReason
) -> None:
    host = begin(config(max_rounds=2), restarting=restarting, handoff_writes_open=True)
    host.drain_until_turn(PLANNER_LABEL)
    host.run.reply_raw(host.run.expect_turn(role=PLANNER_LABEL), plan_reply(implement("h1")))
    (write,) = [
        asked
        for asked in host.open(Operation)
        if isinstance(asked.request, Operation) and isinstance(asked.request.request, WriteHandoff)
    ]
    host.run.fail(write, reason)
    open_next_slot(host)
    implement_and_judge(host, "h1", "c1")
    for asked in host.open(Measure):
        host.answer(asked)
    host.drain_until_turn(PLANNER_LABEL)
    context = planner_context(host)
    assert isinstance(context, PlannerPrompt)
    assert context.handoff_paths == ()
    assert [row.hypothesis_id for row in context.history if hasattr(row, "hypothesis_id")] == ["h1"]
