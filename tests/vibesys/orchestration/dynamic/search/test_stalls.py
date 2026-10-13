"""The run ends however core refuses it and wherever the operator stops it.

A search stalls when an event it needs never comes, or comes in a phase that has no way on. The
three classes here are the ones that left runs open before: a start core refuses, a settlement
core rejects, and a stop that lands in the middle of any wait. Each is driven on the real core
with scripted agents, and the run must reach its terminal state with every attempt closed.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from tests.vibesys.orchestration.dynamic.flow._host import assert_flow_closed, begin
from tests.vibesys.orchestration.dynamic.search._replies import (
    implement,
    implemented,
    plan_reply,
    reviewed,
)
from tests.vibesys.orchestration.dynamic.search._run import config
from tests.vibesys.orchestration.dynamic.search.world import DynamicWorld

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
    from tests.vibesys.orchestration.dynamic.search._refusals import Refuses

    from vs_core.api import DecisionId, Request
    from vs_runtime.api.core import CoreRuntime, ExecutionResult
    from vs_sim.api.testing import VirtualClock

WORKSTREAMS = 2
CODES = st.sampled_from(list(RejectionCode))
_BOUNDED = settings(
    max_examples=12,
    deadline=None,
    derandomize=True,
    database=None,
    suppress_health_check=[HealthCheck.too_slow],
)


def _options() -> dict[str, Any]:
    """Two workstreams in one round, each implemented and approved."""
    return {
        "planner": [plan_reply(*(implement(f"h{n}") for n in range(WORKSTREAMS)))],
        "implementer": [implemented() for _ in range(WORKSTREAMS)],
        "judge": [reviewed() for _ in range(WORKSTREAMS)],
        "max_in_flight": WORKSTREAMS,
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
        name = identity.root.rsplit("/", 1)[-1]
        return self.code if name in self.names else None


def _refusing(refuses: Refuses) -> SimulatedRun:
    return simulate(DynamicWorld(refuses=refuses, **_options()))


@_BOUNDED
@given(names=st.sets(st.sampled_from(FIRST_ASKS), min_size=1).map(frozenset), code=CODES)
def test_a_run_ends_whichever_first_asks_core_refuses(
    names: frozenset[str], code: RejectionCode
) -> None:
    assert_ended_closed(_refusing(RefuseFirst(names, code)))


MODES = pytest.mark.parametrize("restarting", [False, True], ids=["live", "restart-every-step"])


@MODES
@_BOUNDED
@given(names=st.sets(st.sampled_from(FIRST_ASKS), min_size=1).map(frozenset), code=CODES)
def test_a_flow_ends_whichever_first_asks_core_refuses(
    *, restarting: bool, names: frozenset[str], code: RejectionCode
) -> None:
    """The flow-level form of the test above, on the flow alone with a fake core.

    Core refuses the first ask of each named decision, up to its refund limit. The flow takes
    a refused start as a refund, asks to settle plainly, and proposes the plain failure when its
    result is refused.
    """
    host = begin(config(max_rounds=1, max_in_flight=WORKSTREAMS), restarting=restarting)
    host.refusal_code = code
    host.refusals = {name: 2 for name in names if name != "finish"}
    host.drain()
    if "finish" in names:
        host.refuse_finish()
    assert_flow_closed(host)


@dataclass(frozen=True)
class RefuseStartOf:
    """Core refuses the start of the workstreams whose hypotheses are named."""

    hypotheses: frozenset[str]
    code: RejectionCode

    def __call__(self, identity: DecisionId) -> RejectionCode | None:
        """The code for a named workstream's start, else None."""
        key, _, name = identity.root.rpartition("/")
        work = key.partition(".")[2]
        return self.code if name == "start" and work in self.hypotheses else None


@_BOUNDED
@given(
    hypotheses=st.sets(st.sampled_from([f"h{n}" for n in range(WORKSTREAMS)]), min_size=1).map(
        frozenset
    ),
    code=CODES,
)
def test_a_start_core_refuses_leaves_the_other_workstreams_to_finish(
    hypotheses: frozenset[str], code: RejectionCode
) -> None:
    run = _refusing(RefuseStartOf(hypotheses, code))
    assert_ended_closed(run)
    result = run.core.run.result
    assert result is not None
    if len(hypotheses) < WORKSTREAMS:
        assert result.outcome == "success", result.reason


@MODES
@_BOUNDED
@given(
    hypotheses=st.sets(st.sampled_from([f"h{n + 1}" for n in range(WORKSTREAMS)]), min_size=1).map(
        frozenset
    ),
    code=CODES,
)
def test_a_start_core_refuses_leaves_the_other_workstreams_to_finish_in_the_flow(
    *, restarting: bool, hypotheses: frozenset[str], code: RejectionCode
) -> None:
    """The flow-level form of the test above: the refused workstreams are refunded, the rest run.

    The planner's first portfolio names two hypotheses; the starts of the chosen ones are
    refused. A refused workstream gives its slot back, and the run ends in success when at
    least one workstream was left to finish.
    """
    host = begin(config(max_rounds=1, max_in_flight=WORKSTREAMS), restarting=restarting)
    host.refusal_code = code
    host.refused_hypotheses = set(hypotheses)
    host.drain()
    assert_flow_closed(host)
    result = host.run.finished.result if host.run.finished else None
    assert result is not None
    if len(hypotheses) < WORKSTREAMS:
        assert result.outcome == "success", result.reason


# -- the operator stops the run ----------------------------------------------------------------


class StopAfter(DynamicWorld):
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
    return len(simulate(DynamicWorld(**_options())).executed_effects)


@pytest.mark.parametrize(
    "position", range(_requests_of_a_whole_run() + 1), ids=lambda at: f"after-{at}-requests"
)
def test_a_stop_at_any_position_ends_the_run_with_every_attempt_closed(position: int) -> None:
    run = simulate(StopAfter(position, _options()))
    assert_ended_closed(run)
    result = run.core.run.result
    assert result is not None
    if run.stopped:
        assert result.outcome == "cancelled"
