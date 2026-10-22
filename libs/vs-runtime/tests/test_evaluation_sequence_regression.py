"""A measurement retains one resource slot between separately requested executor jobs.

This scenario uses the executor API present at 094c5ad2b. Admission metadata is
copied onto the request so the base runs the same work and fails on its observable
queue counts, rather than on a missing constructor argument or new API export.
"""

from pathlib import Path

import pytest

from vs_evaluation.api import (
    ContentDigest,
    EvaluationRequest,
    EvaluationState,
    EvaluationStep,
    EvidenceFingerprints,
    EvidenceKind,
    ResourceRequirements,
    SemanticEvaluationStage,
)
from vs_runtime.api import AccuracyEvaluation, BenchmarkEvaluation, PollingEvaluationExecutor
from vs_runtime.api.testing import FakeEvaluation, FakeWorkspace, FakeWorkspaces
from vs_sim.api.testing import ManualClock, yield_until


def request(snapshot: str, key: str, kind: EvidenceKind) -> EvaluationRequest:
    digest = ContentDigest.sha256(key.encode())
    return EvaluationRequest(
        key=key,
        owner_scope="official-attempt",
        stages=(
            EvaluationStep(
                name=kind.value,
                payload=SemanticEvaluationStage(
                    snapshot=snapshot,
                    kind=kind,
                    fingerprints=EvidenceFingerprints(
                        candidate=digest, evaluator=digest, workload=digest, environment=digest
                    ),
                ).model_dump(mode="json"),
            ),
        ),
    )


@pytest.mark.regression
async def test_agents_stay_queued_between_an_official_measurements_jobs() -> None:
    evaluation = FakeEvaluation(
        default_accuracy=AccuracyEvaluation(executed=True),
        default_benchmark=BenchmarkEvaluation(executed=True),
    )
    first = evaluation.gate("accuracy", 0)
    workspaces = FakeWorkspaces(
        FakeWorkspace(path=Path("/workspace")), supports_parallel_candidates=True
    )
    snapshot = await workspaces.root.snapshot("candidate")
    executor = PollingEvaluationExecutor(evaluation, workspaces, clock=ManualClock())
    try:
        initial = request(snapshot, "official-accuracy", EvidenceKind.ACCURACY).model_copy(
            update={"admission": {"key": "official", "retain": True}}
        )
        await executor.submit(initial, handle_id=initial.key)

        async def entered() -> bool:
            return first.entered.is_set()

        await yield_until(entered, bool, "official accuracy starts")
        agents = tuple(f"agent-{i}" for i in range(8))
        for agent in agents:
            await executor.submit(request(snapshot, agent, EvidenceKind.ACCURACY), handle_id=agent)
        await yield_until(
            lambda: executor.availability(ResourceRequirements()),
            lambda seen: seen.queue_depth == len(agents),
            "all local validations queue",
        )
        first.release()
        await yield_until(
            lambda: executor.inspect(initial.key),
            lambda seen: seen is not None and seen.state is EvaluationState.SUCCEEDED,
            "official accuracy ends",
        )
        between = await executor.availability(ResourceRequirements())
        assert between.in_flight == 1
        assert between.queue_depth == len(agents)
        assert len(evaluation.accuracy_calls) == 1
        final = request(snapshot, "official-benchmark", EvidenceKind.BENCHMARK).model_copy(
            update={"admission": {"key": "official", "retain": False}}
        )
        await executor.submit(final, handle_id=final.key)
        for handle in (final.key, *agents):
            await yield_until(
                lambda handle=handle: executor.inspect(handle),
                lambda seen: seen is not None and seen.state is EvaluationState.SUCCEEDED,
                f"{handle} finishes",
            )
        assert len(evaluation.benchmark_calls) == 1
        assert len(evaluation.accuracy_calls) == len(agents) + 1
    finally:
        await executor.close()
