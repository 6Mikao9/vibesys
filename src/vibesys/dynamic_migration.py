"""Explicit objective-policy migration; immutable receipts remain the source of evidence.

The pure transform adds versioned per-axis declarations and rebuilds the verified
parent catalog from already-recorded measurements. It never settles an in-flight
attempt, removes queued work, edits a receipt, or advances main.
"""

from __future__ import annotations

from math import sqrt
from typing import TYPE_CHECKING

from vibesys.metrics import AcceptedReading, EvidenceReading, accept_readings, reading_of
from vibesys.orchestration.dynamic.search.api import DynamicResume, ParentSnapshot, ingest
from vs_core.api import CheckpointKnown, EvidenceKind, FlowState, Retained, TaskTerms
from vs_core.api.values import NoiseSample, ObjectiveNoise
from vs_runtime.api.core import EvidenceLookup, RuntimeRecord, reading_mapping

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["migrate_objective_policy", "recorded_objective_readings"]
_MIN_BASELINE_SAMPLES = 2


def recorded_objective_readings(
    record: RuntimeRecord[FlowState], ledger: EvidenceLookup
) -> tuple[AcceptedReading, ...]:
    """Read and bind immutable receipt evidence before the pure migration runs."""
    found = []
    for ref in record.envelope.core.evaluation.evidence:
        entry = ledger.lookup(ref.key)
        if entry is None or entry.purpose != ref.purpose:
            message = f"missing or mismatched evidence receipt: {ref.key}"
            raise ValueError(message)
        reading = EvidenceReading.model_validate(reading_mapping(ref, entry.evidence))
        accepted = accept_readings((reading,), (ref,))
        if isinstance(accepted, str):
            raise TypeError(accepted)
        found.extend(accepted)
    return tuple(found)


def _noise(values: tuple[float, ...], floor: float) -> float:
    if len(values) < _MIN_BASELINE_SAMPLES:
        return floor
    mean = sum(values) / len(values)
    if mean == 0:
        return floor
    spread = sqrt(sum((value - mean) ** 2 for value in values) / (len(values) - 1))
    return max(floor, spread / abs(mean))


def _terms(
    terms: TaskTerms,
    baseline: tuple[AcceptedReading, ...],
    *,
    floor: float,
    calibration: Mapping[str, tuple[float, ...]],
    provenance: str,
) -> TaskTerms:
    unknown = set(calibration) - {axis.name for axis in terms.objectives}
    if unknown:
        message = f"unknown calibration objective: {sorted(unknown)}"
        raise ValueError(message)
    axes = []
    for axis in terms.objectives:
        samples = tuple(
            metric.value
            for reading in baseline
            if reading.kind is EvidenceKind.BENCHMARK and reading.passed
            for metric in reading.metrics
            if metric.name == axis.name
        )
        recorded = len(samples) >= _MIN_BASELINE_SAMPLES
        values = samples if recorded else calibration.get(axis.name, samples)
        source = (
            "recorded baseline receipts"
            if recorded or axis.name not in calibration
            else "operator calibration"
        )
        policy = ObjectiveNoise(
            revision=1 if axis.noise is None else axis.noise.revision + 1,
            floor=floor,
            relative_noise=_noise(values, floor),
            provenance=f"{provenance}; source: {source}; estimator: sample standard deviation / absolute mean",
            baseline_values=tuple(NoiseSample(value=value) for value in values),
        )
        axes.append(axis.model_copy(update={"noise": policy}))
    return terms.model_copy(update={"objectives": tuple(axes)})


def _archive(
    record: RuntimeRecord[FlowState], readings: tuple[AcceptedReading, ...], snapshot: DynamicResume
) -> DynamicResume:
    core = record.envelope.core
    refs = {ref.key: ref for ref in core.evaluation.evidence}
    # Only checkpointed revisions may become buildable. An arbitrary evaluator
    # claim is not proof that core retained a tree.
    retained = {}
    for step in record.envelope.strategy.journal:
        event = step.event
        if isinstance(event, CheckpointKnown) and isinstance(event.checkpoint, Retained):
            retained[event.checkpoint.revision] = event.invocation
    parents = snapshot.record.parents
    starts = snapshot.record.running
    for candidate in retained:
        own = tuple(reading for reading in readings if refs[reading.key].candidate == candidate)
        accuracy = reading_of(own, EvidenceKind.CORRECTNESS)
        benchmark = reading_of(own, EvidenceKind.BENCHMARK)
        if accuracy is None or not accuracy.passed:
            continue
        proof = tuple(refs[reading.key] for reading in own)
        owner = refs[accuracy.key].scope.owner.root
        start = next((item for item in starts if item.attempt.root == owner), None)
        if start is None:
            # Finished rounds already have their exact hypothesis association.
            known = next(
                (
                    (key, row.sequence)
                    for key, hypothesis in snapshot.record.hypotheses
                    for row in hypothesis.rounds
                    if row.candidate == candidate
                ),
                None,
            )
            if known is None:
                continue
            hypothesis_id, sequence = known
        else:
            hypothesis_id, sequence = start.plan.work_id, start.sequence
        parent = ParentSnapshot(
            hypothesis_id=hypothesis_id,
            revision=candidate,
            generation=refs[accuracy.key].scope.generation,
            accuracy=accuracy,
            benchmark=benchmark,
            proof=proof,
            submission_index=sequence,
        )
        parents = ingest(parents, parent)
    return snapshot.model_copy(
        update={"record": snapshot.record.model_copy(update={"parents": parents})}
    )


def migrate_objective_policy(
    record: RuntimeRecord[FlowState],
    readings: tuple[AcceptedReading, ...],
    *,
    floor: float,
    calibration: Mapping[str, tuple[float, ...]],
    provenance: str,
) -> RuntimeRecord[FlowState]:
    """Add a versioned declaration and reproject trusted checkpoints, without I/O.

    ``floor`` and its provenance are mandatory operator inputs. Repeated
    baseline receipts take precedence over supplied calibration observations.
    Original journal, queue, core evidence and decision receipts remain intact.
    Call resume preflight on the committed copy before using it.
    """
    if not provenance.strip():
        message = "objective noise provenance must name its calibration source"
        raise ValueError(message)
    envelope = record.envelope
    task = envelope.core.run.facts.task
    if task is None or not task.objectives:
        message = "objective migration requires recorded task objectives"
        raise ValueError(message)
    base = envelope.core.run.facts.baseline
    refs = {ref.key: ref for ref in envelope.core.evaluation.evidence}
    baseline = tuple(reading for reading in readings if refs[reading.key].candidate == base)
    terms = _terms(task, baseline, floor=floor, calibration=calibration, provenance=provenance)
    facts = envelope.core.run.facts.model_copy(update={"task": terms})
    flows = []
    for original in envelope.strategy.live.flows:
        flow = original
        if flow.path == "":
            snapshot = DynamicResume.model_validate_json(flow.snapshot_json)
            snapshot = _archive(record, readings, snapshot)
            flow = flow.model_copy(update={"snapshot_json": snapshot.model_dump_json()})
        flows.append(flow)
    live = envelope.strategy.live.model_copy(update={"flows": tuple(flows)})
    strategy = envelope.strategy.model_copy(update={"facts": facts, "live": live})
    run = envelope.core.run.model_copy(update={"facts": facts})
    core = envelope.core.model_copy(update={"run": run})
    return record.model_copy(
        update={"envelope": envelope.model_copy(update={"core": core, "strategy": strategy})}
    )
