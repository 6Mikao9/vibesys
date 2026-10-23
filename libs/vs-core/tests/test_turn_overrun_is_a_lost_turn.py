"""A turn that runs past its limit is a lost turn: cancelled, then asked again or failed per role.

The dispatch of a turn that never answers ends at its limit. Nothing is left to answer the
session, which once stayed executing for ever and halted the run (``OrphanWaitError``). Core
now cancels the turn like one whose acceptance stayed unknown and answers its invocation as
lost, so the role's ``lost_turns`` decide: the turn is asked again that many times, then the
ask ends in a typed failure. Either way the run ends with every workspace closed.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

import vs_core.api as core
from vs_core.api.testing import Answer, Failed, Running, Unknown

from .test_agent_turn_runs import assert_ended_closed
from .test_role_limits_through_core import implementer_turns
from .toy_agent_search import IMPLEMENTER, ToyHost, run_toy, summary, toy_search

RETRIES = 2
"""`toy_search`'s ``request_retries.limit``: how often core asks again to cancel a lost turn."""

_SETTINGS = settings(max_examples=12, deadline=None, derandomize=True, database=None)


@dataclass
class OverrunningHost(ToyHost):
    """A host whose first ``overruns`` implementer turns never answer."""

    overruns: int = 1
    _started: int = 0

    def _turn(
        self, request: core.DispatchTurn | core.ResumeSessionTurn, state: core.CoreState
    ) -> Answer:
        if request.turn.session.role_id.root == "toy-implementer":
            self._started += 1
            if self._started <= self.overruns:
                return Running(resource_id=core.ResourceId(root="lease:overrun"))
        return super()._turn(request, state)


@_SETTINGS
@given(
    overruns=st.integers(0, core.MAX_LOST_TURNS + 1), lost_turns=st.integers(0, core.MAX_LOST_TURNS)
)
def test_a_turn_that_overruns_is_asked_again_as_often_as_the_role_says(
    overruns: int, lost_turns: int
) -> None:
    implementer = IMPLEMENTER.model_copy(update={"lost_turns": lost_turns})

    trace = run_toy(
        OverrunningHost(overruns=overruns), definition=toy_search(implementer=implementer)
    )

    assert_ended_closed(trace)
    recovered = overruns <= lost_turns
    assert len(implementer_turns(trace)) == min(overruns, lost_turns + 1) + int(recovered)
    done = summary(trace).done[0]
    assert done.failed == (() if recovered else ("implementer",))


def test_a_turn_that_overruns_its_only_attempt_fails_the_ask_and_the_run_still_ends() -> None:
    """The reported halt: one turn, no lost turns allowed, the turn never answers."""
    implementer = IMPLEMENTER.model_copy(update={"lost_turns": 0})

    trace = run_toy(OverrunningHost(overruns=1), definition=toy_search(implementer=implementer))

    assert_ended_closed(trace)
    assert summary(trace).done[0].failed == ("implementer",)


@dataclass
class UnstoppableHost(OverrunningHost):
    """A host whose cancellation of the overrunning turn fails: it may still be running."""

    def __call__(self, request: core.Request, state: core.CoreState) -> Answer:
        if (
            isinstance(request, core.CancelTurn)
            and request.request_id is not None
            and not request.request_id.root.endswith("close")
        ):
            return Failed()
        return super().__call__(request, state)


@_SETTINGS
@given(lost_turns=st.integers(0, core.MAX_LOST_TURNS))
def test_a_failed_cancellation_never_releases_the_turn_and_the_run_halts_loudly(
    lost_turns: int,
) -> None:
    """A cancel that failed proves nothing about the writer: nothing is released or re-asked."""
    implementer = IMPLEMENTER.model_copy(update={"lost_turns": lost_turns})

    trace = run_toy(UnstoppableHost(overruns=1), definition=toy_search(implementer=implementer))

    assert not trace.finished
    assert len(implementer_turns(trace)) == 1
    [invocation] = [
        row
        for row in trace.core.sessions.invocations
        if row.invocation.invocation_id.root.startswith("inv:implementer")
    ]
    assert invocation.phase == core.SessionPhase.UNKNOWN
    assert core.WaitKind.SESSION in {w.waiter for w in core.orphan_waits(trace.core)}


@dataclass
class SilentCancelHost(OverrunningHost):
    """A host whose first ``silent`` cancellations of the lost turn never answer (a hung executor)."""

    silent: int = 0
    asks: int = 0

    def __call__(self, request: core.Request, state: core.CoreState) -> Answer:
        if (
            isinstance(request, core.CancelTurn)
            and request.request_id is not None
            and not request.request_id.root.endswith("close")
        ):
            self.asks += 1
            if self.asks <= self.silent:
                return Unknown()
        return super().__call__(request, state)


@pytest.mark.regression
@_SETTINGS
@example(silent=1, lost_turns=1)
@given(silent=st.integers(0, RETRIES + 1), lost_turns=st.integers(0, core.MAX_LOST_TURNS))
def test_a_cancellation_that_never_answers_is_asked_again_within_the_retry_limit(
    silent: int, lost_turns: int
) -> None:
    """The reported hang: the cancel of a lost planner turn timed out and nothing was scheduled.

    Cancelling is idempotent, so core asks again, up to ``request_retries.limit`` more times; the
    turn then is released and the role asks it again. Only when every ask stayed silent does the
    session keep its wait and the run halt loudly (the cancel proved nothing about the writer).
    """
    implementer = IMPLEMENTER.model_copy(update={"lost_turns": lost_turns})
    host = SilentCancelHost(overruns=1, silent=silent)

    trace = run_toy(host, definition=toy_search(implementer=implementer))

    if silent <= RETRIES:
        assert_ended_closed(trace)
        assert host.asks == silent + 1
    else:
        assert not trace.finished
        assert host.asks == RETRIES + 1
        assert core.WaitKind.SESSION in {w.waiter for w in core.orphan_waits(trace.core)}
