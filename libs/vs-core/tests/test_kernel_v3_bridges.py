"""Kernel notifications and canonical authority independent of leaf policy."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

import vs_core.api as core


@given(now=st.integers(min_value=0, max_value=10000))
def test_stop_control_records_canonical_result_before_run_drain_and_replays(now: int) -> None:
    state = core.initial_state()
    proposal = core.RunResultProposal(outcome="cancelled", reason="explicit stop")
    control = core.ControlInput(
        control_id=core.ControlId(root="stop"), action="stop", mode=core.StopMode.DRAIN
    )
    event = core.RunControlEvent(control=control, now_at=float(now), result=proposal)
    drain = core.AdmissionControl(action="drain")
    result = core.trace_step(
        state,
        event,
        core.ReducerTrace(
            frames=(
                core.TraceFrame(
                    signal=drain,
                    change=core.SchedulingChange(
                        state=state.scheduling, signals=(core.RunDrained(),)
                    ),
                ),
            )
        ),
    )
    assert result.state.run.status == core.RunStatus.TERMINAL
    assert result.state.run.result == proposal
    receipt = result.state.run.receipts[0]
    assert isinstance(receipt.decision, core.Finish)
    assert isinstance(receipt.feedback, core.Accepted)
    assert receipt.decision_id == core.DecisionId(root="control:stop")
    assert receipt.decision.result == proposal
    assert result.state.run.controls == (control,)
    ended = [row for row in result.events if isinstance(row, core.RunEnded)]
    assert len(ended) == 1
    assert ended[0].result == proposal
    assert result.events[-1] == ended[0]
    restored = core.CoreState.model_validate_json(result.state.model_dump_json())
    replay = core.step(restored, event)
    assert replay.requests == replay.events == ()
    conflicting = event.model_copy(
        update={"result": core.RunResultProposal(outcome="failure", reason="changed")}
    )
    with pytest.raises(core.ContractError, match="result conflict"):
        core.step(restored, conflicting)


@given(identity=st.text(alphabet="abcdef0123456789", min_size=1, max_size=20))
def test_queued_reopen_retirement_uses_queue_admission_over_old_parked_owner(identity: str) -> None:
    state = core.initial_state()
    attempt = core.AttemptRef(attempt_id=core.AttemptId(root="attempt"), generation=0)
    queued_id = core.DecisionId(root=f"queued:{identity}")
    owner = core.AttemptView(
        attempt_id=attempt.attempt_id,
        item_id=core.ItemId(root="item"),
        generation=0,
        phase=core.AttemptPhase.PARKED,
        workspace=core.WorkspacePlan(
            mode=core.WorkspaceMode.EXCLUSIVE_ROOT, base=state.run.facts.baseline
        ),
        budget=core.AttemptBudget(),
        admission_id=core.DecisionId(root="old-admission"),
    )
    queue = core.AttemptReopenRequest(
        decision_id=queued_id, request_id=core.RequestId(root="reopen"), attempt=attempt
    )
    state = state.model_copy(
        update={
            "attempts": core.AttemptsState(attempts=(owner,)),
            "scheduling": core.SchedulingState(queue=(queue,)),
        }
    )
    decision = core.Withdraw(
        decision_id=core.DecisionId(root="withdraw"),
        scope=core.Scope(owner=state.run.run_id, generation=0),
        target=attempt,
        disposition=core.Cancel(),
    )
    signal = core.RetireRequested(
        attempt=attempt,
        disposition="cancel",
        requested_at=state.run.now_at,
        authority=core.RequestId(root="withdraw:withdraw"),
        admission_id=queued_id,
    )
    result = core.trace_step(
        state,
        core.DecisionSubmitted(
            decision=core.Wait(request=decision, until=core.Within(limit=100.0)),
            expected_revision=0,
        ),
        core.ReducerTrace(
            frames=(
                core.TraceFrame(signal=signal, change=core.AttemptsChange(state=state.attempts)),
            )
        ),
    )
    assert isinstance(result.events[0], core.Accepted)
    assert result.state.attempts.attempts[0].admission_id == owner.admission_id
    assert result.requests == ()
