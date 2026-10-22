"""One evaluation slot admits one GPU-using gate, and queueing does not spend its budget.

Accuracy and benchmark share the run's evaluation slots. With one slot, a benchmark that
already holds it keeps every other candidate's accuracy queued: the queued gate is not
started, so a clock jump larger than its command budget cannot time it out, and the
reported queue depth is the number still waiting.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

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
from vs_runtime.api import (
    AccuracyEvaluation,
    AccuracyReceipt,
    BenchmarkEvaluation,
    BenchmarkFailureKind,
    BenchmarkObjective,
    PollingEvaluationExecutor,
    Workspace,
)
from vs_runtime.api.testing import FakeEvaluation, FakeEvaluationGate, FakeWorkspace, FakeWorkspaces
from vs_sim.api.testing import ManualClock, yield_until

if TYPE_CHECKING:
    from vs_sim.api import OutputSink

pytestmark = pytest.mark.regression

_CANDIDATES = 8
_BUDGET_S = 10_000.0
"""Longer than an accuracy or benchmark command budget; queueing must not spend it."""


class _OneGpu(FakeEvaluation):
    """A GPU whose second concurrent accuracy or benchmark is the lock-wait timeout."""

    def __init__(self) -> None:
        super().__init__()
        self.default_accuracy = AccuracyEvaluation(executed=True)
        self.default_benchmark = BenchmarkEvaluation(executed=True)
        self.overlaps = 0
        self._live = 0

    async def accuracy(
        self,
        workspace: Workspace,
        *,
        reuse: AccuracyReceipt | None = None,
        output: OutputSink,
    ) -> AccuracyEvaluation:
        if self._live:
            self.overlaps += 1
            return _timed_out_accuracy()
        self._live += 1
        try:
            return await super().accuracy(workspace, reuse=reuse, output=output)
        finally:
            self._live -= 1

    async def benchmark(
        self,
        workspace: Workspace,
        *,
        objectives: tuple[BenchmarkObjective, ...] = (),
        output: OutputSink,
    ) -> BenchmarkEvaluation:
        if self._live:
            self.overlaps += 1
            return _timed_out_benchmark()
        self._live += 1
        try:
            return await super().benchmark(workspace, objectives=objectives, output=output)
        finally:
            self._live -= 1


def _request(snapshot: str, key: str, *, build: bool = False) -> EvaluationRequest:
    digest = ContentDigest.sha256(key.encode())
    fingerprints = EvidenceFingerprints(
        candidate=digest, evaluator=digest, workload=digest, environment=digest
    )
    return EvaluationRequest(
        key=key,
        stages=tuple(
            EvaluationStep(
                name=kind.value,
                payload=SemanticEvaluationStage(
                    snapshot=snapshot, kind=kind, fingerprints=fingerprints
                ).model_dump(mode="json"),
            )
            for kind in (
                *((EvidenceKind.BUILD,) if build else ()),
                EvidenceKind.ACCURACY,
                EvidenceKind.BENCHMARK,
            )
        ),
    )


@pytest.mark.parametrize("build", [False, True])
async def test_one_slot_queues_gpu_gates_without_spending_their_budget(*, build: bool) -> None:
    """Eight candidates and one slot: one benchmark runs, seven wait, none times out."""
    evaluation = _OneGpu()
    held = evaluation.gate("benchmark", 0)
    clock = ManualClock()
    workspaces = FakeWorkspaces(
        FakeWorkspace(path=Path("/workspace")), supports_parallel_candidates=True
    )
    snapshot = await workspaces.root.snapshot("candidate")
    executor = PollingEvaluationExecutor(evaluation, workspaces, clock=clock)
    handles = tuple(f"candidate-{index}" for index in range(_CANDIDATES))
    for handle in handles:
        await executor.submit(_request(snapshot, handle, build=build), handle_id=handle)

    await yield_until(lambda: _entered(held), lambda entered: entered, "the admitted benchmark")
    clock.advance(_BUDGET_S)
    await _queued(executor, handles)

    assert evaluation.overlaps == 0
    assert len(evaluation.accuracy_calls) == 1
    assert len(evaluation.benchmark_calls) == 1
    held.release()
    for handle in handles:
        observed = await yield_until(
            lambda handle=handle: executor.inspect(handle),
            lambda seen: seen is not None and seen.state is EvaluationState.SUCCEEDED,
            f"{handle} finishes after it is admitted",
        )
        assert observed is not None
        assert observed.failure is None
    assert evaluation.overlaps == 0
    await executor.close()


def _timed_out_accuracy() -> AccuracyEvaluation:
    return AccuracyEvaluation(
        executed=True,
        feedback="timed out",
        operator_feedback="timed out",
        failure_kind=BenchmarkFailureKind.WORKLOAD,
    )


def _timed_out_benchmark() -> BenchmarkEvaluation:
    return BenchmarkEvaluation(
        executed=True,
        feedback="timed out",
        operator_feedback="timed out",
        failure_kind=BenchmarkFailureKind.WORKLOAD,
    )


async def _entered(held: FakeEvaluationGate) -> bool:
    return held.entered.is_set()


async def _queued(executor: PollingEvaluationExecutor, handles: tuple[str, ...]) -> None:
    """The slot holds one gate; every other candidate is queued, not running or finished."""
    snapshot = await executor.availability(ResourceRequirements())
    assert snapshot.capacity == 1
    assert snapshot.in_flight == 1
    assert snapshot.queue_depth == len(handles) - 1
    queued = 0
    running = 0
    for handle in handles:
        observed = await executor.inspect(handle)
        assert observed is not None
        assert observed.state in {EvaluationState.QUEUED, EvaluationState.RUNNING}
        if observed.state is EvaluationState.QUEUED:
            queued += 1
        else:
            running += 1
    assert queued == len(handles) - 1
    assert running == 1
