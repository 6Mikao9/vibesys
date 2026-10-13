"""The multi flow told as a story, by hand: no core, no clock, no event loop.

Each story plays the host of `multi`: it answers the waits the search opens in the order the story
chooses and reads what the search decided. Every story runs live and again with a restart after
every step: the decisions must not change. What core guarantees about these waits is core's own
tests; what is asserted here is only what the search decided.
"""

from __future__ import annotations

import pytest
from tests.vibesys.orchestration.multi._host import (
    IMPLEMENTER,
    JUDGE,
    PLANNER,
    PROFILER,
    RECIPE,
    ROUTER,
    MultiHost,
    config,
    implemented,
    profiled,
    reviewed,
    routed,
)
from tests.vibesys.serial_search._host import assert_flow_closed
from tests.vibesys.serial_search._replies import plan_reply

from vs_core.api import (
    CheckpointDecline,
    Declined,
    FailureReason,
    Finish,
    Retained,
    RevisionRef,
)

MODES = pytest.mark.parametrize("restarting", [False, True], ids=["live", "restart-every-step"])


def start(
    selected: object = None, *, restarting: bool, validating: bool = False, **world: object
) -> MultiHost:
    """Run the input's measurement up to the first design turn."""
    host = MultiHost.start(
        selected or config(), restarting=restarting, validating=validating, **world
    )  # ty: ignore[invalid-argument-type]
    host.drain_until_turn(PLANNER)
    return host


def design(host: MultiHost, identifier: str = "h1") -> None:
    host.run.reply_raw(host.run.expect_turn(role=PLANNER), plan_reply(identifier))


def implement(host: MultiHost, outcome: str = "nominated", candidate: str = "def456") -> None:
    host.drain_until_turn(IMPLEMENTER)
    host.run.reply(
        host.run.expect_turn(role=IMPLEMENTER),
        implemented(outcome),
        checkpoint=Retained(revision=RevisionRef.of_git_commit(candidate)),
    )


def judge(host: MultiHost, *, passed: bool) -> None:
    host.drain_until_turn(JUDGE)
    host.run.reply(host.run.expect_turn(role=JUDGE), reviewed(passed=passed))


def selection_of(host: MultiHost) -> tuple[str, str | None]:
    finish = host.run.finished
    assert isinstance(finish, Finish)
    chosen = finish.result.selection
    assert chosen is not None, finish.result
    commit = chosen.revision.git_commit if chosen.kind == "retained_candidate" else None
    return chosen.kind, commit


@MODES
def test_a_candidate_the_judge_passes_and_the_gates_confirm_is_adopted(*, restarting: bool) -> None:
    host = start(restarting=restarting)
    design(host)
    implement(host)
    judge(host, passed=True)
    host.drain()

    assert selection_of(host) == ("retained_candidate", "def456")
    record = host.record
    assert record is not None
    assert [row.passed for row in record.hypotheses[0].rounds] == [True]
    assert_flow_closed(host)


@MODES
def test_a_candidate_the_judge_rejects_is_never_adopted(*, restarting: bool) -> None:
    host = start(restarting=restarting)
    design(host)
    implement(host)
    judge(host, passed=False)
    host.drain()

    assert selection_of(host) == ("trusted_baseline", None)
    assert_flow_closed(host)


@MODES
def test_a_judge_that_cannot_be_reached_leaves_the_candidate_ineligible(
    *, restarting: bool
) -> None:
    """Dynamic reads a missing review as a pass; multi needs a pass to adopt anything."""
    host = start(restarting=restarting)
    design(host)
    implement(host)
    host.drain_until_turn(JUDGE)
    host.run.fail(host.run.expect_turn(role=JUDGE), FailureReason.TIMED_OUT)
    host.drain()

    assert selection_of(host) == ("trusted_baseline", None)
    assert_flow_closed(host)


@MODES
def test_a_judge_rejection_gets_the_implementer_another_turn(*, restarting: bool) -> None:
    host = start(config(max_retries_per_round=2), restarting=restarting)
    design(host)
    implement(host)
    judge(host, passed=False)
    implement(host, candidate="c2")
    judge(host, passed=True)
    host.drain()

    assert len(host.opened) == 1
    assert selection_of(host) == ("retained_candidate", "c2")
    assert_flow_closed(host)


@MODES
def test_a_blocked_implementer_ends_the_round_and_nothing_is_adopted(*, restarting: bool) -> None:
    host = start(restarting=restarting)
    design(host)
    host.drain_until_turn(IMPLEMENTER)
    host.run.reply(host.run.expect_turn(role=IMPLEMENTER), implemented("blocked"))
    host.drain()

    assert selection_of(host) == ("trusted_baseline", None)
    assert_flow_closed(host)


@MODES
def test_a_candidate_that_does_not_beat_the_input_is_not_adopted(*, restarting: bool) -> None:
    host = start(restarting=restarting, throughputs={"c1": 10.0})
    design(host)
    implement(host, candidate="c1")
    judge(host, passed=True)
    host.drain()

    assert selection_of(host) == ("trusted_baseline", None)
    assert_flow_closed(host)


@MODES
def test_a_validation_recipe_that_fails_is_told_to_the_implementer_not_adopted(
    *, restarting: bool
) -> None:
    host = start(
        config(max_retries_per_round=2), restarting=restarting, validating=True, validation="failed"
    )
    design(host)
    host.drain_until_turn(IMPLEMENTER)
    host.run.reply(
        host.run.expect_turn(role=IMPLEMENTER),
        implemented(validation_recipe_artifact=RECIPE),
        checkpoint=Retained(revision=RevisionRef.of_git_commit("c1")),
    )
    judge(host, passed=True)
    host.drain_until_turn(IMPLEMENTER)
    again = host.run.expect_turn(role=IMPLEMENTER)
    assert getattr(again.context, "validation_failure", None) is not None


@MODES
def test_the_router_may_ask_for_a_profile_and_the_profiler_is_asked_before_the_design(
    *, restarting: bool
) -> None:
    host = MultiHost.start(config(profiling=True), restarting=restarting)
    host.drain_until_turn(ROUTER)
    host.run.reply(host.run.expect_turn(role=ROUTER), routed(need_profile=True, focus="gemm"))
    host.drain_until_turn(PROFILER)
    host.run.reply(host.run.expect_turn(role=PROFILER), profiled())
    host.drain_until_turn(PLANNER)
    design(host)
    implement(host)
    judge(host, passed=True)
    host.drain()

    assert selection_of(host) == ("retained_candidate", "def456")
    assert_flow_closed(host)


@MODES
def test_a_router_that_fails_does_not_stop_the_round(*, restarting: bool) -> None:
    host = MultiHost.start(config(profiling=True), restarting=restarting)
    host.drain_until_turn(ROUTER)
    host.run.fail(host.run.expect_turn(role=ROUTER), FailureReason.TIMED_OUT)
    host.drain_until_turn(PLANNER)
    design(host)
    implement(host)
    judge(host, passed=True)
    host.drain()

    assert selection_of(host) == ("retained_candidate", "def456")
    assert_flow_closed(host)


@MODES
@pytest.mark.parametrize("baseline", ["failed-workload", "no-evidence", "lost"])
def test_an_input_that_cannot_be_measured_is_not_hidden(*, restarting: bool, baseline: str) -> None:
    host = MultiHost.start(config(), restarting=restarting, baseline=baseline)  # ty: ignore[invalid-argument-type]
    host.drain(limit=60)
    assert host.run.finished is not None or host.run.open


@MODES
@pytest.mark.parametrize("reason", list(CheckpointDecline))
def test_a_checkpoint_core_declines_leaves_the_round_without_a_candidate(
    *, restarting: bool, reason: CheckpointDecline
) -> None:
    host = start(restarting=restarting)
    design(host)
    host.drain_until_turn(IMPLEMENTER)
    host.run.reply(
        host.run.expect_turn(role=IMPLEMENTER), implemented(), checkpoint=Declined(reason=reason)
    )
    host.drain()

    assert selection_of(host) == ("trusted_baseline", None)
    assert_flow_closed(host)


@MODES
def test_an_implementer_that_asks_to_continue_is_given_its_own_next_step_without_a_new_design(
    *, restarting: bool
) -> None:
    host = start(config(max_rounds=2), restarting=restarting)
    design(host)
    host.drain_until_turn(IMPLEMENTER)
    host.run.reply(
        host.run.expect_turn(role=IMPLEMENTER),
        implemented("continue", next_step="try the fused kernel"),
        checkpoint=Retained(revision=RevisionRef.of_git_commit("c1")),
    )
    judge(host, passed=True)
    host.drain_until_turn(IMPLEMENTER)

    prompt = host.run.expect_turn(role=IMPLEMENTER).context
    assert getattr(prompt, "continuation_step", None) == "try the fused kernel"
    assert not [item for item in host.run.open if item.label == PLANNER]
    record = host.record
    assert record is not None
    assert [item.hypothesis_id for item in record.hypotheses] == ["h1"]
