"""A reconciliation request that never reported before a crash is issued again by the next recovery."""

import json
from hashlib import sha256

from hypothesis import given
from hypothesis import strategies as st

from vs_core.api import (
    Access,
    BlockRequest,
    CancelOwnedResource,
    EnsureSession,
    FailureReason,
    InspectRequest,
    LifecycleClass,
    RecordsState,
    RecoveryBarrier,
    RecoveryStarted,
    RequestId,
    RequestPhase,
    RequestRecord,
    ResourceId,
    RoleId,
    RunStatus,
    Scope,
    SessionId,
    SessionSpec,
    Until,
    Value,
    initial_state,
    step,
)


def _digest(value: Value) -> str:
    return sha256(
        json.dumps(value.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _session_record() -> RequestRecord:
    state = initial_state()
    request_id = RequestId(root="target")
    request = EnsureSession(
        request_id=request_id,
        scope=Scope(owner=state.run.run_id, generation=0),
        spec=SessionSpec(
            session_id=SessionId(root="target"),
            role_id=RoleId(root="worker"),
            policy="reuse",
            lifetime="owner",
            access=Access.WRITE_ARTIFACTS,
        ),
    )
    return RequestRecord(
        request_id=request_id,
        request=request,
        payload_digest=_digest(request),
        lifecycle=LifecycleClass.IDEMPOTENT_WRITE,
        phase=RequestPhase.DISPATCHED,
        until=Until(at=100.0),
        addressed_by=(),
    )


def _lost_request(target: RequestRecord, kind: str, phase: RequestPhase) -> RequestRecord:
    request_id = RequestId(root=f"lost-{kind}")
    scope = target.request.scope
    request: InspectRequest | CancelOwnedResource | BlockRequest
    match kind:
        case "inspect":
            request = InspectRequest(request_id=request_id, scope=scope, target=target.request_id)
            lifecycle = LifecycleClass.QUERY
        case "cancel":
            request = CancelOwnedResource(
                request_id=request_id,
                scope=scope,
                target=target.request_id,
                resource_id=ResourceId(root="target-resource"),
            )
            lifecycle = LifecycleClass.IDEMPOTENT_WRITE
        case _:
            request = BlockRequest(
                request_id=request_id,
                scope=scope,
                target=target.request_id,
                reason=FailureReason.TIMED_OUT,
            )
            lifecycle = LifecycleClass.IDEMPOTENT_WRITE
    return RequestRecord(
        request_id=request_id,
        request=request,
        payload_digest=_digest(request),
        lifecycle=lifecycle,
        phase=phase,
        until=Until(at=100.0),
        addressed_by=(),
    )


@given(
    kind=st.sampled_from(["inspect", "cancel", "block"]),
    phase=st.sampled_from(
        [RequestPhase.PREPARED, RequestPhase.DISPATCHED, RequestPhase.RECONCILING]
    ),
    restart_at=st.floats(min_value=0.0, max_value=10_000.0, allow_nan=False),
)
def test_a_reconciliation_request_that_never_reported_goes_out_again_with_a_bound_from_the_restart(
    kind: str, phase: RequestPhase, restart_at: float
) -> None:
    """An inspection, a cancellation or a block; in flight or only prepared; any downtime.

    Nothing ends an inspection at its bound, so one sent out with a bound that passed while
    the host was down would be dropped unanswered and hold the run open for good. A block or
    a cancellation the dead host sent was neither checked nor sent again, so it stayed in
    flight for good: a run that ended blocked and died once more never closed (#2529).
    """
    target = _session_record()
    lost = _lost_request(target, kind, phase)
    state = initial_state()
    state = state.model_copy(
        update={
            "records": RecordsState(hosts=(), records=(target, lost), recovery=RecoveryBarrier()),
            "run": state.run.model_copy(update={"status": RunStatus.PAUSED}),
        }
    )
    result = step(state, RecoveryStarted(epoch=1, now_at=restart_at))
    rows = {row.request_id: row for row in result.state.records.records}
    now_at = max(result.state.run.now_at, restart_at)
    assert rows[lost.request_id].phase == RequestPhase.PREPARED
    assert rows[lost.request_id].until.at >= now_at + state.run.limits.reconciliation_bound
    assert rows[target.request_id].phase == target.phase
