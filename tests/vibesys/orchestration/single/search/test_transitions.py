"""The phases of one round, taken event by event through the public search.

Each test plays core by hand with `Drive` and reads the answer through the state
(`SingleState.parts`, `Round.phase`) and the decisions: which events a phase takes, what an
operator stop and a refusal of core do in it, and how a turn that produced no reply is asked again.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from tests.vibesys.orchestration.single.search._drive import (
    PHASES,
    Drive,
    asked_once,
    prompt_of,
    settle_of,
)

from vibesys.orchestration.single.search.api import NoReplyKind, SingleState
from vibesys.serial_search.api import FailureKind
from vs_core.api import (
    AttemptRef,
    DecisionId,
    Event,
    FailureReason,
    Finish,
    MeasurementFinished,
    MeasurementResult,
    ObservationStatus,
    RejectionCode,
    ReplyError,
    ReplyErrorCode,
    TurnFailureKind,
    UnexpectedEvent,
    WorkspaceOpened,
)

CODES = st.sampled_from(list(RejectionCode))
_BOUNDED = settings(max_examples=25, deadline=None, derandomize=True, database=None)


def _counts(state: SingleState) -> tuple[int, int]:
    """How many rounds the search closed and how many left an outcome."""
    return state.closed, len(state.outcomes)


# -- each phase takes its events ------------------------------------------------------------------


def test_the_happy_path_visits_every_phase_in_order() -> None:
    drive = Drive()
    drive.open_round()
    seen = [drive.phase]
    for step in (drive.opened, drive.implement, drive.measure, drive.decoding_lost):
        step()
        seen.append(drive.phase)
    assert seen == ["opening", *PHASES[1:]]


@pytest.mark.parametrize("phase", PHASES)
def test_an_event_the_phase_does_not_take_is_refused(phase: str) -> None:
    drive = Drive()
    drive.reach(phase)
    answers = DecisionId(root="round.1/start")
    event: Event = (
        MeasurementFinished(
            decision=answers,
            result=MeasurementResult(
                scope=drive.round.scope, evidence=(), status=ObservationStatus.FAILED
            ),
        )
        if phase == "opening"
        else WorkspaceOpened(
            decision=answers, attempt=AttemptRef(attempt_id=drive.round.attempt, generation=0)
        )
    )
    before = drive.state
    with pytest.raises(UnexpectedEvent):
        drive.send(event)
    assert drive.state == before


@pytest.mark.parametrize(
    ("phase", "name"), [("measuring", "measure.0"), ("interpreting", "interpret")]
)
def test_a_failed_wait_settles_the_round_with_its_failure(phase: str, name: str) -> None:
    drive = Drive()
    drive.reach(phase)
    decisions = drive.fail(f"round.1/{name}")
    assert drive.phase == "settling"
    assert drive.round.failure is not None
    assert drive.round.failure.kind is not FailureKind.STOPPED
    asked_once(decisions, "round.1/settle")


# -- the operator stops -----------------------------------------------------------------------------


@pytest.mark.parametrize("phase", ["implementing", "measuring", "interpreting"])
def test_a_stop_in_a_started_phase_settles_the_attempt_as_stopped(phase: str) -> None:
    drive = Drive()
    drive.reach(phase)
    decisions = drive.stop()
    assert drive.phase == "settling"
    assert drive.round.failure is not None
    assert drive.round.failure.kind is FailureKind.STOPPED
    asked_once(decisions, "round.1/settle")


def test_a_stop_while_opening_is_remembered_and_settles_once_the_workspace_opens() -> None:
    drive = Drive()
    drive.open_round()
    assert drive.stop() == []
    assert drive.phase == "opening"
    assert drive.round.phase.model_dump()["stopping"] is True
    drive.opened()
    assert drive.phase == "settling"
    assert drive.round.failure is not None
    assert drive.round.failure.kind is FailureKind.STOPPED


def test_a_stop_while_settling_changes_nothing_of_the_round() -> None:
    drive = Drive()
    drive.reach("settling")
    before = drive.round
    assert drive.stop() == []
    assert drive.round == before


@pytest.mark.parametrize("phase", PHASES[:-1])
def test_a_stopped_round_is_cleared_without_being_counted(phase: str) -> None:
    drive = Drive()
    drive.reach(phase)
    drive.stop()
    if phase == "opening":
        drive.opened()
    decisions = drive.close()
    assert drive.open_rounds == []
    assert _counts(drive.state) == (0, 0)
    assert any(isinstance(item, Finish) for item in decisions)


# -- core refuses ----------------------------------------------------------------------------------


@_BOUNDED
@given(code=CODES)
def test_a_refused_start_closes_the_round_as_failed_without_an_outcome(code: RejectionCode) -> None:
    drive = Drive()
    drive.open_round()
    drive.fail("round.1/start", FailureReason.REFUSED, cause=code)
    assert 1 not in drive.open_rounds
    assert _counts(drive.state) == (1, 0)
    assert drive.state.checkpoint is None


@_BOUNDED
@given(first=CODES, second=CODES)
def test_a_refused_settle_is_proposed_plainly_once_then_the_round_ends_without_settlement(
    first: RejectionCode, second: RejectionCode
) -> None:
    drive = Drive()
    drive.reach("measuring")
    drive.fail("round.1/measure.0")
    plain = drive.fail("round.1/settle", FailureReason.REFUSED, cause=first)
    assert drive.phase == "settling"
    assert settle_of(plain, "round.1/settle.1").retention == "discard"
    assert _counts(drive.state) == (0, 0)

    drive.fail("round.1/settle.1", FailureReason.REFUSED, cause=second)
    assert 1 not in drive.open_rounds
    assert _counts(drive.state) == (1, 0)


# -- an attempt that produced no reply ---------------------------------------------------------------

LOST = (
    (
        FailureReason.INVALID_REPLY,
        {"errors": (ReplyError(path=("verdict",), code=ReplyErrorCode.MISSING),)},
        NoReplyKind.INVALID_REPLY,
    ),
    (FailureReason.TIMED_OUT, {}, NoReplyKind.NO_REPLY),
    (
        FailureReason.TURN_FAILED,
        {"turn_failure": next(iter(TurnFailureKind))},
        NoReplyKind.NO_REPLY,
    ),
)


@_BOUNDED
@given(budget=st.integers(min_value=1, max_value=4), ending=st.sampled_from(LOST))
def test_a_lost_turn_is_asked_again_while_the_paid_budget_lasts_then_settles(
    budget: int, ending: tuple[FailureReason, dict[str, object], NoReplyKind]
) -> None:
    reason, extra, kind = ending
    drive = Drive(max_retries_per_round=budget)
    drive.reach("implementing")
    for spent in range(1, budget):
        assert drive.round.spent == spent
        again = drive.fail(f"round.1/implementer.{spent - 1}", reason, **extra)
        assert drive.phase == "implementing"
        told = prompt_of(again, f"round.1/implementer.{spent}").no_reply
        assert told is not None
        assert told.kind is kind
    drive.fail(f"round.1/implementer.{budget - 1}", reason, **extra)
    assert drive.phase == "settling"
    assert drive.round.spent == budget
    assert drive.round.no_reply is not None
    assert drive.round.no_reply.kind is kind
