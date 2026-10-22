"""In-process semantic evaluation executor for runs without a Slurm cluster.

It is the sibling of ``SemanticSlurmEvaluationExecutor``: both implement the
``PollingEvaluationExecutor`` protocol plus ``close``, so the evaluation service and
core bindings accept either. This one runs stages as tasks of the current
process and reports state through ``inspect``/``wait_for_change``.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import nullcontext
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import JsonValue, ValidationError

from vs_agent.api import AgentExecutionCancelledError
from vs_core.api import EvaluationLease, EvaluationTiming
from vs_evaluation.api import (
    MAX_EVIDENCE_SUMMARY_CHARS,
    AvailabilitySnapshot,
    AvailabilityState,
    CostClass,
    EvaluationRequest,
    EvaluationState,
    EvaluationStepResult,
    EvidenceKind,
    EvidenceMetric,
    EvidenceOutcome,
    EvidenceResultIdentity,
    ExecutorObservation,
    ExecutorPoll,
    ExecutorRejectedError,
    HostSampleRecord,
    HostSnapshotRecord,
    LifecyclePublisher,
    PollPhase,
    ResourceRequirements,
    ReuseStatus,
    SemanticEvaluationStage,
    StageFailureKind,
    StageState,
    TrustedEvidence,
    evidence_identity,
    is_finished,
)
from vs_redaction.api import redact_diagnostic_tail
from vs_runtime._evaluation_admission import EvaluationAdmission
from vs_runtime._evaluation_failure_text import render_evaluation_failure, render_stage_failure
from vs_runtime._evaluation_jobs import headline_first
from vs_runtime._failure_classification import is_unsettled
from vs_runtime._measurement_progress import StageRun
from vs_sim.api import DISCARD, HostSample

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

    from vs_runtime._measurement_progress import StageOutputOpener
    from vs_runtime._profile_evaluation import CommandProfileCapture
    from vs_runtime._run_control import RunControlChannel
    from vs_runtime.contracts import CandidateWorkspace, Evaluation, Workspace, Workspaces
    from vs_sim.api import Clock, HostProbe, OutputSink


_GATING_KINDS = frozenset({EvidenceKind.BUILD, EvidenceKind.ACCURACY})
"""Stages whose failed outcome skips the stages after them: nothing later can count."""

_RESOURCE_KINDS = frozenset({EvidenceKind.ACCURACY, EvidenceKind.BENCHMARK, EvidenceKind.PROFILE})
"""Trusted stages that use the evaluation resource and therefore take a slot."""


@dataclass(frozen=True, slots=True)
class _SemanticStageObservation:
    evidence: TrustedEvidence
    completed: bool
    operator_summary: str | None


class PollingEvaluationExecutor:
    """Run semantic evaluation requests in-process, publishing observations a caller polls.

    Each submitted request evaluates its candidate snapshot in a fresh candidate
    workspace through the trusted ``Evaluation`` effects, one stage at a time.
    Resource gates share the admission pool: a gate stays queued until a lease
    is free. A caller-chosen sequence retains that lease between jobs until its
    final member or scope cancellation. Observations are process-local.
    """

    def __init__(  # noqa: PLR0913  # lint-waiver: LW-241001 [PLR0913]; host_probe and admission are construction facts of this executor. A configure() after init can run once jobs exist and split admission from the executor that owns it, a carrier would only forward those resources, and making the probe required would force every test that does not sample the host to pass one.
        # > Dropping admission would let accuracy and benchmark start before a slot is free.
        self,
        evaluation: Evaluation,
        workspaces: Workspaces,
        *,
        clock: Clock,
        stage_output: StageOutputOpener | None = None,
        control: RunControlChannel | None = None,
        host_probe: HostProbe | None = None,
        admission: EvaluationAdmission | None = None,
        profile_capture: CommandProfileCapture | None = None,
    ) -> None:
        """Evaluate through ``evaluation`` in candidate workspaces from ``workspaces``.

        ``clock`` stamps availability observations. ``stage_output`` opens the stream
        each stage's command writes its raw output to while it runs; ``None`` discards it.
        ``host_probe`` reads host contention around a benchmark; ``None`` records none.
        ``admission`` shares slots with other evaluation-resource jobs; by default
        this executor owns one slot.
        """
        self._profile_capture = profile_capture
        self._control = control
        self._clock = clock
        self._stage_output = stage_output
        self._host_probe = host_probe
        self._evaluation = evaluation
        self._workspaces = workspaces
        self._admission = EvaluationAdmission() if admission is None else admission
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._lifecycle = LifecyclePublisher()
        self._lease_keys: dict[str, str] = {}
        self._retain: set[str] = set()
        self._reserved: set[str] = set()
        self._members: dict[str, str] = {}
        self._closed: set[str] = set()
        self._queued_at: dict[str, float] = {}
        self._admitted_at: dict[str, float] = {}

    async def availability(self, requirements: ResourceRequirements) -> AvailabilitySnapshot:
        del requirements
        active, queued = await self._admission.counts()
        busy = active > 0 or queued > 0
        return AvailabilitySnapshot(
            state=AvailabilityState.BUSY if busy else AvailabilityState.IMMEDIATE,
            capacity=self._admission.capacity,
            in_flight=active,
            queue_depth=queued,
            reuse_status=ReuseStatus.UNKNOWN,
            cost_class=CostClass.UNKNOWN,
            observed_at=self._clock.now(),
            fresh_for_s=1.0,
            supported_profile_fields=(
                self._profile_capture.plan.profile_fields
                if self._profile_capture is not None
                else ()
            ),
            supported_evidence_kinds=(
                EvidenceKind.BUILD.value,
                EvidenceKind.ACCURACY.value,
                EvidenceKind.BENCHMARK.value,
                *((EvidenceKind.PROFILE.value,) if self._profile_capture is not None else ()),
            ),
        )

    async def submit(self, request: EvaluationRequest, *, handle_id: str) -> None:
        if handle_id in self._tasks or self._lifecycle.observation(handle_id) is not None:
            return
        for step in request.stages:
            stage = SemanticEvaluationStage.model_validate(step.payload)
            if stage.kind is EvidenceKind.PROFILE:
                if self._profile_capture is None:
                    message = "the task declares no host profile capture"
                    raise ExecutorRejectedError(message)
                self._profile_capture.require(stage.required_profile_fields)
        self._register_lease(handle_id, request)
        self._queued_at[handle_id] = self._clock.now()
        self._publish(handle_id, ExecutorObservation(state=EvaluationState.QUEUED))
        self._tasks[handle_id] = asyncio.create_task(self._run(handle_id, request))

    async def inspect_only(self, handle_id: str) -> ExecutorObservation | None:
        """Read process-local evidence without starting recovery tasks."""
        return self._lifecycle.observation(handle_id)

    async def poll(self, handle_id: str) -> ExecutorPoll:
        """Inspect once from process-local state; never submits, cancels or creates a workspace."""
        observed = self._lifecycle.observation(handle_id)
        if observed is None:
            return ExecutorPoll(phase=PollPhase.UNSUBMITTED)
        if is_finished(observed.state):
            return ExecutorPoll(
                phase=PollPhase.ENDED, terminal=observed, admission_timing=self._timing(handle_id)
            )
        phase = PollPhase.QUEUED if observed.state is EvaluationState.QUEUED else PollPhase.RUNNING
        return ExecutorPoll(
            phase=phase,
            current_stage=observed.current_stage,
            admission_timing=self._timing(handle_id),
        )

    async def inspect(self, handle_id: str) -> ExecutorObservation | None:
        return self._lifecycle.observation(handle_id)

    async def wait_for_change(self, handle_id: str, timeout_s: float) -> None:
        await self._lifecycle.wait_for_change(handle_id, timeout_s)

    async def cancel(self, handle_id: str) -> None:
        # A completed member may still own its sequence's lease. Cancel its
        # current member first, so releasing an earlier member cannot admit a
        # competing gate while the current command is still running.
        key = self._lease_keys.get(handle_id)
        if key is not None:
            self._closed.add(key)
        member = self._members.get(key) if key is not None else None
        handles = tuple(dict.fromkeys((handle_id, *((member,) if member is not None else ()))))
        tasks = tuple(self._tasks[h] for h in handles if h in self._tasks)
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if key is not None:
            await self._release_lease(key)
        for handle in handles:
            observed = self._lifecycle.observation(handle)
            self._publish(
                handle,
                ExecutorObservation(
                    state=EvaluationState.CANCELED,
                    stage_results=observed.stage_results if observed is not None else (),
                ),
            )

    async def close(self) -> None:
        # Through cancel(), not task.cancel(): a task cancelled before its first step
        # never runs its own handler, so only cancel() ends it in a published state.
        active = {
            self._lease_keys[handle_id]: handle_id
            for handle_id, task in self._tasks.items()
            if not task.done() or self._lease_keys[handle_id] in self._reserved
        }
        # One cancellation per sequence: cancelling every completed member in
        # parallel would repeatedly interrupt the current member's cleanup.
        await asyncio.gather(*(self.cancel(handle_id) for handle_id in active.values()))

    async def _run(self, handle_id: str, request: EvaluationRequest) -> None:
        first = SemanticEvaluationStage.model_validate(request.stages[0].payload)
        workspace: CandidateWorkspace | None = None
        results: list[EvaluationStepResult] = []
        failure: str | None = None
        operator_failure: str | None = None
        holding = False
        retain = handle_id in self._retain
        try:
            holding = await self._admit(handle_id, request)
            self._admitted_at[handle_id] = self._clock.now()
            workspace = await self._workspaces.create_measured_candidate(first.snapshot)
            opened = False
            for step in request.stages:
                stage = SemanticEvaluationStage.model_validate(step.payload)
                if not opened:
                    # The drain learns the job is executing once a stage is admitted,
                    # not while it is still waiting for a slot.
                    if self._control is not None:
                        self._control.start_execution(lambda: None, evaluation_id=request.key)
                    opened = True
                self._publish(
                    handle_id,
                    ExecutorObservation(
                        state=EvaluationState.RUNNING,
                        current_stage=step.name,
                        stage_results=tuple(results),
                    ),
                )
                with self._open_output(
                    StageRun(handle_id, step.name, purpose=stage.purpose)
                ) as output:
                    observed = await self._evaluate(workspace, stage, handle_id, output)
                evidence = observed.evidence
                results.append(
                    EvaluationStepResult(
                        name=step.name,
                        state=StageState.SUCCEEDED if observed.completed else StageState.FAILED,
                        result=evidence.model_dump(mode="json"),
                        operator_summary=observed.operator_summary,
                        failure_kind=None if observed.completed else StageFailureKind.EXECUTION,
                        failure=(
                            None
                            if observed.completed
                            else render_stage_failure(
                                ((evidence.semantic_summary, evidence.kind),), None
                            )
                        ),
                        operator_failure=(
                            None
                            if observed.completed
                            else render_stage_failure(
                                ((observed.operator_summary, evidence.kind),), None
                            )
                        ),
                    )
                )
                if not observed.completed or (
                    evidence.kind in _GATING_KINDS and evidence.outcome is EvidenceOutcome.FAILED
                ):
                    skipped = request.stages[len(results) :]
                    results.extend(
                        EvaluationStepResult(name=remaining.name, state=StageState.SKIPPED)
                        for remaining in skipped
                    )
                    if not observed.completed or skipped:
                        # Infrastructure failure or skipped stages prevent completion.
                        # Preserve diagnostics for the submitting agent.
                        failure = render_stage_failure(
                            ((evidence.semantic_summary, evidence.kind),), None
                        )
                        operator_failure = render_stage_failure(
                            ((observed.operator_summary, evidence.kind),), None
                        )
                    break
            self._publish(
                handle_id,
                ExecutorObservation(
                    state=EvaluationState.SUCCEEDED if failure is None else EvaluationState.FAILED,
                    stage_results=tuple(results),
                    failure=failure,
                    operator_failure=operator_failure,
                ),
            )
        except (asyncio.CancelledError, AgentExecutionCancelledError) as cancelled:
            retain = False
            self._publish(
                handle_id,
                ExecutorObservation(
                    state=EvaluationState.CANCELED,
                    stage_results=tuple(results),
                ),
            )
            raise asyncio.CancelledError from cancelled
        except Exception as error:  # noqa: BLE001  # lint-waiver: LW-930049 [BLE001]; this lifecycle boundary converts arbitrary extension failures into durable diagnostics; narrower catches would let unknown providers bypass the contract.
            raw_failure = render_evaluation_failure(str(error) or None, type(error).__name__)
            self._publish(
                handle_id,
                ExecutorObservation(
                    state=EvaluationState.FAILED,
                    stage_results=tuple(results),
                    # Extensions may raise an exception without a message.
                    # Its type is still a failure fact; the existing template
                    # guarantees the terminal diagnostic stays nonempty.
                    failure=raw_failure,
                    operator_failure=redact_diagnostic_tail(
                        raw_failure,
                        max_chars=MAX_EVIDENCE_SUMMARY_CHARS,
                    ),
                ),
            )
        finally:
            key = self._lease_keys[handle_id]
            if holding and not retain:
                await self._release_lease(key)
            self._members.pop(key, None)
            if workspace is not None:
                await workspace.discard()

    def _timing(self, handle_id: str) -> JsonValue:
        queued = self._queued_at.get(handle_id)
        if queued is None:
            return None
        return EvaluationTiming(
            queued_at=queued, admitted_at=self._admitted_at.get(handle_id)
        ).model_dump(mode="json")

    def _register_lease(self, handle_id: str, request: EvaluationRequest) -> None:
        try:
            lease = (
                None
                if request.admission is None
                else EvaluationLease.model_validate(request.admission)
            )
        except ValidationError as error:
            message = f"evaluation admission: {error}"
            raise ExecutorRejectedError(message) from error
        key = (
            f"job:{handle_id}"
            if lease is None
            else "sequence:"
            + json.dumps((request.owner_scope, request.owner_generation, lease.key))
        )
        if key in self._closed or key in self._members:
            message = f"evaluation lease {key!r} is closed or already executing a member"
            raise ExecutorRejectedError(message)
        self._lease_keys[handle_id] = key
        self._members[key] = handle_id
        if lease is not None and lease.retain:
            self._retain.add(handle_id)

    async def _release_lease(self, key: str) -> None:
        self._reserved.discard(key)
        await self._admission.release(key)

    async def _admit(self, handle_id: str, request: EvaluationRequest) -> bool:
        # Admission covers the whole job. A build must not publish RUNNING
        # before a following GPU gate acquires its resource: core would then
        # charge that gate's queue wait against the measurement limit.
        uses_resource = request.admission is not None or any(
            SemanticEvaluationStage.model_validate(step.payload).kind in _RESOURCE_KINDS
            for step in request.stages
        )
        if uses_resource:
            key = self._lease_keys[handle_id]
            if key not in self._reserved:
                await self._admission.acquire(key)
                self._reserved.add(key)
        return uses_resource

    def _open_output(self, run: StageRun) -> AbstractContextManager[OutputSink]:
        if self._stage_output is None:
            return nullcontext(DISCARD)
        return self._stage_output(run)

    async def _evaluate(
        self,
        workspace: Workspace,
        stage: SemanticEvaluationStage,
        handle_id: str,
        output: OutputSink,
    ) -> _SemanticStageObservation:
        if stage.kind is EvidenceKind.PROFILE:
            return await self._profile(workspace, stage, handle_id, output)
        unmet: str | None = None
        host_snapshot: HostSnapshotRecord | None = None
        if stage.kind is EvidenceKind.BUILD:
            built = await self._evaluation.build(workspace, output=output)
            completed = not is_unsettled(built.failure_kind)
            outcome = (
                EvidenceOutcome.PASSED if completed and built.passed else EvidenceOutcome.FAILED
            )
            summary = built.feedback or ("reused this commit's build" if built.reused else None)
            operator_summary = built.operator_feedback or summary
            metrics: tuple[EvidenceMetric, ...] = ()
            partial = None
            kind = built.failure_kind
        elif stage.kind is EvidenceKind.ACCURACY:
            result = await self._evaluation.accuracy(workspace, output=output)
            completed = not is_unsettled(result.failure_kind)
            outcome = (
                EvidenceOutcome.PASSED if completed and result.passed else EvidenceOutcome.FAILED
            )
            summary = result.feedback
            operator_summary = result.operator_feedback
            metrics = ()
            partial = None
            kind = result.failure_kind
        elif stage.kind is EvidenceKind.BENCHMARK:
            start = _sample_host(self._host_probe)
            try:
                result = await self._evaluation.benchmark(workspace, output=output)
            finally:
                host_snapshot = _host_snapshot(start, _sample_host(self._host_probe))
            completed = not is_unsettled(result.failure_kind)
            kind = result.failure_kind
            outcome = (
                EvidenceOutcome.PASSED if completed and result.passed else EvidenceOutcome.FAILED
            )
            summary = result.feedback
            operator_summary = result.operator_feedback
            partial = result.partial_measurement
            unmet = result.unmet_requirement
            row = result.row or {}
            metrics = tuple(
                EvidenceMetric(
                    name=name,
                    value=row[name],
                    direction=(
                        result.metric_direction.value
                        if name == result.metric_name and result.metric_direction is not None
                        else None
                    ),
                    unit=result.metric_unit if name == result.metric_name else None,
                )
                for name in headline_first(row, result.metric_name)
            )
        else:
            message = "the task declares no host profile capture"
            raise ExecutorRejectedError(message)
        if summary is not None:
            summary = summary[-MAX_EVIDENCE_SUMMARY_CHARS:]
        if operator_summary is not None:
            operator_summary = operator_summary[-MAX_EVIDENCE_SUMMARY_CHARS:]
        evidence_id = evidence_identity(
            stage,
            EvidenceResultIdentity(
                evaluation_id=handle_id,
                outcome=outcome,
                summary=summary,
                metrics=metrics,
                partial=partial,
                unmet_requirement=unmet,
            ),
        )
        evidence = TrustedEvidence(
            evidence_id=evidence_id,
            evaluation_id=handle_id,
            stage_name=stage.kind.value,
            kind=stage.kind,
            fingerprints=stage.fingerprints,
            trusted_inputs=stage.fingerprints.candidate,
            outcome=outcome,
            semantic_summary=summary,
            metrics=metrics,
            partial_measurement=partial,
            failure_kind=None if outcome is EvidenceOutcome.PASSED else kind,
            unmet_requirement=None if outcome is EvidenceOutcome.PASSED else unmet,
            accepted_round=0,
            host_snapshot=host_snapshot,
        )
        return _SemanticStageObservation(
            evidence=evidence,
            completed=completed,
            operator_summary=operator_summary,
        )

    async def _profile(
        self,
        workspace: Workspace,
        stage: SemanticEvaluationStage,
        handle_id: str,
        output: OutputSink,
    ) -> _SemanticStageObservation:
        if self._profile_capture is None:
            message = "the task declares no host profile capture"
            raise ExecutorRejectedError(message)
        profile = await self._profile_capture.capture(workspace, output=output)
        outcome = EvidenceOutcome.PASSED if profile.failure is None else EvidenceOutcome.FAILED
        summary = profile.failure[-MAX_EVIDENCE_SUMMARY_CHARS:] if profile.failure else None
        identity = EvidenceResultIdentity(
            evaluation_id=handle_id,
            outcome=outcome,
            summary=summary,
            metrics=profile.metrics,
            partial=None,
        )
        return _SemanticStageObservation(
            evidence=TrustedEvidence(
                evidence_id=evidence_identity(stage, identity),
                evaluation_id=handle_id,
                stage_name=stage.kind.value,
                kind=stage.kind,
                fingerprints=stage.fingerprints,
                trusted_inputs=stage.fingerprints.candidate,
                outcome=outcome,
                semantic_summary=summary,
                metrics=profile.metrics,
                failure_kind=profile.failure_kind,
                accepted_round=0,
            ),
            completed=profile.completed,
            operator_summary=summary,
        )

    def _publish(self, handle_id: str, observation: ExecutorObservation) -> None:
        self._lifecycle.publish(handle_id, observation)


def _sample_host(probe: HostProbe | None) -> HostSample | None:
    """One host reading, or None when this run has no probe.

    A probe that fails returns an all-unavailable sample. The measurement still counts.
    """
    if probe is None:
        return None
    try:
        return probe.sample()
    except Exception:  # noqa: BLE001  # lint-waiver: LW-241002 [BLE001]; a probe's failures are open and must not fail the measurement.
        # > Catching only OSError misses a probe that raises ValueError or RuntimeError, and the benchmark would fail.
        # > Letting the exception propagate treats an unread host as a failed candidate.
        return HostSample()


def _host_snapshot(start: HostSample | None, end: HostSample | None) -> HostSnapshotRecord | None:
    """The start and end readings of one benchmark, or None when the run did not sample."""
    if start is None:
        return None
    return HostSnapshotRecord(
        start=_sample_record(start), end=_sample_record(HostSample() if end is None else end)
    )


def _sample_record(sample: HostSample) -> HostSampleRecord:
    return HostSampleRecord(
        load_1=sample.load_1,
        load_5=sample.load_5,
        load_15=sample.load_15,
        cpu_count=sample.cpu_count,
        memory_free_bytes=sample.memory_free_bytes,
        memory_pressure=sample.memory_pressure,
        gpu_processes=sample.gpu_processes,
    )
