"""A request whose executor was a host instance that is gone settles at recovery, never unknown.

A run was stopped with SIGTERM while a planner turn was in flight and its provider wedged, then
resumed. Recovery sent an inspection about eight requests of the dead host's agent sessions and
ended the run blocked, "outcome unknown", although nothing could ever answer: an ensure whose
session had not yet accepted a turn, the lost turn, and the cancels and closes the stop had left
in flight. An agent session and its turns live and die with the host instance that started them,
so a restarted executor can only say that a turn began and has no recorded result.

Every test drives core through its public API (`step`) and answers each request core sends the
way an executor does (`test_recovery_unanswered`): from what it did, sealed, begun, never begun,
unknown, retryably failed, or not at all.
"""

from __future__ import annotations

from enum import StrEnum

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

import vs_core.api as core
from vs_sim.api.testing import examples

from .test_recovery_unanswered import EPOCH, LATER, START, Executor, _settle, checks_of
from .test_request_recovery import fixture_digest, observed, recovering_state, reload

DEAD_EPOCH = 1
"""The host epoch that dispatched what the stop left; the resumed host is `EPOCH`."""
RUN = core.Scope(owner=core.initial_state().run.run_id, generation=0)
SESSION = core.SessionSpec(
    session_id=core.SessionId(root="session:orchestrator:plan"),
    role_id=core.RoleId(root="dynamic-orchestrator"),
    policy="reuse",
    lifetime="owner",
    access=core.Access.READ_ONLY,
)
TURN = core.TurnSpec.model_validate(
    {
        "session": SESSION,
        "invocation_id": core.InvocationId(root="inv:plan:1"),
        "workspace": RUN,
        "prompts": (),
        "output_schema": core.SchemaRef(name="plan", version=1),
        "charge_class": "free",
    }
)
INVOCATION = core.InvocationRef(
    session_id=SESSION.session_id, invocation_id=TURN.invocation_id, generation=0
)
LEASE = core.ResourceId(root="session:run:plan")


def recover(
    records: list[core.RequestRecord], executor: Executor, *, session_waits: bool = False
) -> core.CoreState:
    """Resume a run whose agent session holds the turn, with the executor answering as given.

    As `test_recovery_unanswered.recover`, over a state that also knows the session and the
    invocation the dead host's requests belong to. With ``session_waits`` the session is still
    settling: it waits on the requests the stop left open, as the real one did.
    """
    waiting = tuple(row.request_id for row in records if row.phase != core.RequestPhase.COMPLETED)
    state = recovering_state(*records)
    state = state.model_copy(
        update={
            "sessions": core.SessionsState(
                sessions=(
                    core.SessionView(
                        spec=SESSION,
                        scope=RUN,
                        generation=0,
                        phase=core.SessionPhase.CLOSING,
                        invocation=TURN.invocation_id,
                        resource_id=LEASE,
                        wait=core.WaitOn(requests=waiting) if session_waits and waiting else None,
                    ),
                ),
                invocations=(
                    core.Invocation(
                        until=core.Until(at=60.0),
                        invocation=INVOCATION,
                        scope=RUN,
                        turn=TURN,
                        phase=core.SessionPhase.ACQUIRING,
                    ),
                ),
            )
        }
    )
    started = core.step(reload(state), core.RecoveryStarted(epoch=EPOCH, now_at=START))
    state = _settle(started.state, started.requests, executor)
    for _ in range(4):
        if "pending" not in checks_of(state).values():
            break
        moved = core.step(state, core.ClockAdvanced(now_at=LATER))
        state = _settle(moved.state, moved.requests, executor)
    return state


class Left(StrEnum):
    """What a stopped host left of its agent sessions: the shapes the real run held."""

    ENSURE_COMPLETED = "ensure-completed"
    """A sealed ensure whose session was closed before it accepted a turn."""
    TURN_OPEN = "turn-open"
    TURN_ENDED_AT_CLOSE = "turn-ended-at-close"
    """The turn core ended when the run closed: its result is core's, not the executor's."""
    TURN_BLOCKED = "turn-blocked"
    CANCEL_OPEN = "cancel-open"
    CANCEL_ENDED_AT_CLOSE = "cancel-ended-at-close"
    CLOSE_OPEN = "close-open"
    CLOSE_ENDED_AT_CLOSE = "close-ended-at-close"


OPEN = frozenset({Left.TURN_OPEN, Left.CANCEL_OPEN, Left.CLOSE_OPEN})
OPEN_WRITES = frozenset({Left.CANCEL_OPEN, Left.CLOSE_OPEN})
ENDED = frozenset(set(Left) - OPEN)


def _request(kind: Left, name: str) -> core.Request:
    identity = core.RequestId(root=name)
    match kind:
        case Left.ENSURE_COMPLETED:
            return core.EnsureSession(request_id=identity, scope=RUN, spec=SESSION)
        case Left.TURN_OPEN | Left.TURN_ENDED_AT_CLOSE | Left.TURN_BLOCKED:
            return core.DispatchTurn(request_id=identity, scope=RUN, turn=TURN)
        case Left.CANCEL_OPEN | Left.CANCEL_ENDED_AT_CLOSE:
            return core.CancelTurn(request_id=identity, scope=RUN, invocation=INVOCATION)
        case Left.CLOSE_OPEN | Left.CLOSE_ENDED_AT_CLOSE:
            return core.CloseSession(
                request_id=identity, scope=RUN, session_id=SESSION.session_id, resource_id=LEASE
            )
    raise AssertionError(kind)


def left_by_dead_host(kind: Left, number: int) -> core.RequestRecord:
    """One record of ``kind``, dispatched by the host epoch that died."""
    request = _request(kind, f"{kind.value}:{number}")
    assert request.request_id is not None
    lifecycle = (
        core.LifecycleClass.SESSION_TURN
        if isinstance(request, core.DispatchTurn)
        else core.LifecycleClass.IDEMPOTENT_WRITE
    )
    phase = {
        Left.TURN_BLOCKED: core.RequestPhase.BLOCKED,
        **dict.fromkeys(OPEN, core.RequestPhase.DISPATCHED),
    }.get(kind, core.RequestPhase.COMPLETED)
    record = core.RequestRecord(
        request_id=request.request_id,
        request=request,
        payload_digest=fixture_digest(request),
        lifecycle=lifecycle,
        phase=phase,
        until=core.Until(at=60.0),
        addressed_by=(),
        dispatched_in=DEAD_EPOCH,
        failure=core.Failed(reason=core.FailureReason.TIMED_OUT, limit_at=60.0)
        if phase == core.RequestPhase.BLOCKED
        else None,
    )
    if kind == Left.ENSURE_COMPLETED:
        facts: dict[str, object] = {
            "status": core.ObservationStatus.SUCCEEDED,
            "accepted": True,
            "terminal": True,
            "resource_id": LEASE,
            "children_complete": True,
        }
    elif kind in ENDED:
        facts = {"status": core.ObservationStatus.FAILED, "terminal": True}
    else:
        return record
    return record.model_copy(update={"observation": observed(record, **facts)})


def test_a_stop_that_left_agent_sessions_in_flight_does_not_block_the_resume() -> None:
    """The eight requests of the real run: ended and open, whatever the new executor can say."""
    kinds = [
        Left.ENSURE_COMPLETED,
        Left.ENSURE_COMPLETED,
        Left.TURN_BLOCKED,
        Left.CANCEL_ENDED_AT_CLOSE,
        Left.CANCEL_ENDED_AT_CLOSE,
        Left.CLOSE_ENDED_AT_CLOSE,
    ]
    records = [left_by_dead_host(kind, number) for number, kind in enumerate(kinds)]
    state = recover(records, Executor.BEGUN)
    assert state.records.recovery.phase == core.RecoveryPhase.READY
    assert set(checks_of(state).values()) == {"terminal"}


@pytest.mark.regression
@settings(max_examples=examples(pr=60, nightly=600), deadline=None)
@example(
    kinds=[
        Left.ENSURE_COMPLETED,
        Left.TURN_BLOCKED,
        Left.CANCEL_ENDED_AT_CLOSE,
        Left.CLOSE_ENDED_AT_CLOSE,
    ],
    executor=Executor.BEGUN,
    session_waits=True,
)
@given(
    kinds=st.lists(st.sampled_from(list(Left)), min_size=1, max_size=len(Left), unique=True),
    executor=st.sampled_from(list(Executor)),
    session_waits=st.booleans(),
)
def test_recovery_never_blocks_on_a_request_of_a_gone_host_instance(
    kinds: list[Left], executor: Executor, *, session_waits: bool
) -> None:
    """Whatever the dead host's sessions left, and however the new executor answers.

    Recovery ends ready with no check pending or blocked. A request the stop left ended holds
    nothing; a turn still open is ended as lost, never waited on; a cancel or a close still open
    goes out again unless the executor sealed its result.
    """
    records = [left_by_dead_host(kind, number) for number, kind in enumerate(kinds)]
    state = recover(records, executor, session_waits=session_waits)
    resolutions = checks_of(state)
    assert state.records.recovery.phase == core.RecoveryPhase.READY
    assert not {"pending", "blocked"} & set(resolutions.values())
    rows = {row.request_id: row for row in state.records.records}
    for kind, record in zip(kinds, records, strict=True):
        resolution = resolutions[record.request_id.root]
        row = rows[record.request_id]
        if kind in ENDED:
            assert resolution == "terminal"
        if kind == Left.TURN_OPEN:
            assert resolution in {"terminal", "safe-prepared"}
            assert row.phase != core.RequestPhase.DISPATCHED
        if kind in OPEN_WRITES and executor != Executor.SEALED:
            assert resolution in {"safe-prepared", "terminal"}


@pytest.mark.parametrize("kind", sorted(Left))
@pytest.mark.parametrize("executor", list(Executor))
def test_each_request_the_stop_can_leave_resolves_whatever_the_executor_says(
    kind: Left, executor: Executor
) -> None:
    record = left_by_dead_host(kind, 0)
    state = recover([record], executor)
    assert state.records.recovery.phase == core.RecoveryPhase.READY
    assert checks_of(state)[record.request_id.root] in {"terminal", "safe-prepared"}
