"""The run ends however core refuses it and wherever the operator stops it.

A search stalls when an event it needs never comes, or comes in a phase that has no way on. The
three classes here are the ones that left runs open before: a start core refuses, a settlement
core rejects, and a stop that lands in the middle of any wait. A round that ends without core's
settlement is closed from the search's own state, counted as failed, so the next round starts. Each is driven on the real core
with scripted agents, and the run must reach its terminal state with every attempt closed.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from tests.vibesys.orchestration.single.search.world import SingleWorld, single_state

from vs_core.api import (
    AttemptPhase,
    ControlId,
    ControlInput,
    RejectionCode,
    RunControlEvent,
    RunResultProposal,
)
from vs_dst.api import SimulatedRun, simulate

if TYPE_CHECKING:
    from tests.support.refusing_search import Refuses

    from vs_core.api import DecisionId, Request
    from vs_runtime.api.core import CoreRuntime, ExecutionResult
    from vs_sim.api.testing import VirtualClock

ROUNDS = 2
CODES = st.sampled_from(list(RejectionCode))
_BOUNDED = settings(
    max_examples=12,
    deadline=None,
    derandomize=True,
    database=None,
    suppress_health_check=[HealthCheck.too_slow],
)


def _options() -> dict[str, Any]:
    """Two rounds, each a new hypothesis, implemented and self-approved."""
    return {"max_rounds": ROUNDS}


def assert_ended_closed(run: SimulatedRun) -> None:
    """The run reached its terminal state, was never halted, and closed every attempt."""
    assert run.halted is None, run.halted
    assert run.finished
    assert not run.liveness_violations
    for attempt in run.core.attempts.attempts:
        assert attempt.phase == AttemptPhase.TERMINAL


# -- core refuses a decision -------------------------------------------------------------------

FIRST_ASKS = ("start", "settle", "propose", "finish")
"""The decisions core may refuse once; the search asks again or ends without them."""


@dataclass(frozen=True)
class RefuseFirst:
    """Core refuses the first ask of each named decision with ``code``."""

    names: frozenset[str]
    code: RejectionCode

    def __call__(self, identity: DecisionId) -> RejectionCode | None:
        """The code for a first ask of a named decision, else None."""
        name = identity.root.rsplit("/", 1)[-1]
        return self.code if name in self.names else None


def _refusing(refuses: Refuses) -> SimulatedRun:
    return simulate(SingleWorld(refuses=refuses, **_options()))


@_BOUNDED
@given(names=st.sets(st.sampled_from(FIRST_ASKS), min_size=1).map(frozenset), code=CODES)
def test_a_run_ends_whichever_first_asks_core_refuses(
    names: frozenset[str], code: RejectionCode
) -> None:
    assert_ended_closed(_refusing(RefuseFirst(names, code)))


@dataclass(frozen=True)
class RefuseStartOf:
    """Core refuses the start of the rounds whose numbers are named."""

    rounds: frozenset[int]
    code: RejectionCode

    def __call__(self, identity: DecisionId) -> RejectionCode | None:
        """The code for a named round's start, else None."""
        key, _, name = identity.root.rpartition("/")
        number = int(key.partition(".")[2]) if key.startswith("round.") else None
        return self.code if name == "start" and number in self.rounds else None


@_BOUNDED
@given(
    rounds=st.sets(st.integers(1, ROUNDS), min_size=1).map(frozenset),
    code=CODES,
)
def test_a_round_core_refuses_to_start_does_not_stall_the_run(
    rounds: frozenset[int], code: RejectionCode
) -> None:
    """A refused start is counted as a failed round, so the search moves on instead of asking again."""
    run = _refusing(RefuseStartOf(rounds, code))
    assert_ended_closed(run)
    result = run.core.run.result
    assert result is not None
    if len(rounds) < ROUNDS:
        assert result.outcome == "success", result.reason
    state = single_state(run)
    assert state.closed == ROUNDS


# -- the operator stops the run ----------------------------------------------------------------


class StopAfter(SingleWorld):
    """A world whose operator stops the run once ``requests`` requests have been executed."""

    def __init__(self, requests: int, options: dict[str, Any]) -> None:
        """Stop after the ``requests``-th executed request (0: before any)."""
        super().__init__(**options)
        self._requests = requests
        self.setup = replace(self.setup, controlled=True)

    def attach(self, shell: CoreRuntime[Any], clock: VirtualClock) -> _Stopper:
        """The operator of this host."""
        return _Stopper(shell, clock, self._requests)


class _Stopper:
    """Admits the operator's stop after a number of executed requests."""

    def __init__(self, shell: CoreRuntime[Any], clock: VirtualClock, after: int) -> None:
        self._shell = shell
        self._clock = clock
        self._left = after
        self._stopped = False

    async def __call__(self, request: Request, result: ExecutionResult) -> ExecutionResult:
        """The request's result; the stop follows the last request the operator waited for."""
        del request
        if self._left <= 0 and not self._stopped:
            self._stop()
        self._left -= 1
        return result

    def _stop(self) -> None:
        self._stopped = True
        self._shell.submit(
            RunControlEvent(
                control=ControlInput(control_id=ControlId(root="stop"), action="stop"),
                now_at=self._clock.now(),
                result=RunResultProposal(outcome="cancelled", reason="operator stop"),
            ),
            now_at=self._clock.now(),
        )


def _requests_of_a_whole_run() -> int:
    """How many requests the uninterrupted run executes (so every position is tried)."""
    return len(simulate(SingleWorld(**_options())).executed_effects)


@pytest.mark.parametrize(
    "position",
    [pytest.param(at, id=f"after-{at}-requests") for at in range(_requests_of_a_whole_run() + 1)],
)
def test_a_stop_at_any_position_ends_the_run_with_every_attempt_closed(position: int) -> None:
    run = simulate(StopAfter(position, _options()))
    assert_ended_closed(run)
    result = run.core.run.result
    assert result is not None
    if run.stopped:
        assert result.outcome == "cancelled"
