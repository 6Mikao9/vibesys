"""Crash and replay the dynamic search across durable transitions, including lost turns.

This uses the public core driver, not the story driver's synthetic restart. Every
record is codec-reloaded and recovery inspects begun and sealed requests. The
shell/CLI and Git properties beside this test exercise the I/O boundaries.
"""

from __future__ import annotations

from collections import Counter
from functools import cache
from typing import TYPE_CHECKING

import pytest
from hypothesis import Phase, example, given, note, settings
from hypothesis import strategies as st
from tests.support.dynamic_script import REGISTRY, config, harness
from tests.vibesys.dynamic_world import DynamicWorld
from tests.vibesys.orchestration.dynamic._replies import (
    implement,
    implemented,
    plan_reply,
    reviewed,
)

from vibesys.orchestration.dynamic.search.api import MergeOutcome, RunSnapshot, dynamic
from vs_core.api import DispatchTurn, FlowDriver, RecoveryPhase, ResumeSessionTurn, RunStatus
from vs_core.api.testing import Faults, drive
from vs_faults.api import AgentFault, Boundary, FaultPlan, FaultRule
from vs_sim.api.testing import examples

if TYPE_CHECKING:
    from vs_core.api import FlowState, Request
    from vs_core.api.testing import Trace


def _run(*, width: int, fault: Faults | None = None) -> Trace[FlowState]:
    selected = config(max_in_flight=width)
    world = DynamicWorld(
        planner=[plan_reply(*(implement(f"h{n}") for n in range(width)))],
        implementer=[implemented() for _ in range(width)],
        judge=[reviewed() for _ in range(width)],
        max_in_flight=width,
    )
    return drive(
        dynamic.driver(selected, REGISTRY),
        selected,
        world,
        harness(selected, shell_events=True),
        fault,
    )


@cache
def _uninterrupted(width: int) -> Trace[FlowState]:
    """The run no fault touches: each example compares its recovery with it, never changes it."""
    return _run(width=width)


def _moved_to(snapshot: RunSnapshot) -> bool:
    """Whether main is a revision a recorded merge or fast-forward moved it to."""
    return snapshot.main in {
        item.revision
        for item in snapshot.merges
        if item.outcome is MergeOutcome.MERGED and item.revision is not None
    }


def _snapshot(trace: Trace[FlowState]) -> RunSnapshot:
    snapshot = FlowDriver.published(trace.state, RunSnapshot)
    assert snapshot is not None
    return snapshot


@pytest.mark.regression
@settings(
    max_examples=examples(pr=4, nightly=30),
    phases=(Phase.explicit, Phase.generate),
    database=None,
)
@example(point=1, width=1, unsealed=False, turn=0)
@example(point=24, width=1, unsealed=True, turn=1)
@example(point=60, width=2, unsealed=True, turn=3)
@given(
    point=st.integers(1, 500),
    width=st.integers(1, 2),
    unsealed=st.booleans(),
    turn=st.integers(0, 6),
)
def test_dynamic_recovery_converges_at_generated_commits_and_inside_agent_turns(
    point: int, width: int, *, unsealed: bool, turn: int
) -> None:
    """R3: both dispatch and resumed-turn facts must settle their recovery checks."""
    normal = _uninterrupted(width)
    crash = 1 + (point - 1) % normal.commits
    plan = FaultPlan(
        seed=point,
        rules=(FaultRule(boundary=Boundary.AGENT_TURN, at=max(1, turn), fault=AgentFault.CRASH),)
        if turn
        else (),
    )
    turns = 0

    def dies(request: Request) -> bool:
        nonlocal turns
        assert isinstance(request, DispatchTurn | ResumeSessionTurn)
        turns += 1
        return plan.match(Boundary.AGENT_TURN, request.turn.session.role_id.root, turns) is not None

    recovered = _run(
        width=width,
        fault=Faults(
            crash_after=frozenset({crash}),
            unsealed=lambda _request: unsealed,
            dies_in_turn=dies,
            reload=True,
        ),
    )
    note(f"{normal.commits} durable points; crash after {crash}, agent turn {turn}, {unsealed=}")
    assert recovered.finished
    assert recovered.core.run.status is RunStatus.TERMINAL
    assert recovered.core.records.recovery.phase is RecoveryPhase.READY
    assert recovered.core.run.result is not None
    assert recovered.core.run.result.outcome != "blocked", recovered.core.run.result.reason
    assert "outcome unknown" not in (recovered.core.run.result.reason or "")
    assert max(Counter(recovered.effects).values(), default=0) == 1
    before, after = _snapshot(normal), _snapshot(recovered)
    # Main moves to the first settled candidate built on it that beats it, so it follows the order
    # the candidates settled in, which a recovery may change; either way a recorded move left it.
    assert after.main == before.main or all(_moved_to(snapshot) for snapshot in (before, after))
    assert after.baseline == before.baseline
    # An interrupted provider never consumed its reply. Settled scientific results
    # therefore agree even when recovery changes the turn or settlement identities.
    assert {row.plan.work_id for row in after.finished} == {
        row.plan.work_id for row in before.finished
    }
    assert normal.core.run.result is not None
    assert recovered.core.run.result.selection == normal.core.run.result.selection
