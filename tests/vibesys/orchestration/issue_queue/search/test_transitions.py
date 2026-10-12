"""The first transitions of the search, taken one event at a time on a constructed view."""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st
from tests.vibesys.orchestration.issue_queue.search._decisions import works
from tests.vibesys.orchestration.issue_queue.search.world import FACTS, issue_queue_config

from vibesys.orchestration.issue_queue.search.api import Finished, IssueQueueSearch, Reading
from vs_core.api import (
    ControlId,
    ControlInput,
    DecisionId,
    Failed,
    FailureReason,
    Finish,
    Operation,
    RejectionCode,
    StopRequested,
    run_view,
)
from vs_core.testing.builders import initial_state
from vs_issue_tracker.api import ReadBoard

VIEW = run_view(initial_state())
CONFIG = issue_queue_config()
SEARCH = IssueQueueSearch(CONFIG)


def test_a_run_starts_by_reading_the_board() -> None:
    state, decisions = SEARCH.init(CONFIG, FACTS)
    (first,) = works(decisions)
    assert isinstance(first, Operation)
    assert isinstance(first.request, ReadBoard)
    assert first.decision_id == DecisionId(root="read.0")
    assert isinstance(state.run, Reading)


def test_an_operator_stop_before_anything_ran_finishes_the_run_cancelled() -> None:
    state, _ = SEARCH.init(CONFIG, FACTS)
    stop = StopRequested(control=ControlInput(control_id=ControlId(root="stop"), action="stop"))
    state, decisions = SEARCH.update(state, stop, VIEW)
    (final,) = works(decisions)
    assert isinstance(final, Finish)
    assert final.result.outcome == "cancelled"
    assert isinstance(state.run, Finished)


@given(code=st.sampled_from(list(RejectionCode)))
def test_a_board_read_core_refuses_ends_the_run_in_failure(code: RejectionCode) -> None:
    search = IssueQueueSearch(CONFIG)
    state, _ = search.init(CONFIG, FACTS)
    refused = Failed(reason=FailureReason.REFUSED, cause=code, decision=DecisionId(root="read.0"))
    state, decisions = search.update(state, refused, VIEW)
    (final,) = works(decisions)
    assert isinstance(final, Finish)
    assert final.result.outcome == "failure"
    assert isinstance(state.run, Finished)


def test_a_board_read_that_keeps_timing_out_is_sent_again_until_the_retries_are_spent() -> None:
    state, _ = SEARCH.init(CONFIG, FACTS)
    asked = 1
    final: Finish | None = None
    for number in range(CONFIG.max_board_retries + 2):
        timed_out = Failed(
            reason=FailureReason.TIMED_OUT, limit_at=1.0, decision=DecisionId(root=f"read.{number}")
        )
        state, decisions = SEARCH.update(state, timed_out, VIEW)
        (item,) = works(decisions)
        if isinstance(item, Finish):
            final = item
            break
        asked += 1
    assert final is not None
    assert final.result.outcome == "failure"
    assert asked == CONFIG.max_board_retries + 1


def test_an_event_for_a_decision_the_search_did_not_ask_is_not_answered() -> None:
    state, _ = SEARCH.init(CONFIG, FACTS)
    stray = Failed(reason=FailureReason.TIMED_OUT, decision=DecisionId(root="nobody"), limit_at=1.0)
    again, decisions = SEARCH.update(state, stray, VIEW)
    assert again == state
    assert decisions == []
