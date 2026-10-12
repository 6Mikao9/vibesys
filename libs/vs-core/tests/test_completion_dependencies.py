"""A queued decision still owns the requests of its later admission."""

import vs_core.api as core
from vs_core.api import (
    AttemptBudget,
    AttemptId,
    AttemptRequest,
    AttemptRequested,
    ClockAdvanced,
    DecisionId,
    DecisionSubmitted,
    ItemId,
    ReducerTrace,
    SchedulingChange,
    Scope,
    StartAttempt,
    TraceFrame,
    WorkspaceMode,
    WorkspacePlan,
    trace_step,
)


def queued_start(state: core.CoreState) -> tuple[core.Transition, StartAttempt]:
    start = StartAttempt(
        decision_id=DecisionId(root="queued"),
        scope=Scope(owner=state.run.run_id, generation=0),
        attempt_id=AttemptId(root="attempt"),
        item_id=ItemId(root="item"),
        workspace=WorkspacePlan(mode=WorkspaceMode.EXCLUSIVE_ROOT, base=state.run.facts.baseline),
        budget=AttemptBudget(),
    )
    signal = AttemptRequested(
        request=AttemptRequest(
            decision_id=start.decision_id,
            attempt_id=start.attempt_id,
            item_id=start.item_id,
            generation=0,
            admission_charge=1,
        )
    )
    result = trace_step(
        state,
        DecisionSubmitted(
            decision=core.Wait(request=start, until=core.Within(limit=100.0)), expected_revision=0
        ),
        ReducerTrace(
            frames=(TraceFrame(signal=signal, change=SchedulingChange(state=state.scheduling)),)
        ),
    )
    return result, start


def test_later_admission_requests_keep_the_queued_decision_owner() -> None:
    queued, start = queued_start(core.initial_state())
    request = core.EnsureWorkspace(
        scope=start.scope,
        attempt=core.AttemptRef(attempt_id=start.attempt_id, generation=0),
        plan=start.workspace,
    )
    admission = core.AdmitAttempt(
        request=core.AttemptRequest(
            decision_id=start.decision_id,
            attempt_id=start.attempt_id,
            item_id=start.item_id,
            generation=0,
            admission_charge=1,
        )
    )
    assert isinstance(admission.request, core.AttemptRequest)
    admitted = core.AttemptAdmitted(
        request=admission.request,
        admission_id=start.decision_id,
        workspace=start.workspace,
        budget=start.budget,
    )
    event = ClockAdvanced(now_at=10.0)
    result = trace_step(
        queued.state,
        event,
        ReducerTrace(
            frames=(
                TraceFrame(
                    signal=event,
                    change=SchedulingChange(state=queued.state.scheduling, signals=(admission,)),
                ),
                TraceFrame(
                    signal=admitted,
                    change=core.AttemptsChange(
                        state=queued.state.attempts, requests=(core.within(request, 100.0),)
                    ),
                ),
            )
        ),
    )
    assert getattr(result.requests[0], "decision_id", None) == start.decision_id
    assert result.state.run.receipts[0].request_ids == (result.requests[0].request_id,)
