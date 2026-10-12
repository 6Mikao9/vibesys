"""An inspection that never reported before a crash is issued again by the next recovery."""

import json
from hashlib import sha256

import pytest

from vs_core.api import (
    Access,
    EnsureSession,
    InspectRequest,
    LifecycleClass,
    RecordsState,
    RecoveryBarrier,
    RecoveryStarted,
    RequestId,
    RequestPhase,
    RequestRecord,
    RoleId,
    RunStatus,
    Scope,
    SessionId,
    SessionSpec,
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
        deadline_at=100.0,
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
        reconcile_deadline_at=100.0,
    )


def _lost_inspection(target: RequestRecord, phase: RequestPhase) -> RequestRecord:
    request_id = RequestId(root="inspection")
    request = InspectRequest(
        request_id=request_id,
        scope=target.request.scope,
        deadline_at=100.0,
        target=target.request_id,
    )
    return RequestRecord(
        request_id=request_id,
        request=request,
        payload_digest=_digest(request),
        lifecycle=LifecycleClass.QUERY,
        phase=phase,
        reconcile_deadline_at=100.0,
    )


@pytest.mark.parametrize("phase", [RequestPhase.DISPATCHED, RequestPhase.RECONCILING])
def test_an_inspection_that_never_reported_is_prepared_again(phase: RequestPhase) -> None:
    target = _session_record()
    lost = _lost_inspection(target, phase)
    state = initial_state()
    state = state.model_copy(
        update={
            "records": RecordsState(records=(target, lost), recovery=RecoveryBarrier()),
            "run": state.run.model_copy(update={"status": RunStatus.PAUSED}),
        }
    )
    result = step(state, RecoveryStarted(epoch=1, now_at=11.0))
    phases = {row.request_id: row.phase for row in result.state.records.records}
    assert phases[lost.request_id] == RequestPhase.PREPARED
    assert phases[target.request_id] == target.phase
