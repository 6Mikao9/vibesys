"""A leaf rejection rolls back speculative state without manufacturing acceptance."""

import vs_core.api as core


def test_leaf_budget_rejection_preserves_no_charges_requests_or_acceptance() -> None:
    state = core.initial_state()
    decision = core.StartAttempt(
        decision_id=core.DecisionId(root="rejected"),
        scope=core.Scope(owner=state.run.run_id, generation=0),
        attempt_id=core.AttemptId(root="attempt"),
        item_id=core.ItemId(root="item"),
        workspace=core.WorkspacePlan(
            mode=core.WorkspaceMode.EXCLUSIVE_ROOT, base=state.run.facts.baseline
        ),
        budget=core.AttemptBudget(),
    )
    signal = core.AttemptRequested(
        request=core.AttemptRequest(
            decision_id=decision.decision_id,
            attempt_id=decision.attempt_id,
            item_id=decision.item_id,
            generation=0,
            admission_charge=1,
        )
    )
    rejection = core.Rejected(
        decision_id=decision.decision_id,
        code=core.RejectionCode.BUDGET,
        path=("max_attempts",),
        detail="admission exhausted",
    )
    request = core.InspectRequest(scope=decision.scope, target=core.RequestId(root="target"))
    result = core.trace_step(
        state,
        core.DecisionSubmitted(
            decision=core.Wait(request=decision, until=core.Within(limit=100.0)),
            expected_revision=0,
        ),
        core.ReducerTrace(
            frames=(
                core.TraceFrame(
                    signal=signal,
                    change=core.SchedulingChange(
                        state=state.scheduling.model_copy(update={"released_slot_seconds": 1.0}),
                        requests=(core.within(request, 10.0),),
                        events=(rejection,),
                    ),
                ),
            )
        ),
    )
    assert result.events == (rejection,)
    assert result.requests == ()
    assert result.state.scheduling == state.scheduling
    assert result.state.records == state.records
    assert result.state.run.receipts[0].feedback == rejection
