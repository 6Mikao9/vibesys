"""The run ends however core refuses it and wherever the operator stops it.

A search stalls when an event it needs never comes, or comes in a phase that has no way on. The
two classes here are the ones that left runs open before: a decision core refuses (a start, a
settlement, the proposal of the winner, the finish) and a stop that lands in the middle of any
wait. Each is driven on the real core with scripted agents, and the run must reach its terminal
state with every attempt closed.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from tests.vibesys.orchestration.issue_queue.search.world import (
    IssueQueueWorld,
    evaluated,
    implemented,
    judged,
)

from vs_core.api import (
    AttemptPhase,
    ContractError,
    ControlId,
    ControlInput,
    RejectionCode,
    RunControlEvent,
    RunResultProposal,
)
from vs_dst.api import SimulatedRun, simulate
from vs_runtime.api.core import ObservationRejectedError

if TYPE_CHECKING:
    from tests.vibesys.orchestration.issue_queue.search._refusals import Refuses

    from vs_core.api import DecisionId, Request
    from vs_runtime.api.core import CoreRuntime, ExecutionResult
    from vs_sim.api.testing import VirtualClock

CODES = st.sampled_from(list(RejectionCode))
_BOUNDED = settings(
    max_examples=12,
    deadline=None,
    derandomize=True,
    database=None,
    suppress_health_check=[HealthCheck.too_slow],
)


def _options() -> dict[str, Any]:
    """One issue, worked once, approved, and evaluated once."""
    return {
        "implementer": [implemented()],
        "judge": [judged()],
        "perf": [evaluated()],
        "max_rounds": 1,
    }


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
        name, _, ask = identity.root.rsplit("/", 1)[-1].partition(".")
        return self.code if name in self.names and ask in ("", "0") else None


def _refusing(refuses: Refuses) -> SimulatedRun:
    return simulate(IssueQueueWorld(refuses=refuses, **_options()))


@_BOUNDED
@given(names=st.sets(st.sampled_from(FIRST_ASKS), min_size=1).map(frozenset), code=CODES)
def test_a_run_ends_whichever_first_asks_core_refuses(
    names: frozenset[str], code: RejectionCode
) -> None:
    assert_ended_closed(_refusing(RefuseFirst(names, code)))


@_BOUNDED
@given(code=CODES)
def test_a_start_core_refuses_ends_the_run_in_failure_naming_the_issue(
    code: RejectionCode,
) -> None:
    run = _refusing(RefuseFirst(frozenset({"start"}), code))
    assert_ended_closed(run)
    result = run.core.run.result
    assert result is not None
    assert result.outcome == "failure"
    assert "issue #1" in result.reason


# -- the operator stops the run ----------------------------------------------------------------


class StopAfter(IssueQueueWorld):
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
    return len(simulate(IssueQueueWorld(**_options())).executed_effects)


STOP_GAP = (
    "gap (owner: vs-core, vs-runtime): a stop settles attempts that still have a prepared or "
    "in-flight request, and the loop halts on it instead of cancelling it"
)
KNOWN_OPEN = frozenset({10})
HALTS = (ContractError, ObservationRejectedError)


@pytest.mark.parametrize(
    "position",
    [
        pytest.param(
            at,
            marks=(
                [pytest.mark.xfail(raises=HALTS, strict=True, reason=STOP_GAP)]
                if at in KNOWN_OPEN
                else []
            ),
            id=f"after-{at}-requests",
        )
        for at in range(_requests_of_a_whole_run() + 1)
    ],
)
def test_a_stop_at_any_position_ends_the_run_with_every_attempt_closed(position: int) -> None:
    run = simulate(StopAfter(position, _options()))
    assert_ended_closed(run)
    result = run.core.run.result
    assert result is not None
    if run.stopped:
        assert result.outcome == "cancelled"
