"""An executor that answered Unknown or a retryable failure and went silent is blocked on time.

Core learns time only from ``ClockAdvanced``. Any tick at or after an answered-but-open
record's reconciliation bound blocks it (a ``BlockRequest`` request), earlier ticks do not,
the run's recovery barrier stays open for scheduling, and the projected next wake names
the bound so a shell sleeps toward it instead of stalling.
"""

from hypothesis import given
from hypothesis import strategies as st

from vs_core.api import (
    Access,
    BlockRequest,
    ClockAdvanced,
    CoreState,
    EnsureSession,
    EventId,
    LifecycleClass,
    Observation,
    ObservationStatus,
    RecordsState,
    RecoveryBarrier,
    RecoveryPhase,
    RequestId,
    RequestPhase,
    RequestRecord,
    RoleId,
    Scope,
    SessionId,
    SessionSpec,
    Until,
    initial_state,
    project,
    step,
)

BOUND = 100.0


def _silent(phase: RequestPhase, status: ObservationStatus, *, terminal: bool) -> CoreState:
    state = initial_state()
    request_id = RequestId(root="setup")
    request = EnsureSession(
        request_id=request_id,
        scope=Scope(owner=state.run.run_id, generation=0),
        spec=SessionSpec(
            session_id=SessionId(root="setup"),
            role_id=RoleId(root="worker"),
            policy="reuse",
            lifetime="owner",
            access=Access.WRITE_ARTIFACTS,
        ),
    )
    answered = Observation(
        event_id=EventId(root="setup:0"),
        request_id=request_id,
        scope=request.scope,
        sequence=0,
        observed_at=1.0,
        status=status,
        terminal=terminal,
    )
    record = RequestRecord(
        request_id=request_id,
        request=request,
        payload_digest="setup",
        lifecycle=LifecycleClass.IDEMPOTENT_WRITE,
        phase=phase,
        sequence=0,
        observation=answered,
        until=Until(at=BOUND),
    )
    return state.model_copy(
        update={
            "records": RecordsState(
                records=(record,), recovery=RecoveryBarrier(epoch=1, phase=RecoveryPhase.READY)
            )
        }
    )


ANSWERS = st.sampled_from(
    [
        (RequestPhase.RECONCILING, ObservationStatus.UNKNOWN, False),
        (RequestPhase.DISPATCHED, ObservationStatus.FAILED, False),
    ]
)


@given(answer=ANSWERS, now=st.floats(min_value=1.0, max_value=1000.0))
def test_a_tick_blocks_a_silent_record_exactly_when_its_bound_has_passed(
    answer: tuple[RequestPhase, ObservationStatus, bool], now: float
) -> None:
    phase, status, terminal = answer
    state = _silent(phase, status, terminal=terminal)
    assert project(state).next_observe_at == BOUND

    after = step(state, ClockAdvanced(now_at=now))

    blocks = [r for r in after.requests if isinstance(r, BlockRequest)]
    assert (len(blocks) == 1) == (now >= BOUND)
    assert after.state.records.recovery.phase == RecoveryPhase.READY


def test_an_record_still_executing_has_no_bound_to_wait_for() -> None:
    state = _silent(RequestPhase.DISPATCHED, ObservationStatus.PENDING, terminal=False)
    state = state.model_copy(
        update={
            "records": state.records.model_copy(
                update={
                    "records": (state.records.records[0].model_copy(update={"observation": None}),)
                }
            )
        }
    )
    assert project(state).next_observe_at is None
    assert not step(state, ClockAdvanced(now_at=10_000.0)).requests


@given(
    status=st.sampled_from(list(ObservationStatus)), now=st.floats(min_value=1.0, max_value=1000.0)
)
def test_only_an_answered_open_record_is_blocked_and_progress_never_is(
    status: ObservationStatus, now: float
) -> None:
    """A DISPATCHED record still reporting progress is never blocked at its bound."""
    state = _silent(RequestPhase.DISPATCHED, status, terminal=False)
    blocks = [
        r for r in step(state, ClockAdvanced(now_at=now)).requests if isinstance(r, BlockRequest)
    ]
    retryable = status in (
        ObservationStatus.FAILED,
        ObservationStatus.REJECTED,
        ObservationStatus.CANCELLED,
    )
    assert (len(blocks) == 1) == (retryable and now >= BOUND)
    if not retryable:
        assert project(state).next_observe_at is None
