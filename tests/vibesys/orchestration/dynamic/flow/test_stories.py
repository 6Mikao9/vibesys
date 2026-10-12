"""The dynamic flow told as a story, by hand: no core, no clock, no event loop.

Each story plays the host for `dynamic_search`: it answers the waits the search opens, in an
order the story chooses, and reads what the search decided. Every story runs twice, the second
time with a restart after every step: the search's state is round-tripped through JSON and the
search rebuilt by replay, and the decisions must not change.
"""

from __future__ import annotations

import pytest
from tests.vibesys.orchestration.dynamic.search._replies import (
    implement,
    implemented,
    plan_reply,
    reviewed,
)
from tests.vibesys.orchestration.dynamic.search._run import FACTS, config

from vibesys.orchestration.dynamic.flow.api import dynamic_search, published
from vibesys.orchestration.dynamic.models import (
    ImplementerResult,
    ReviewResult,
    planner_reply_type,
)
from vibesys.orchestration.dynamic.search.api import OPERATIONS, FailureKind
from vs_core.api import (
    AskAgent,
    AttemptRef,
    Failed,
    FailureReason,
    Finish,
    InvocationId,
    InvocationRef,
    ProposeWinner,
    RejectionCode,
    Retained,
    RevisionRef,
    SessionId,
    Settlement,
    SettlementId,
    StartAttempt,
    Withdraw,
    WorkspaceClosed,
    WorkspaceOpened,
)
from vs_core.api.testing import FlowRun

CONFIG = config(accuracy_configured=False, benchmark_configured=False)
CANDIDATE = RevisionRef.of_git_commit("def456")
MODES = pytest.mark.parametrize("restarting", [False, True], ids=["live", "restart-every-step"])


def begin(*, restarting: bool) -> FlowRun[object]:
    run = FlowRun.start(dynamic_search(CONFIG), CONFIG, FACTS, restarting=restarting)
    run.registry = OPERATIONS.registry()
    return run


def plan(run: FlowRun[object], body: str) -> None:
    reply = planner_reply_type(CONFIG.profile_offer).model_validate_json(body)
    run.reply(run.expect(AskAgent, label="planner"), reply)


def open_attempt(run: FlowRun[object]) -> AttemptRef:
    start = run.expect(StartAttempt, label="start")
    assert isinstance(start.request, StartAttempt)
    attempt = AttemptRef(attempt_id=start.request.attempt_id, generation=0)
    run.answer(start, WorkspaceOpened(decision=start.decision, attempt=attempt))
    return attempt


def implement_and_review(run: FlowRun[object], *, passed: bool = True) -> None:
    result = ImplementerResult.model_validate_json(implemented())
    run.reply(
        run.expect(AskAgent, label="implementer"), result, checkpoint=Retained(revision=CANDIDATE)
    )
    verdict = ReviewResult.model_validate_json(reviewed(passed=passed))
    run.reply(run.expect(AskAgent, label="judge"), verdict)


def close(run: FlowRun[object], attempt: AttemptRef) -> None:
    settle = run.expect(Withdraw, label="settle")
    settlement = Settlement(
        settlement_id=SettlementId(root="s1"),
        attempt=attempt,
        candidate=CANDIDATE,
        assessments=(),
        eligible=True,
        retention="candidate",
        outcome="succeeded",
    )
    run.answer(settle, WorkspaceClosed(decision=settle.decision, settlement=settlement))


@MODES
def test_one_hypothesis_from_planning_to_the_proposal_of_its_candidate(*, restarting: bool) -> None:
    run = begin(restarting=restarting)
    plan(run, plan_reply(implement("h1")))
    attempt = open_attempt(run)
    implement_and_review(run)
    close(run, attempt)

    proposal = run.expect(ProposeWinner)
    assert isinstance(proposal.request, ProposeWinner)
    assert proposal.request.selection.revision == CANDIDATE
    record = published(run.state)
    assert record is not None
    assert [item.hypothesis_id for item in record.hypotheses] == ["h1"]
    assert [row.eligible for row in record.hypotheses[0].rounds] == [True]


@MODES
def test_a_stop_while_the_implementer_works_settles_the_attempt_and_ends_cancelled(
    *,
    restarting: bool,
) -> None:
    run = begin(restarting=restarting)
    plan(run, plan_reply(implement("h1")))
    attempt = open_attempt(run)
    run.expect(AskAgent, label="implementer")

    run.stop()
    settle = run.expect(Withdraw, label="settle")
    close(run, attempt)
    run.fail(run.expect(ProposeWinner), FailureReason.REFUSED)

    assert isinstance(settle.request, Withdraw)
    assert settle.request.disposition.outcome == "cancelled"
    assert isinstance(run.finished, Finish)
    assert run.finished.result.outcome == "cancelled"
    record = published(run.state)
    assert record is not None
    assert record.finished[0].failure is not None
    assert record.finished[0].failure.kind is FailureKind.STOPPED


@MODES
def test_a_lost_planner_turn_is_asked_again_after_its_backoff(*, restarting: bool) -> None:
    run = begin(restarting=restarting)
    asked = run.expect(AskAgent, label="planner")
    lost = InvocationRef(
        session_id=SessionId(root="session"), invocation_id=InvocationId(root="turn"), generation=0
    )
    run.fail(asked, FailureReason.LOST, turn=lost)

    (wake,) = run.sleeping_until
    assert wake == CONFIG.backoff.delay(0)
    assert not [item for item in run.open if item.label == "planner"]
    run.advance_to(wake)
    plan(run, plan_reply(implement("h1")))
    open_attempt(run)


@MODES
def test_a_refused_result_is_proposed_again_as_the_plain_failure(*, restarting: bool) -> None:
    run = begin(restarting=restarting)
    plan(run, plan_reply(implement("h1")))
    attempt = open_attempt(run)
    implement_and_review(run, passed=False)
    close(run, attempt)
    run.fail(run.expect(ProposeWinner), FailureReason.REFUSED)
    first = run.finished
    assert isinstance(first, Finish)

    run.send(
        Failed(reason=FailureReason.REFUSED, decision=first.decision_id, cause=RejectionCode.BUDGET)
    )

    assert isinstance(run.finished, Finish)
    assert run.finished.decision_id != first.decision_id
    assert run.finished.result.outcome == "failure"
