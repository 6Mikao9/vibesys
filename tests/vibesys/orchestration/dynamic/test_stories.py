"""The dynamic flow told as a story, by hand: no core, no clock, no event loop.

Each story plays the host for `dynamic`: it answers the waits the search opens, in an order the
story chooses, and reads what the search decided. Every story runs twice, the second time with a
restart after every step: the search's state is round-tripped through JSON and the search rebuilt
by replay, and the decisions must not change.
"""

from __future__ import annotations

import pytest
from tests.vibesys.orchestration.dynamic._host import (
    PLANNER_LABEL,
    Host,
    begin,
    config,
)
from tests.vibesys.orchestration.dynamic._replies import (
    implement,
    implemented,
    plan_reply,
    reviewed,
)

from vibesys.orchestration.dynamic.models import ImplementerResult, ReviewResult
from vibesys.orchestration.dynamic.search.api import FailureKind
from vs_core.api import (
    Finish,
    Retained,
    RevisionRef,
    StartAttempt,
    Withdraw,
)

CONFIG = config()
CANDIDATE = RevisionRef.of_git_commit("def456")
MODES = pytest.mark.parametrize("restarting", [False, True], ids=["live", "restart-every-step"])


def start(*, restarting: bool) -> Host:
    """Run the baseline measurement the root begins with, up to the first planner turn."""
    host = begin(CONFIG, restarting=restarting)
    host.drain_until_turn(PLANNER_LABEL)
    return host


def plan(host: Host, body: str) -> None:
    host.run.reply_raw(host.run.expect_turn(role=PLANNER_LABEL), body)


def implement_and_review(host: Host, *, passed: bool = True) -> None:
    result = ImplementerResult.model_validate_json(implemented())
    host.run.reply(
        host.run.expect_turn(role="implementer"), result, checkpoint=Retained(revision=CANDIDATE)
    )
    verdict = ReviewResult.model_validate_json(reviewed(passed=passed))
    host.run.reply(host.run.expect_turn(role="judge"), verdict)


@MODES
def test_one_hypothesis_from_planning_to_the_adoption_of_its_candidate(*, restarting: bool) -> None:
    host = start(restarting=restarting)
    plan(host, plan_reply(implement("h1")))
    host.answer(host.run.expect(StartAttempt, label="start"))
    implement_and_review(host)
    host.drain()

    finish = host.run.finished
    assert isinstance(finish, Finish)
    assert finish.result.outcome == "success"
    assert finish.result.selection.revision == CANDIDATE  # ty: ignore[unresolved-attribute]
    record = host.record
    assert record is not None
    assert [item.hypothesis_id for item in record.hypotheses] == ["h1"]
    assert [row.eligible for row in record.hypotheses[0].rounds] == [True]


@MODES
def test_a_stop_while_the_implementer_works_settles_the_attempt_cancelled(
    *, restarting: bool
) -> None:
    host = start(restarting=restarting)
    plan(host, plan_reply(implement("h1")))
    host.answer(host.run.expect(StartAttempt, label="start"))
    host.run.expect_turn(role="implementer")

    host.run.stop()
    settle = host.run.expect(Withdraw, label="settle")
    host.drain()

    assert isinstance(settle.request, Withdraw)
    assert settle.request.disposition.outcome == "cancelled"  # ty: ignore[unresolved-attribute]
    assert isinstance(host.run.finished, Finish)
    assert not host.unsettled
    record = host.record
    assert record is not None
    assert record.finished[0].failure is not None
    assert record.finished[0].failure.kind is FailureKind.STOPPED


@MODES
def test_a_lost_planner_turn_is_asked_again(*, restarting: bool) -> None:
    host = start(restarting=restarting)
    host.run.lose(host.run.expect_turn(role=PLANNER_LABEL))
    host.drain_until_turn(PLANNER_LABEL)
    plan(host, plan_reply(implement("h1")))
    host.answer(host.run.expect(StartAttempt, label="start"))
    assert host.opened


@MODES
def test_a_plan_accepted_after_a_stop_is_recorded_stopped_and_opens_no_attempt(
    *, restarting: bool
) -> None:
    host = start(restarting=restarting)
    asked = host.run.expect_turn(role=PLANNER_LABEL)
    host.run.stop()
    host.run.reply_raw(asked, plan_reply(implement("h1")))

    assert not [item for item in host.run.open if isinstance(item.request, StartAttempt)]
    record = host.record
    assert record is not None
    assert not record.running
    host.drain()
    assert isinstance(host.run.finished, Finish)
