"""One evaluation slot admits one GPU-using gate, and queueing does not spend its budget.

Accuracy and benchmark share the run's evaluation slots. With one slot, a benchmark that
already holds it keeps every other candidate's accuracy queued: the queued gate is not
started, so a clock jump larger than its command budget cannot time it out, and the
reported queue depth is the number still waiting.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from vs_core.api import EvaluationLease
from vs_evaluation.api import (
    ContentDigest,
    EvaluationRequest,
    EvaluationState,
    EvaluationStep,
    EvidenceFingerprints,
    EvidenceKind,
    PollPhase,
    ResourceRequirements,
    SemanticEvaluationStage,
)
from vs_evaluation.api.queue_status import EvaluationOccupancy, LocalQueueStatusReader
from vs_evaluation.api.testing import InMemoryEvaluationNamespace
from vs_project.api import Project
from vs_runtime.api import (
    AccuracyEvaluation,
    AccuracyReceipt,
    BenchmarkEvaluation,
    BenchmarkFailureKind,
    BenchmarkObjective,
    EvaluationAdmission,
    EvaluationRetries,
    PollingEvaluationExecutor,
    RetryingEvaluationExecutor,
    Workspace,
)
from vs_runtime.api.core import MeasurementRequests, ReceiptStore
from vs_runtime.api.slurm_evaluation_stack import submission
from vs_runtime.api.testing import FakeEvaluation, FakeEvaluationGate, FakeWorkspace, FakeWorkspaces
from vs_sim.api import DISCARD, yield_now
from vs_sim.api.testing import ManualClock, SimThreads, yield_until

from .test_evaluation_requests import close_request, context_for

if TYPE_CHECKING:
    from vs_sim.api import OutputSink

pytestmark = pytest.mark.regression

_CANDIDATES = 8
_BUDGET_S = 10_000.0
"""Longer than an accuracy or benchmark command budget; queueing must not spend it."""


class _OneGpu(FakeEvaluation):
    """A GPU whose second concurrent accuracy or benchmark is the lock-wait timeout."""

    def __init__(self, capacity: int = 1, *, interleave: bool = False) -> None:
        super().__init__()
        self.default_accuracy = AccuracyEvaluation(executed=True)
        self.default_benchmark = BenchmarkEvaluation(executed=True)
        self.overlaps = 0
        self.peak = 0
        self.started: list[tuple[str, str | None]] = []
        self.capacity = capacity
        self.interleave = interleave
        self._live = 0

    async def accuracy(
        self,
        workspace: Workspace,
        *,
        reuse: AccuracyReceipt | None = None,
        output: OutputSink,
    ) -> AccuracyEvaluation:
        if self._live >= self.capacity:
            self.overlaps += 1
            return _timed_out_accuracy()
        self._live += 1
        self.peak = max(self.peak, self._live)
        self.started.append(("accuracy", workspace.revision))
        try:
            if self.interleave:
                await yield_now()
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
        if self._live >= self.capacity:
            self.overlaps += 1
            return _timed_out_benchmark()
        self._live += 1
        self.peak = max(self.peak, self._live)
        self.started.append(("benchmark", workspace.revision))
        try:
            if self.interleave:
                await yield_now()
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


async def _finished(executor: PollingEvaluationExecutor, handle: str) -> None:
    await yield_until(
        lambda: executor.inspect(handle),
        lambda seen: seen is not None and seen.state is EvaluationState.SUCCEEDED,
        f"{handle} succeeds",
    )


def _member(snapshot: str, index: int, *, final: bool, benchmark_first: bool) -> EvaluationRequest:
    request = _request(snapshot, f"official-{index}")
    stage = request.stages[int(benchmark_first if index == 0 else not benchmark_first)]
    return request.model_copy(
        update={
            "stages": (stage,),
            "owner_scope": "official-attempt",
            "admission": {"key": "official-sequence", "retain": not final},
        }
    )


@example(waiters=8, benchmark_first=False)
@example(waiters=8, benchmark_first=True)
@given(waiters=st.integers(min_value=1, max_value=12), benchmark_first=st.booleans())
async def test_official_sequence_rejoins_fifo_between_jobs(
    waiters: int, *, benchmark_first: bool
) -> None:
    """An official's next member follows the local validations already waiting for its slot."""
    evaluation = _OneGpu()
    first_kind = "benchmark" if benchmark_first else "accuracy"
    first = evaluation.gate(first_kind, 0)
    occupancy = EvaluationOccupancy(SimThreads())
    admission = EvaluationAdmission(on_counts=occupancy.note_executor)
    reader = LocalQueueStatusReader(occupancy)
    workspaces = FakeWorkspaces(
        FakeWorkspace(path=Path("/workspace")), supports_parallel_candidates=True
    )
    snapshot = await workspaces.root.snapshot("candidate")
    executor = PollingEvaluationExecutor(
        evaluation, workspaces, clock=ManualClock(), admission=admission
    )
    try:
        await executor.submit(
            _member(snapshot, 0, final=False, benchmark_first=benchmark_first),
            handle_id="official-0",
        )
        await yield_until(lambda: _entered(first), bool, "first official gate starts")
        handles = tuple(f"agent-{i}" for i in range(waiters))
        for handle in handles:
            await executor.submit(_request(snapshot, handle), handle_id=handle)
        await yield_until(admission.counts, lambda counts: counts == (1, waiters), "agents queue")
        agent_gate = evaluation.gate("accuracy", 0 if benchmark_first else 1)
        first.release()
        await _finished(executor, "official-0")
        await yield_until(lambda: _entered(agent_gate), bool, "oldest validation starts")
        assert await admission.counts() == (1, waiters - 1)
        second_kind = "accuracy" if benchmark_first else "benchmark"
        second = evaluation.gate(second_kind, waiters)
        await executor.submit(
            _member(snapshot, 1, final=True, benchmark_first=benchmark_first),
            handle_id="official-1",
        )
        await yield_until(
            admission.counts, lambda counts: counts == (1, waiters), "successor queues"
        )
        waiting = await executor.inspect("official-1")
        assert waiting is not None
        assert waiting.state is EvaluationState.QUEUED
        agent_gate.release()
        await yield_until(lambda: _entered(second), bool, "second official gate starts")
        assert await admission.counts() == (1, 0)
        assert reader.read().contention.jobs_running == 1
        assert reader.read().contention.jobs_waiting == 0
        assert len(evaluation.accuracy_calls) + len(evaluation.benchmark_calls) == 2 + 2 * waiters
        second.release()
        for handle in ("official-1", *handles):
            await _finished(executor, handle)
        assert evaluation.overlaps == 0
        assert await admission.counts() == (0, 0)
    finally:
        await executor.close()


@pytest.mark.parametrize("exit_at", ["queued", "between", "running", "close"])
async def test_an_interrupted_sequence_returns_the_shared_slot(exit_at: str) -> None:
    """Scope cleanup can name any member, even a completed member of a live sequence."""
    evaluation = _OneGpu()
    first = evaluation.gate("accuracy", 0)
    admission = EvaluationAdmission()
    workspaces = FakeWorkspaces(
        FakeWorkspace(path=Path("/workspace")), supports_parallel_candidates=True
    )
    snapshot = await workspaces.root.snapshot("candidate")
    executor = PollingEvaluationExecutor(
        evaluation, workspaces, clock=ManualClock(), admission=admission
    )
    try:
        if exit_at == "queued":
            await admission.acquire("profile")
        await executor.submit(
            _member(snapshot, 0, final=False, benchmark_first=False), handle_id="official-0"
        )
        if exit_at == "queued":
            await yield_until(admission.counts, lambda counts: counts == (1, 1), "official queues")
        else:
            await yield_until(lambda: _entered(first), bool, "official starts")
            first.release()
            await _finished(executor, "official-0")
        if exit_at == "running":
            second = evaluation.gate("benchmark", 0)
            await executor.submit(
                _member(snapshot, 1, final=True, benchmark_first=False), handle_id="official-1"
            )
            await yield_until(lambda: _entered(second), bool, "second official starts")
        if exit_at == "close":
            await executor.close()
        else:
            await executor.cancel("official-0")
        if exit_at == "queued":
            assert await admission.counts() == (1, 0)
            await admission.release("profile")
        assert await admission.counts() == (0, 0)
        first.release()
        await executor.submit(_request(snapshot, "next-agent"), handle_id="next-agent")
        await _finished(executor, "next-agent")
        assert evaluation.overlaps == 0
    finally:
        await executor.close()


@example(capacity=1, candidates=8, benchmark_first=False)
@example(capacity=3, candidates=8, benchmark_first=True)
@given(
    capacity=st.integers(min_value=1, max_value=3),
    candidates=st.integers(min_value=2, max_value=8),
    benchmark_first=st.booleans(),
)
async def test_mixed_official_sequences_and_agent_gates_share_capacity(
    capacity: int, candidates: int, *, benchmark_first: bool
) -> None:
    """Generated interleavings preserve official stage order and the shared N-slot bound."""
    evaluation = _OneGpu(capacity, interleave=True)
    counts: list[tuple[int, int]] = []
    admission = EvaluationAdmission(
        capacity, on_counts=lambda running, waiting: counts.append((running, waiting))
    )
    workspaces = FakeWorkspaces(
        FakeWorkspace(path=Path("/workspace")), supports_parallel_candidates=True
    )
    snapshots = tuple([await workspaces.root.snapshot(f"candidate-{i}") for i in range(candidates)])
    agents = tuple([await workspaces.root.snapshot(f"agent-{i}") for i in range(candidates)])
    executor = PollingEvaluationExecutor(
        evaluation, workspaces, clock=ManualClock(), admission=admission
    )

    async def official(index: int) -> None:
        for member in range(2):
            handle = f"official-{index}-{member}"
            job = _member(
                snapshots[index], member, final=member == 1, benchmark_first=benchmark_first
            )
            job = job.model_copy(
                update={
                    "key": handle,
                    "admission": {"key": f"sequence-{index}", "retain": member == 0},
                }
            )
            await executor.submit(job, handle_id=handle)
            await _finished(executor, handle)

    async def agent(index: int) -> None:
        handle = f"agent-{index}"
        await executor.submit(_request(agents[index], handle), handle_id=handle)
        await _finished(executor, handle)

    tasks = tuple(
        asyncio.create_task(work(index))
        for index in range(candidates)
        for work in (official, agent)
    )
    try:
        await asyncio.gather(*tasks)
        assert evaluation.overlaps == 0
        assert evaluation.peak == capacity
        assert all(running <= capacity for running, _waiting in counts)
        assert await admission.counts() == (0, 0)
        for snapshot in snapshots:
            positions = [
                i for i, (_kind, revision) in enumerate(evaluation.started) if revision == snapshot
            ]
            assert len(positions) == 2
            kinds = tuple(evaluation.started[i][0] for i in positions)
            assert kinds == (
                ("benchmark", "accuracy") if benchmark_first else ("accuracy", "benchmark")
            )
            assert positions[1] > positions[0]
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await executor.close()


async def test_scope_close_releases_a_completed_sequence_member(tmp_path: Path) -> None:
    """The durable scope manifest cleans up a lease while its flow is between measure calls."""
    evaluation = _OneGpu()
    admission = EvaluationAdmission()
    root = FakeWorkspace(path=Path("/workspace"))
    commit = "a" * 40
    root.add_retained_revision(commit)
    workspaces = FakeWorkspaces(root, supports_parallel_candidates=True)
    executor = PollingEvaluationExecutor(
        evaluation, workspaces, clock=ManualClock(), admission=admission
    )
    project = Project.open(tmp_path)
    store = ReceiptStore(project.state.state_store_namespace("run"), threads=SimThreads())
    requests = MeasurementRequests(executor, store)
    submitted = submission("sequence-member", candidate=commit)
    submitted = submitted.model_copy(
        update={
            "plan": submitted.plan.model_copy(
                update={"evaluation_lease": EvaluationLease(key="sequence", retain=True)}
            )
        }
    )
    try:
        result = await requests.execute(submitted, context_for(submitted))
        resource = result.observation.observation.resource_id
        assert resource is not None
        await _finished(executor, resource.root)
        assert await admission.counts() == (1, 0)
        close = close_request()
        closed = await requests.execute(close, context_for(close))
        assert closed.observation.observation.released
        assert await admission.counts() == (0, 0)
    finally:
        await executor.close()


async def test_a_profile_job_shares_admission_and_releases_on_cancellation() -> None:
    evaluation = _OneGpu()
    held = evaluation.gate("benchmark", 0)
    admission = EvaluationAdmission()
    root = FakeWorkspace(path=Path("/workspace"))
    workspaces = FakeWorkspaces(root, supports_parallel_candidates=True)
    snapshot = await root.snapshot("candidate")
    executor = PollingEvaluationExecutor(
        evaluation, workspaces, clock=ManualClock(), admission=admission
    )

    async def profile() -> None:
        async with admission.lease("profile"):
            await evaluation.benchmark(root, output=DISCARD)

    capture = asyncio.create_task(profile())
    try:
        await yield_until(lambda: _entered(held), bool, "profile holds the GPU")
        await executor.submit(_request(snapshot, "official"), handle_id="official")
        await yield_until(admission.counts, lambda counts: counts == (1, 1), "official waits")
        assert evaluation.accuracy_calls == []
        capture.cancel()
        await asyncio.gather(capture, return_exceptions=True)
        await _finished(executor, "official")
        assert evaluation.overlaps == 0
        assert await admission.counts() == (0, 0)
    finally:
        capture.cancel()
        await asyncio.gather(capture, return_exceptions=True)
        await executor.close()


@example(count=17)
@given(count=st.integers(min_value=1, max_value=8))
async def test_restart_reacquires_every_submitted_jobs_durable_admission_lease(count: int) -> None:
    namespace = InMemoryEvaluationNamespace()
    retries = EvaluationRetries(
        infrastructure_attempts=1,
        ambiguous_attempts=1,
        environment_backoff=30.0,
        environment_backoff_cap=300.0,
    )
    workspaces = FakeWorkspaces(
        FakeWorkspace(path=Path("/workspace")), supports_parallel_candidates=True
    )
    snapshot = await workspaces.root.snapshot("candidate")
    before = _OneGpu()
    held = before.gate("accuracy", 0)
    old_slots = EvaluationAdmission()
    first = RetryingEvaluationExecutor(
        PollingEvaluationExecutor(before, workspaces, clock=ManualClock(), admission=old_slots),
        retries,
        namespace,
        clock=ManualClock(),
    )
    try:
        for index in range(count):
            request = _request(snapshot, f"job-{index}").model_copy(
                update={
                    "owner_scope": f"attempt-{index}",
                    "admission": {"key": f"durable-lease-{index}", "retain": False},
                }
            )
            await first.submit(request, handle_id=f"job-{index}")
        await yield_until(lambda: _entered(held), bool, "first evaluation holds its slot")
        await yield_until(
            old_slots.counts, lambda counts: counts == (1, count - 1), "all other evaluations queue"
        )
        assert len(before.accuracy_calls) == 1
    finally:
        await first.close()
    assert await old_slots.counts() == (0, 0)

    after = _OneGpu(interleave=True)
    recovered = after.gate("accuracy", 0)
    slots = EvaluationAdmission()
    resumed = RetryingEvaluationExecutor(
        PollingEvaluationExecutor(after, workspaces, clock=ManualClock(), admission=slots),
        retries,
        namespace,
        clock=ManualClock(),
    )
    try:
        for index in range(count):
            observed = await resumed.poll(f"job-{index}")
            assert observed.phase is PollPhase.QUEUED
        await yield_until(lambda: _entered(recovered), bool, "recovered job reacquires a slot")
        await yield_until(
            slots.counts,
            lambda counts: counts == (1, count - 1),
            "recovered plans queue behind the same capacity",
        )
        recovered.release()
        for index in range(count):
            result = await yield_until(
                lambda handle=f"job-{index}": resumed.poll(handle),
                lambda seen: seen.phase is PollPhase.ENDED,
                "every recovered evaluation completes",
                turns=1000,
            )
            assert result.terminal is not None
            assert result.terminal.state is EvaluationState.SUCCEEDED
        assert after.overlaps == 0
        assert after.peak == 1
        assert len(after.accuracy_calls) == count
        assert len(after.benchmark_calls) == count
        assert await slots.counts() == (0, 0)
    finally:
        recovered.release()
        await resumed.close()
