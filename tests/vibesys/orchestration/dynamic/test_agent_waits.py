"""Agents of the dynamic search that wait for their own evaluations, or are lost, still end the run.

The implementer and the judge may end a turn waiting for an evaluation they submitted; core
suspends the turn and resumes it once the evaluation settles. A lost turn is asked again after
a growing wait on the run clock. Which roles may do either, and how the workstream goes on after
the resume, is the dynamic search's; what core does between (record the wait, authorize the
resume, charge the turns) is core's own. Every story runs live and with a restart after every
step.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from tests.vibesys.orchestration.dynamic._host import (
    PLANNER_LABEL,
    Host,
    assert_flow_closed,
    begin,
    config,
)
from tests.vibesys.orchestration.dynamic._replies import (
    implement,
    implemented,
    plan_reply,
    profile,
    reviewed,
)

from vibesys.orchestration.dynamic.models import ImplementerResult, ReviewResult
from vibesys.orchestration.dynamic.search.api import ROLES, dynamic
from vs_core.api import (
    Capabilities,
    CheckpointDecline,
    CheckpointKnown,
    Continuation,
    Declined,
    Finish,
    InvocationRef,
    LifecycleCapability,
    RequestTurn,
    Retained,
    RevisionRef,
    Settle,
    StartAttempt,
    Wait,
    Withdraw,
)
from vs_core.api.testing import Asked, FakeView

MODES = pytest.mark.parametrize("restarting", [False, True], ids=["live", "restart-every-step"])
CANDIDATE = RevisionRef.of_git_commit("abc123")
LIFECYCLE: frozenset[LifecycleCapability] = frozenset({"suspend", "profile-capture"})


def offering() -> FakeView:
    """A host that can suspend a turn that waits and capture profiles."""
    return FakeView(changes={"capabilities": Capabilities(lifecycle=LIFECYCLE)})


def start(*, restarting: bool, **settings: object) -> Host:
    host = begin(config(**settings), restarting=restarting, view=offering())
    host.drain_until_turn(PLANNER_LABEL)
    return host


def to_open_slot(host: Host, *entries: dict[str, object]) -> None:
    host.run.reply_raw(host.run.expect_turn(role=PLANNER_LABEL), plan_reply(*entries))
    host.answer(host.run.expect(StartAttempt, label="start"))


def invocation_of(asked: Asked) -> InvocationRef:
    request = asked.request
    assert isinstance(request, RequestTurn)
    return InvocationRef(
        session_id=request.turn.session.session_id,
        invocation_id=request.turn.invocation_id,
        generation=request.scope.generation,
    )


def charge_of(asked: Asked) -> str:
    request = asked.request
    assert isinstance(request, RequestTurn)
    return request.turn.charge_class


def implementer_reply(host: Host, *, checkpoint: Retained | None) -> None:
    result = ImplementerResult.model_validate_json(implemented())
    host.run.reply(host.run.expect_turn(role="implementer"), result, checkpoint=checkpoint)


def judge_reply(host: Host, *, passed: bool = True) -> None:
    verdict = ReviewResult.model_validate_json(reviewed(passed=passed))
    host.run.reply(host.run.expect_turn(role="judge"), verdict)


# -- a judge that waits ---------------------------------------------------------------------


@MODES
def test_a_judge_that_waits_for_its_own_evaluation_is_resumed_and_the_run_adopts(
    *, restarting: bool
) -> None:
    """The v12 stall: the judge, a read-only turn, ended waiting and nothing resumed it."""
    host = start(restarting=restarting)
    to_open_slot(host, implement("h1"))
    implementer_reply(host, checkpoint=Retained(revision=CANDIDATE))

    waiting = host.run.suspend(host.run.expect_turn(role="judge"))
    assert not host.open(RequestTurn, label="judge")
    host.run.resume_ready(waiting)
    resumed = host.run.expect_turn(role="judge")
    assert charge_of(resumed) == "resume"
    judge_reply(host)
    host.drain()

    finish = host.run.finished
    assert isinstance(finish, Finish)
    assert finish.result.outcome == "success"
    assert finish.result.selection.revision == CANDIDATE  # ty: ignore[unresolved-attribute]
    assert_flow_closed(host)


@MODES
def test_an_implementer_resumed_after_waiting_keeps_the_candidate_core_retained_for_it(
    *, restarting: bool
) -> None:
    """The resumed turn only reads the verdict, so the candidate is the suspended turn's."""
    host = start(restarting=restarting)
    to_open_slot(host, implement("h1"))
    asked = host.run.expect_turn(role="implementer")
    waiting = host.run.suspend(asked)
    host.run.send(
        CheckpointKnown(invocation=invocation_of(asked), checkpoint=Retained(revision=CANDIDATE))
    )
    host.run.resume_ready(waiting)
    resumed = host.run.expect_turn(role="implementer")
    assert charge_of(resumed) == "resume"
    # The resumed turn left the workspace as it was: core reports the same revision again.
    implementer_reply(host, checkpoint=Retained(revision=CANDIDATE))
    judge_reply(host)
    host.drain()

    (settle,) = [
        item.request.disposition
        for item in host.run.decisions
        if isinstance(item, Wait)
        and isinstance(item.request, Withdraw)
        and isinstance(item.request.disposition, Settle)
    ]
    assert settle.candidate == CANDIDATE
    finish = host.run.finished
    assert isinstance(finish, Finish)
    assert finish.result.selection.revision == CANDIDATE  # ty: ignore[unresolved-attribute]


# -- whatever waits, and however its checkpoint ends, the run ends ---------------------------------

WAITERS = ("implementer", "judge")
CHECKPOINTS = st.sampled_from(["retained", "declined", "none"])


def suspend(host: Host, asked: Asked, checkpoint: str, serial: int) -> Continuation:
    """The agent ended the turn waiting; core recorded the wait and what it did with the work."""
    waiting = host.run.suspend(asked)
    if asked.label == "implementer":
        outcome = {
            "retained": Retained(revision=RevisionRef.of_git_commit(f"w{serial}")),
            "declined": Declined(reason=next(iter(CheckpointDecline))),
        }.get(checkpoint)
        if outcome is not None:
            host.run.send(CheckpointKnown(invocation=invocation_of(asked), checkpoint=outcome))
    return waiting


def fresh_waiter(asked: Asked) -> bool:
    """Whether the wait is a first turn of a role that may end waiting (a resume does not)."""
    return (
        isinstance(asked.request, RequestTurn)
        and asked.label in WAITERS
        and charge_of(asked) != "resume"
    )


@MODES
@settings(max_examples=40, deadline=None, derandomize=True, database=None)
@given(
    steps=st.lists(
        st.tuples(st.integers(0, 7), st.integers(0, 11), st.booleans(), CHECKPOINTS), max_size=40
    ),
    rounds=st.integers(1, 2),
    in_flight=st.integers(1, 2),
)
def test_a_run_ends_whatever_its_agents_wait_for_and_however_their_checkpoints_end(
    *,
    restarting: bool,
    steps: list[tuple[int, int, bool, str]],
    rounds: int,
    in_flight: int,
) -> None:
    """A turn that waits is resumed once its evaluation settles, in any order among the waits.

    Each step answers one open wait (a fresh implementer or judge turn waits when the step
    says so) or resumes one recorded wait; then everything left is answered the ordinary way.
    The run must reach its one ``Finish`` with every workspace closed and nothing waiting.
    """
    host = begin(
        config(max_rounds=rounds, max_in_flight=in_flight), restarting=restarting, view=offering()
    )
    recorded: list[Continuation] = []
    serial = 0

    def move(pick: int, variant: int, checkpoint: str, *, waits: bool) -> bool:
        nonlocal serial
        answerable = [item for item in host.run.open if item.until is not None]
        options = len(recorded) + len(answerable)
        if not options:
            if not host.run.sleeping_until:
                return False
            host.run.advance_to(min(host.run.sleeping_until))
            return True
        chosen = pick % options
        if chosen < len(recorded):
            host.run.resume_ready(recorded.pop(chosen))
            return True
        asked = answerable[chosen - len(recorded)]
        if waits and fresh_waiter(asked):
            serial += 1
            recorded.append(suspend(host, asked, checkpoint, serial))
        else:
            host.answer(asked, variant)
        return True

    for pick, variant, waits, checkpoint in steps:
        if not move(pick, variant, checkpoint, waits=waits):
            break
    for _ in range(500):
        if not move(0, 0, "none", waits=False):
            break

    assert not recorded, host.run.timeline()
    assert_flow_closed(host)
    assert len([item for item in host.run.decisions if isinstance(item, Finish)]) == 1


# -- a lost turn is asked again after a growing wait ---------------------------------------------

LOST_WAITS = (5.0, 10.0)
"""The run-clock waits before the first and second re-ask: ``Backoff(first=5, cap=120)`` doubling."""


def lose_and_wait(host: Host, role: str) -> float:
    """Lose the open turn of ``role``; return how long the run clock waits to ask it again."""
    before = host.run.view.now_at
    host.run.lose(host.run.expect_turn(role=role))
    until = min(host.run.sleeping_until)  # the others are limits of turns still open
    host.run.advance_to(until)
    return until - before


@MODES
@pytest.mark.parametrize("role", ["implementer", "judge", "profiler"])
def test_a_lost_turn_is_asked_again_twice_after_five_then_ten_seconds_and_then_ends_the_work(
    *, restarting: bool, role: str
) -> None:
    host = start(restarting=restarting, profiling=True)
    if role == "profiler":
        to_open_slot(host, profile("p1"))
    else:
        to_open_slot(host, implement("h1"))
        if role == "judge":
            implementer_reply(host, checkpoint=Retained(revision=CANDIDATE))

    waits = [lose_and_wait(host, role) for _ in LOST_WAITS]
    assert waits == list(LOST_WAITS)
    assert charge_of(host.run.expect_turn(role=role)) == "correction"

    host.run.lose(host.run.expect_turn(role=role))
    assert not host.open(RequestTurn, label=role), host.run.timeline()
    host.drain()
    assert_flow_closed(host)


@MODES
def test_a_lost_planner_turn_is_asked_again_after_five_then_ten_seconds(
    *, restarting: bool
) -> None:
    host = start(restarting=restarting)

    waits = [lose_and_wait(host, PLANNER_LABEL) for _ in LOST_WAITS]

    assert waits == list(LOST_WAITS)
    assert host.open(RequestTurn, label=PLANNER_LABEL)


def test_only_the_roles_that_wait_for_evaluations_may_end_a_turn_waiting() -> None:
    assert {role.name for role in ROLES if role.may_wait_for_evaluations} == set(WAITERS)


# -- what the host must offer ----------------------------------------------------------------------


def test_the_search_offers_suspension_and_profile_capture_as_optional_and_requires_nothing() -> (
    None
):
    declaration = dynamic.declaration

    assert {"suspend", "profile-capture"} <= declaration.optional
    assert not declaration.required
