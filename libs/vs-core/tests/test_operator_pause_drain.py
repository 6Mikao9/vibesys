"""An operator Pause admits no new attempt, and settles every attempt it already admitted.

The drain is the run's way to stop without losing work: it closes admission of new attempts,
lets each admitted attempt run its continuations (the resume of a turn whose evaluation ended,
the interpretation of its evidence, the next role's turn, its settlement) to the close of its
workspace, and parks. A drain that held a continuation of admitted work, because the closed
gate was meant for new attempts, leaves open work with nothing runnable: the run sits in
``DRAINING`` and only a signal ends it.

These tests drive the toy search (``toy_agent_search``) on the real core against scripted
executors. The pause lands before one of the first requests, so it meets a turn running, an
evaluation queued or polling, a wait suspended, an evaluation that ended while paused, and an
attempt between two roles. The operator then relaunches the run as a successor epoch.
"""

from __future__ import annotations

from dataclasses import replace

from hypothesis import example, given, settings
from hypothesis import strategies as st

import vs_core.api as core
from vs_core.api.testing import Faults, Trace
from vs_sim.api.testing import examples

from .test_agent_turn_runs import SCENARIOS, Scenario, _ends
from .toy_agent_search import ROLES, Config, ToyHost, run_toy

_STEPS = 600
"""A hang guard: a run that is still going after this many steps is stuck."""
#: A judge that waits for its evaluation, whose job reports running for two polls.
_WAITING_JUDGE = Scenario(
    workstreams=1,
    in_flight=1,
    waits=(True,),
    polls=(2,),
    roles=("judge",),
    ends=("wait",),
    shares=True,
    faults=Faults(),
)
#: Two workstreams at one in flight: the second waits for a slot when the Pause lands.
_QUEUED_WORKSTREAM = Scenario(
    workstreams=2,
    in_flight=1,
    waits=(True,),
    polls=(0,),
    roles=ROLES,
    ends=("wait",),
    shares=True,
    faults=Faults(),
)
#: The same, with a job that reports running twice, so the drain settles while the host can die.
_SETTLING_WORKSTREAM = replace(_QUEUED_WORKSTREAM, polls=(2,))
# A whole run of the toy search per example, with and without the pause: PR depth draws few.
_SETTINGS = settings(
    max_examples=examples(pr=5, nightly=60), deadline=None, derandomize=True, database=None
)


def _run(
    scenario: Scenario, pause_at: int | None, crash: int | None = None
) -> tuple[Trace, ToyHost]:
    """The scenario with an operator Pause before the ``pause_at``-th answer, relaunched once parked.

    With ``crash``, the host also dies after that many commits and a new one takes over.
    """
    host = ToyHost(
        waits=scenario.waits,
        polls=scenario.polls,
        roles=scenario.roles,
        ends=scenario.ends,
        shares=scenario.shares,
        pause_at=pause_at,
        pause_owner=core.PauseOwner.OPERATOR,
        events=(core.RecoveryStarted(epoch=1, now_at=0.0),),
        max_steps=_STEPS,
    )
    config = Config(workstreams=scenario.workstreams, in_flight=scenario.in_flight, interpret=True)
    faults = (
        scenario.faults
        if crash is None
        else replace(scenario.faults, crash_after=frozenset({crash}))
    )
    trace = run_toy(host, config, faults=faults)
    assert trace.finished, f"the run never ended: {trace.core.run.status.value}"
    return trace, host


def _open_work(state: core.CoreState) -> list[str]:
    """What the run still holds open for an attempt it admitted."""
    waits = [
        f"wait {row.continuation_id.root}"
        for row in state.evaluation.continuations
        if row.phase in (core.ContinuationPhase.WAITING, core.ContinuationPhase.AUTHORIZED)
    ]
    attempts = [
        f"attempt {row.attempt_id.root}"
        for row in state.attempts.attempts
        if row.phase == core.AttemptPhase.ACTIVE
    ]
    return [*waits, *attempts]


def _restarted(trace: Trace) -> bool:
    """Whether a relaunch restarted the search from its checkpoints (its root had concluded)."""
    assert isinstance(trace.state, core.FlowState)
    return trace.state.base is not None


@_SETTINGS
@example(
    scenario=_WAITING_JUDGE, pause_at=11, crash=None
)  # the job ends while paused (the live stall)
@example(
    scenario=_QUEUED_WORKSTREAM, pause_at=3, crash=None
)  # a workstream the run had not started
@example(scenario=_SETTLING_WORKSTREAM, pause_at=0, crash=43)  # the host dies as the drain settles
@given(scenario=SCENARIOS, pause_at=st.integers(0, 40), crash=st.none() | st.integers(1, 200))
def test_a_pause_settles_every_admitted_attempt_before_the_run_parks(
    scenario: Scenario, pause_at: int, crash: int | None
) -> None:
    """The run that parks has no admitted attempt open, and its relaunch ends it as no pause would.

    Wherever the Pause lands, the drain runs each admitted attempt to the close of its
    workspace; only the workstreams the run had not started wait for the relaunch, and it
    then ends once, in success, with every workstream where it would have ended.
    """
    unpaused, _ = _run(scenario, None)
    trace, host = _run(scenario, pause_at, crash)
    for parked in host.parked:
        assert parked.run.status is core.RunStatus.PARKED
        assert _open_work(parked) == []
        # It parked when the work ran out, not when the clock reached the run's deadline.
        assert parked.run.now_at < parked.run.deadline_at
    result = trace.core.run.result
    assert result is not None
    assert result.outcome == "success"
    finishes = [
        row
        for row in trace.core.run.receipts
        if isinstance(row.decision, core.Finish) and isinstance(row.feedback, core.Accepted)
    ]
    assert len(finishes) == 1
    if host.parked and not _restarted(trace) and crash is None:
        # Held work resumes in another order, so parallel workstreams may end in another order.
        assert _ends(trace) == _ends(unpaused)
