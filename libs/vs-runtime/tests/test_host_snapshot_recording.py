"""A benchmark records the host around it, on that revision, and nothing else does."""

from __future__ import annotations

from pathlib import Path

import pytest

from vs_evaluation.api import (
    ContentDigest,
    EvaluationRequest,
    EvaluationState,
    EvaluationStep,
    EvidenceFingerprints,
    EvidenceKind,
    EvidenceOutcome,
    EvidenceResultIdentity,
    SemanticEvaluationStage,
    TrustedEvidence,
    evidence_identity,
)
from vs_runtime.api import AccuracyEvaluation, BenchmarkEvaluation, PollingEvaluationExecutor
from vs_runtime.api.testing import FakeEvaluation, FakeWorkspace, FakeWorkspaces
from vs_sim.api import HostSample
from vs_sim.api.testing import FakeHostProbe, ManualClock, yield_until

pytestmark = pytest.mark.asyncio

_TERMINAL = frozenset({EvaluationState.SUCCEEDED, EvaluationState.FAILED})


def _request(snapshot: str, *kinds: EvidenceKind) -> EvaluationRequest:
    digest = ContentDigest.sha256(b"same")
    fingerprints = EvidenceFingerprints(
        candidate=digest, evaluator=digest, workload=digest, environment=digest
    )
    return EvaluationRequest(
        key="host-snapshot",
        stages=tuple(
            EvaluationStep(
                name=kind.value,
                payload=SemanticEvaluationStage(
                    snapshot=snapshot, kind=kind, fingerprints=fingerprints
                ).model_dump(mode="json"),
            )
            for kind in kinds
        ),
    )


def _sample(load: float) -> HostSample:
    return HostSample(
        load_1=load,
        load_5=load,
        load_15=load,
        cpu_count=8,
        memory_free_bytes=1_000,
        memory_pressure="40%",
        gpu_processes=("1 compile",),
    )


async def _evidence(
    executor: PollingEvaluationExecutor, handle_id: str, kind: EvidenceKind
) -> TrustedEvidence:
    observed = await yield_until(
        lambda: executor.inspect(handle_id),
        lambda seen: seen is not None and seen.state in _TERMINAL,
        "a terminal observation",
    )
    assert observed is not None
    stage = next(item for item in observed.stage_results if item.name == kind.value)
    return TrustedEvidence.model_validate(stage.result)


async def test_each_revision_records_the_host_around_its_own_benchmark() -> None:
    probe = FakeHostProbe((_sample(1.0), _sample(2.0), _sample(3.0), _sample(4.0)))
    evaluation = FakeEvaluation(default_accuracy=AccuracyEvaluation(executed=True))
    evaluation.script_benchmark(
        BenchmarkEvaluation(executed=True, row={"throughput": 10.0}),
        BenchmarkEvaluation(executed=True, row={"throughput": 10.0}),
    )
    workspaces = FakeWorkspaces(
        FakeWorkspace(path=Path("/workspace")), supports_parallel_candidates=True
    )
    executor = PollingEvaluationExecutor(
        evaluation, workspaces, clock=ManualClock(), host_probe=probe
    )
    snapshot = await workspaces.root.snapshot("candidate")
    await executor.submit(
        _request(snapshot, EvidenceKind.ACCURACY, EvidenceKind.BENCHMARK), handle_id="rev-a"
    )
    accuracy = await _evidence(executor, "rev-a", EvidenceKind.ACCURACY)
    first = await _evidence(executor, "rev-a", EvidenceKind.BENCHMARK)
    await executor.submit(_request(snapshot, EvidenceKind.BENCHMARK), handle_id="rev-b")
    second = await _evidence(executor, "rev-b", EvidenceKind.BENCHMARK)
    await executor.close()

    assert accuracy.host_snapshot is None
    assert first.host_snapshot is not None
    assert second.host_snapshot is not None
    assert first.host_snapshot.start.load_1 == 1.0
    assert first.host_snapshot.end.load_1 == 2.0
    assert second.host_snapshot.start.load_1 == 3.0
    assert second.host_snapshot.end.load_1 == 4.0
    assert first.evidence_id != second.evidence_id
    assert first.evidence_id == evidence_identity(
        SemanticEvaluationStage.model_validate(
            _request(snapshot, EvidenceKind.BENCHMARK).stages[0].payload
        ),
        EvidenceResultIdentity(
            evaluation_id=first.evaluation_id,
            outcome=EvidenceOutcome.PASSED,
            summary=first.semantic_summary,
            metrics=first.metrics,
            partial=first.partial_measurement,
        ),
    )


class _Unreadable:
    def sample(self) -> HostSample:
        message = "the host could not be read"
        raise OSError(message)


async def test_an_unreadable_host_is_recorded_unavailable_and_the_benchmark_still_counts() -> None:
    evaluation = FakeEvaluation()
    evaluation.script_benchmark(BenchmarkEvaluation(executed=True, row={"throughput": 1.0}))
    workspaces = FakeWorkspaces(
        FakeWorkspace(path=Path("/workspace")), supports_parallel_candidates=True
    )
    executor = PollingEvaluationExecutor(
        evaluation, workspaces, clock=ManualClock(), host_probe=_Unreadable()
    )
    snapshot = await workspaces.root.snapshot("candidate")
    await executor.submit(_request(snapshot, EvidenceKind.BENCHMARK), handle_id="rev")
    evidence = await _evidence(executor, "rev", EvidenceKind.BENCHMARK)
    await executor.close()

    assert evidence.outcome is EvidenceOutcome.PASSED
    assert evidence.host_snapshot is not None
    assert evidence.host_snapshot.start == evidence.host_snapshot.end
    assert evidence.host_snapshot.start.load_1 is None
    assert evidence.host_snapshot.start.gpu_processes is None
