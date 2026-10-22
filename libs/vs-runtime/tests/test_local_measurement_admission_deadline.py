"""The core's outer measurement limit follows the local executor's actual admission."""

from pathlib import Path

import pytest

import vs_core.api as core
from vs_project.api import Project
from vs_runtime.api import AccuracyEvaluation, EvaluationAdmission, PollingEvaluationExecutor
from vs_runtime.api.core import MeasurementRequests, ReceiptStore
from vs_runtime.api.slurm_evaluation_stack import plan
from vs_runtime.api.testing import FakeEvaluation, FakeWorkspace, FakeWorkspaces
from vs_sim.api.testing import ManualClock, SimThreads, yield_until

from .test_evaluation_requests import context_for


@pytest.mark.regression
async def test_outer_measurement_limit_excludes_a_live_executor_lease_wait(tmp_path: Path) -> None:
    clock = ManualClock()
    admission = EvaluationAdmission()
    evaluation = FakeEvaluation(default_accuracy=AccuracyEvaluation(executed=True))
    accuracy = evaluation.gate("accuracy", 0)
    commit = "a" * 40
    root = FakeWorkspace(path=Path("/workspace"))
    root.add_retained_revision(commit)
    executor = PollingEvaluationExecutor(
        evaluation,
        FakeWorkspaces(root, supports_parallel_candidates=True),
        clock=clock,
        admission=admission,
    )
    project = Project.open(tmp_path)
    requests = MeasurementRequests(
        executor, ReceiptStore(project.state.state_store_namespace("run"), threads=SimThreads())
    )
    initial = core.initial_state()
    initial = initial.model_copy(
        update={
            "run": initial.run.model_copy(
                update={
                    "deadline_at": 1_000_000.0,
                    "limits": initial.run.limits.model_copy(
                        update={"reconciliation_bound": 1_000_000.0}
                    ),
                }
            )
        }
    )
    asked = core.Measure(
        decision_id=core.DecisionId(root="measure"),
        scope=core.Scope(owner=initial.run.run_id, generation=initial.run.generation),
        plan=plan(candidate=commit),
    )
    accepted = core.step(
        initial,
        core.DecisionSubmitted(
            decision=core.Wait(request=asked, until=core.Within(limit=100.0)),
            expected_revision=initial.revision,
        ),
    )
    (submission,) = [r for r in accepted.requests if isinstance(r, core.SubmitMeasurement)]
    assert submission.request_id is not None
    state = core.step(
        accepted.state, core.DispatchAuthorized(request_id=submission.request_id)
    ).state
    await admission.acquire("agent-validation")
    try:
        submitted = await requests.execute(submission, context_for(submission, now_at=0.0))
        state = core.step(state, submitted.observation).state
        for event in submitted.owner_events:
            state = core.step(state, event).state
        clock.advance(10_000.0)
        queued = core.step(state, core.ClockAdvanced(now_at=clock.now()))
        assert not [e for e in queued.events if isinstance(e, core.Failed)]
        assert not [r for r in queued.requests if isinstance(r, core.CancelOwnedJob)]
        assert not accuracy.entered.is_set()
        await admission.release("agent-validation")

        async def entered() -> bool:
            return accuracy.entered.is_set()

        await yield_until(entered, bool, "official gate is admitted after the agent")
        resource = submitted.observation.observation.resource_id
        assert resource is not None
        query = core.ObserveOwnedJob(
            request_id=core.RequestId(root="observe-admission"),
            scope=submission.scope,
            admission_id=submission.admission_id,
            resource_id=resource,
        )
        running = await requests.execute(query, context_for(query, now_at=clock.now()))
        state = queued.state
        for event in running.owner_events:
            state = core.step(state, event).state
        clock.advance(99.0)
        within = core.step(state, core.ClockAdvanced(now_at=clock.now()))
        assert not [e for e in within.events if isinstance(e, core.Failed)]
        clock.advance(1.0)
        expired = core.step(within.state, core.ClockAdvanced(now_at=clock.now()))
        assert any(
            isinstance(e, core.Failed) and e.reason is core.FailureReason.TIMED_OUT
            for e in expired.events
        )
        assert any(isinstance(r, core.CancelOwnedJob) for r in expired.requests)
    finally:
        await admission.release("agent-validation")
        await executor.close()
