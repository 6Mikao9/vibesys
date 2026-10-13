"""Stories of the issue-queue flow, each run live and with a restart after every step."""

from __future__ import annotations

import pytest

from tests.vibesys.orchestration.issue_queue._host import (
    FACTS,
    IMPLEMENTER,
    JUDGE,
    UNGATED,
    Host,
    assert_flow_closed,
    begin,
    config,
)
from tests.vibesys.orchestration.issue_queue._replies import evaluated, generic_evaluated, implemented, judged

from vs_core.api import FailureReason, Measure, RejectionCode, Withdraw
from vs_core.api.testing import Asked, FakeView

SUSPENDING = FakeView(
    changes={
        "capabilities": FakeView().current().capabilities.model_copy(update={"lifecycle": frozenset({"suspend"})})
    }
)
"""A host whose agents may end a turn waiting for their evaluations."""
RESTARTING = pytest.mark.parametrize("restarting", [False, True], ids=["live", "restarting"])


def outcome(host: Host) -> str:
    """How the run ended."""
    assert host.run.finished is not None, host.run.timeline()
    return host.run.finished.result.outcome


@RESTARTING
def test_one_issue_is_closed_and_adopted(restarting: bool) -> None:
    host = begin(config(), restarting=restarting)
    host.drain()
    assert_flow_closed(host)
    assert outcome(host) == "success"
    assert host.board() == {1: ("closed", 1)}
    assert [item.digest for item in host.measured] == ["git-commit:c1"], "the proof"
    assert [item.outcome for item in host.settled.values()] == ["succeeded", "succeeded"]


@RESTARTING
def test_a_task_without_accuracy_needs_no_proof(restarting: bool) -> None:
    host = begin(config(), restarting=restarting, facts=UNGATED)
    host.drain()
    assert_flow_closed(host)
    assert outcome(host) == "success"
    assert host.measured == []


@RESTARTING
def test_a_rejected_attempt_is_retried_with_the_judges_feedback(restarting: bool) -> None:
    host = begin(config(), restarting=restarting, judge=[judged(passed=False), judged()])
    host.drain()
    assert_flow_closed(host)
    assert outcome(host) == "success"
    assert host.board() == {1: ("closed", 2)}


@RESTARTING
def test_an_issue_out_of_attempts_is_blocked_and_the_input_stands(restarting: bool) -> None:
    host = begin(
        config(), restarting=restarting, judge=[judged(passed=False), judged(passed=False)]
    )
    host.drain()
    assert_flow_closed(host)
    assert outcome(host) == "failure"
    assert host.board() == {1: ("blocked", 2)}
    assert host.measured == []


@RESTARTING
def test_a_bug_the_judge_files_is_worked_next(restarting: bool) -> None:
    host = begin(config(), restarting=restarting, judge=[judged(bugs=("crash on empty",))])
    host.drain()
    assert_flow_closed(host)
    assert outcome(host) == "success"
    assert "crash on empty" in host.titles()
    assert set(host.statuses().values()) == {"closed"}


@RESTARTING
def test_issues_the_evaluator_files_are_worked_in_the_next_round(restarting: bool) -> None:
    host = begin(config(), restarting=restarting, perf=[evaluated("faster", "leaner")])
    host.drain()
    assert_flow_closed(host)
    assert outcome(host) == "success"
    assert {"faster", "leaner"} <= set(host.titles())
    assert set(host.statuses().values()) == {"closed"}


@RESTARTING
def test_a_spent_round_budget_still_adopts_what_was_built(restarting: bool) -> None:
    host = begin(config(max_rounds=1), restarting=restarting, perf=[evaluated("more")])
    host.drain()
    assert_flow_closed(host)
    assert outcome(host) == "success"
    assert host.statuses()["more"] == "open"
    assert host.measured, "the built tip was proven and adopted"


@RESTARTING
def test_an_unchanged_candidate_adopts_nothing(restarting: bool) -> None:
    host = begin(config(), restarting=restarting, unchanged=True)
    host.drain()
    assert_flow_closed(host)
    assert host.measured == []
    assert host.board() == {1: ("closed", 1)}


@RESTARTING
def test_a_stop_mid_job_settles_the_attempt_and_adopts_nothing(restarting: bool) -> None:
    host = begin(config(), restarting=restarting)
    host.drain_until_turn(IMPLEMENTER)
    host.run.stop()
    host.drain()
    assert_flow_closed(host)
    assert host.measured == []
    assert [item.outcome for item in host.settled.values()] == ["cancelled"]


@RESTARTING
def test_a_reply_that_is_not_json_is_corrected(restarting: bool) -> None:
    host = begin(config(), restarting=restarting)
    host.drain_until_turn(IMPLEMENTER)
    (turn,) = [item for item in host.run.open if item.label == IMPLEMENTER]
    host.answer(turn, variant=1)
    host.drain()
    assert_flow_closed(host)
    assert outcome(host) == "success"
    assert host.board() == {1: ("closed", 1)}


@RESTARTING
def test_a_lost_board_acknowledgement_is_sent_again_and_lands_once(restarting: bool) -> None:
    host = begin(config(), restarting=restarting, lost_acknowledgements=frozenset({0, 1, 2}))
    host.drain()
    assert_flow_closed(host)
    assert outcome(host) == "success"
    assert host.board() == {1: ("closed", 1)}


@RESTARTING
def test_a_board_that_never_answers_ends_the_run(restarting: bool) -> None:
    host = begin(config(), restarting=restarting, dropped_writes=frozenset(range(50)))
    host.drain()
    assert_flow_closed(host)
    assert outcome(host) == "failure"


@RESTARTING
def test_an_implementer_that_cannot_be_checkpointed_fails_the_job(restarting: bool) -> None:
    host = begin(config(), restarting=restarting)
    host.drain_until_turn(IMPLEMENTER)
    (turn,) = [item for item in host.run.open if item.label == IMPLEMENTER]
    host.answer(turn, variant=5)
    host.drain()
    assert_flow_closed(host)
    assert outcome(host) == "failure"
    assert implemented().summary


def turn_of(host: Host, role: str) -> Asked:
    """The one open turn of ``role``."""
    (asked,) = [item for item in host.run.open if item.label == role]
    return asked


@RESTARTING
@pytest.mark.parametrize("role", [IMPLEMENTER, JUDGE])
def test_an_agent_that_waits_for_its_evaluations_is_resumed_and_the_issue_closes(
    *, restarting: bool, role: str
) -> None:
    host = begin(config(), restarting=restarting, view=SUSPENDING)
    host.drain_until_turn(role)
    host.run.resume_ready(host.run.suspend(turn_of(host, role)))
    host.drain()
    assert_flow_closed(host)
    assert outcome(host) == "success"
    assert host.board() == {1: ("closed", 1)}


@RESTARTING
def test_a_reply_that_stays_invalid_ends_the_run_in_failure_with_nothing_adopted(
    restarting: bool,
) -> None:
    host = begin(config(), restarting=restarting)
    host.drain_until_turn(JUDGE)
    for _ in range(4):
        if not [item for item in host.run.open if item.label == JUDGE]:
            break
        host.run.reply_raw(turn_of(host, JUDGE), "not json")
    host.drain()
    assert_flow_closed(host)
    assert outcome(host) == "failure"
    assert host.measured == []


@RESTARTING
def test_no_more_issues_than_the_round_cap_are_worked_before_the_evaluation(
    restarting: bool,
) -> None:
    host = begin(
        config(max_issues_per_round=1, max_rounds=1),
        restarting=restarting,
        judge=[judged(bugs=("one more",))],
    )
    host.drain()
    assert_flow_closed(host)
    assert host.statuses() == {host.titles()[0]: "closed", "one more": "open"}


@RESTARTING
def test_a_task_without_an_objective_fails_the_run_instead_of_seeding_an_unrelated_issue(
    restarting: bool,
) -> None:
    blank = FACTS.model_copy(update={"objective": "  "})
    host = begin(config(), restarting=restarting, facts=blank)
    host.drain()
    assert_flow_closed(host)
    assert outcome(host) == "failure"
    assert host.titles() == []


@RESTARTING
@pytest.mark.parametrize("code", list(RejectionCode))
def test_a_settlement_core_refuses_ends_the_job_instead_of_stranding_it(
    *, restarting: bool, code: RejectionCode
) -> None:
    host = begin(config(), restarting=restarting)
    host.drain_until_turn(JUDGE)
    host.answer(turn_of(host, JUDGE))
    for asked in [item for item in host.run.open if isinstance(item.request, Withdraw)]:
        host.run.fail(asked, FailureReason.REFUSED, cause=code)
    host.drain()
    assert_flow_closed(host)


@RESTARTING
def test_a_generic_domain_evaluation_is_accepted(restarting: bool) -> None:
    host = begin(config(), restarting=restarting, perf=[generic_evaluated()])
    host.drain()
    assert_flow_closed(host)
    assert outcome(host) == "success"
