"""Host task profiles use the normal executor, retained revision and evidence ledger."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from vs_core.api import (
    EvidenceKind,
    HostFence,
    HostId,
    InspectOwnedJob,
    ObservationStatus,
    RequestId,
    SubmitMeasurement,
    Until,
)
from vs_evaluation.api import (
    ContentDigest,
    EvaluationRequest,
    EvaluationState,
    EvaluationStep,
    EvidenceFingerprints,
    EvidenceOutcome,
    ExecutorRejectedError,
    ResourceRequirements,
    SemanticEvaluationStage,
)
from vs_evaluation.api import EvidenceKind as StageKind
from vs_project.api import Project
from vs_runtime.api import (
    CommandProfileCapture,
    CommandResult,
    EvaluationAdmission,
    PollingEvaluationExecutor,
    ProfileField,
    Workspace,
)
from vs_runtime.api.core import (
    ExecutionContext,
    MeasurementRequests,
    ReceiptEvidenceLedger,
    ReceiptStore,
)
from vs_runtime.api.infrastructure import (
    TrustedEvaluationPlan,
)
from vs_runtime.api.slurm_evaluation_stack import ADMISSION, SCOPE, plan, submission
from vs_runtime.api.testing import (
    FakeCommands,
    FakeEvaluation,
    FakeWorkspace,
    FakeWorkspaces,
)
from vs_sim.api import DISCARD
from vs_sim.api.testing import ManualClock, SimThreads, examples, yield_until


class HeldLease:
    """Authority retained throughout this simulated run."""

    def verify(self, *, now_at: float) -> bool:
        del now_at
        return True

    def renew(self, *, now_at: float, lease_duration: float) -> None:
        pass


def execution_context(request: SubmitMeasurement | InspectOwnedJob) -> ExecutionContext:
    return ExecutionContext(
        fence=HostFence(host_id=HostId(root="host"), epoch=1),
        lease=HeldLease(),
        now_at=0.0,
        payload_digest=request.model_dump_json(),
        until=Until(at=100.0),
    )


TARGET = "a" * 40
FIELDS = (ProfileField.PREFILL_TIMING, ProfileField.DECODE_TIMING)


def _profile_command(workspace: Workspace) -> str:
    assert workspace.revision == TARGET
    return f"python {workspace.path}/profile.py"


def _stream(values: dict[str, float]) -> str:
    return "\n".join(
        json.dumps(item)
        for item in (
            {"kind": "hello", "protocol": 2, "metrics": {name: {"unit": "ms"} for name in values}},
            {"kind": "result", "values": values},
        )
    )


@pytest.mark.regression
@example(prefill=100.0, decode=20.0)
@settings(max_examples=examples(pr=5, nightly=30))
@given(
    prefill=st.floats(0, 1e6, allow_nan=False, allow_infinity=False),
    decode=st.floats(0, 1e6, allow_nan=False, allow_infinity=False),
)
async def test_host_profile_stores_named_revision_readings_without_promotion_facts(
    tmp_path_factory: pytest.TempPathFactory, prefill: float, decode: float
) -> None:
    root = FakeWorkspace(path=Path("/workspace"), revision="b" * 40, known_revisions={TARGET})
    workspaces = FakeWorkspaces(root, supports_parallel_candidates=True)
    commands = FakeCommands()
    values = {"prefill_timing": prefill, "decode_timing": decode}
    commands.script(CommandResult(output=_stream(values), exit_code=0))
    capture = CommandProfileCapture(
        commands,
        TrustedEvaluationPlan(
            profile_command="python profile.py", profile_fields=FIELDS, profile_timeout_seconds=30
        ),
        _profile_command,
    )
    evaluation = FakeEvaluation(run_id="run")
    executor = PollingEvaluationExecutor(
        evaluation, workspaces, clock=ManualClock(), profile_capture=capture
    )
    project = Project.open(tmp_path_factory.mktemp("profile-ledger"))
    store = ReceiptStore(project.state.state_store_namespace("run"), threads=SimThreads())
    requests = MeasurementRequests(executor, store)
    profile = plan(stages=("profile",), candidate=TARGET).model_copy(update={"purpose": "profile"})
    stage = profile.stages[0].model_copy(update={"required_outputs": tuple(FIELDS)})
    profile = profile.model_copy(update={"stages": (stage,)})
    submit = submission("profile", override=profile)
    try:
        submitted = await requests.execute(submit, execution_context(submit))
        resource = submitted.observation.observation.resource_id
        assert resource is not None, submitted
        await yield_until(
            lambda: executor.inspect(resource.root),
            lambda seen: seen is not None and seen.state.value in {"succeeded", "failed"},
            "profile settles",
        )
        inspect = InspectOwnedJob(
            request_id=RequestId(root="inspect"),
            scope=SCOPE,
            admission_id=ADMISSION,
            resource_id=resource,
        )
        result = await requests.execute(inspect, execution_context(inspect))
        target = result.observation.target
        assert target is not None
        assert target.observation.status is ObservationStatus.SUCCEEDED
        (ref,) = target.evidence
        assert ref.candidate == profile.candidate
        assert ref.purpose == "profile"
        assert ref.kind is EvidenceKind.PROFILING
        facts = target.evaluation_result
        assert facts is not None
        assert not facts.accuracy_passed
        assert ref.reading is None
        entry = ReceiptEvidenceLedger(store).lookup(ref.key)
        assert entry is not None
        assert entry.purpose == "profile"
        assert entry.evidence.outcome is EvidenceOutcome.PASSED
        assert {item.name: item.value for item in entry.evidence.metrics} == values
        assert all(item.direction is None for item in entry.evidence.metrics)
        (candidate,) = workspaces.candidates
        assert commands.calls[0].workspace is candidate
        assert commands.calls[0].argv[0] == "python"
        assert candidate.discarded
        assert root.revision == "b" * 40
        assert not evaluation.accuracy_calls
        assert not evaluation.benchmark_calls
    finally:
        await executor.close()


@pytest.mark.parametrize("text", ["", "{}", _stream({"prefill_timing": 1.0})])
async def test_incomplete_or_missing_declared_profile_fields_never_pass(text: str) -> None:
    commands = FakeCommands()
    commands.script(CommandResult(output=text, exit_code=0))
    capture = CommandProfileCapture(
        commands,
        TrustedEvaluationPlan(profile_command="profile", profile_fields=FIELDS),
        lambda _: "profile",
    )
    result = await capture.capture(FakeWorkspace(path=Path("/workspace")), output=DISCARD)
    assert result.failure is not None
    assert result.metrics == ()


async def test_profile_without_a_command_is_refused_before_opening_a_checkout() -> None:
    workspaces = FakeWorkspaces(
        FakeWorkspace(path=Path("/workspace")), supports_parallel_candidates=True
    )
    executor = PollingEvaluationExecutor(
        FakeEvaluation(run_id="run"), workspaces, clock=ManualClock()
    )
    available = await executor.availability(ResourceRequirements())
    assert "profile" not in available.supported_evidence_kinds
    # Submission through the measurement role rejects the unavailable stage immediately.
    digest = ContentDigest.sha256(b"profile")
    stage = SemanticEvaluationStage(
        snapshot=TARGET,
        kind=StageKind.PROFILE,
        fingerprints=EvidenceFingerprints(
            candidate=digest, evaluator=digest, workload=digest, environment=digest
        ),
    )
    with pytest.raises(ExecutorRejectedError, match="profile"):
        await executor.submit(
            EvaluationRequest(
                key="profile",
                stages=(EvaluationStep(name="profile", payload=stage.model_dump(mode="json")),),
            ),
            handle_id="profile",
        )
    assert not workspaces.candidates


async def test_executor_offers_exact_capture_fields_and_refuses_unsupported_work_before_launch() -> (
    None
):
    commands = FakeCommands()
    workspaces = FakeWorkspaces(
        FakeWorkspace(path=Path("/workspace")), supports_parallel_candidates=True
    )
    capture = CommandProfileCapture(
        commands,
        TrustedEvaluationPlan(profile_command="profile", profile_fields=FIELDS),
        lambda _: "profile",
    )
    executor = PollingEvaluationExecutor(
        FakeEvaluation(run_id="run"), workspaces, clock=ManualClock(), profile_capture=capture
    )
    available = await executor.availability(ResourceRequirements())
    assert available.supported_profile_fields == FIELDS
    assert "profile" in available.supported_evidence_kinds
    digest = ContentDigest.sha256(b"profile")
    stage = SemanticEvaluationStage(
        snapshot=TARGET,
        kind=StageKind.PROFILE,
        required_profile_fields=(ProfileField.HIP_API_TIMING,),
        fingerprints=EvidenceFingerprints(
            candidate=digest, evaluator=digest, workload=digest, environment=digest
        ),
    )
    with pytest.raises(ExecutorRejectedError, match="hip_api_timing"):
        await executor.submit(
            EvaluationRequest(
                key="profile",
                stages=(EvaluationStep(name="profile", payload=stage.model_dump(mode="json")),),
            ),
            handle_id="profile",
        )
    assert not commands.calls
    assert not workspaces.candidates


@pytest.mark.regression
@pytest.mark.parametrize("cancel_queued", [False, True])
async def test_profile_waits_for_a_benchmark_lease_without_starting_its_command(
    *, cancel_queued: bool
) -> None:
    """Normal profile submissions share admission, including queued cancellation."""
    commands = FakeCommands()
    commands.script(
        CommandResult(output=_stream({"prefill_timing": 2.0, "decode_timing": 1.0}), exit_code=0)
    )
    capture = CommandProfileCapture(
        commands,
        TrustedEvaluationPlan(
            profile_command="profile", profile_fields=FIELDS, profile_timeout_seconds=30
        ),
        _profile_command,
    )
    root = FakeWorkspace(path=Path("/workspace"), known_revisions={TARGET})
    workspaces = FakeWorkspaces(root, supports_parallel_candidates=True)
    evaluation = FakeEvaluation()
    held = evaluation.gate("benchmark", 0)
    admission = EvaluationAdmission()
    clock = ManualClock()
    executor = PollingEvaluationExecutor(
        evaluation, workspaces, clock=clock, admission=admission, profile_capture=capture
    )
    digest = ContentDigest.sha256(b"profile-admission")
    fingerprints = EvidenceFingerprints(
        candidate=digest, evaluator=digest, workload=digest, environment=digest
    )

    def request(kind: StageKind) -> EvaluationRequest:
        stage = SemanticEvaluationStage(
            snapshot=TARGET,
            kind=kind,
            fingerprints=fingerprints,
            required_profile_fields=FIELDS if kind is StageKind.PROFILE else (),
        )
        return EvaluationRequest(
            key=kind.value,
            stages=(EvaluationStep(name=kind.value, payload=stage.model_dump(mode="json")),),
        )

    async def benchmark_started() -> bool:
        return held.entered.is_set()

    try:
        await executor.submit(request(StageKind.BENCHMARK), handle_id="benchmark")
        await yield_until(benchmark_started, bool, "benchmark holds the evaluation slot")
        await executor.submit(request(StageKind.PROFILE), handle_id="profile")
        await yield_until(admission.counts, lambda counts: counts == (1, 1), "profile queues")
        clock.advance(10_000)
        queued = await executor.inspect("profile")
        assert queued is not None
        assert queued.state is EvaluationState.QUEUED
        assert not commands.calls
        assert len(workspaces.candidates) == 1
        if cancel_queued:
            await executor.cancel("profile")
            assert await admission.counts() == (1, 0)
        held.release()
        for handle in ("benchmark",) if cancel_queued else ("benchmark", "profile"):
            observed = await yield_until(
                lambda handle=handle: executor.inspect(handle),
                lambda seen: (
                    seen is not None
                    and seen.state in {EvaluationState.SUCCEEDED, EvaluationState.FAILED}
                ),
                f"{handle} settles after admission",
            )
            assert observed is not None
            assert observed.state is EvaluationState.SUCCEEDED, observed
        if cancel_queued:
            assert not commands.calls
        else:
            assert len(commands.calls) == 1
            assert commands.calls[0].timeout_seconds == 30
            timing = (await executor.poll("profile")).admission_timing
            assert timing == {"queued_at": 0.0, "admitted_at": 10_000.0}
        assert await admission.counts() == (0, 0)
        assert all(workspace.discarded for workspace in workspaces.candidates)
    finally:
        await executor.close()
