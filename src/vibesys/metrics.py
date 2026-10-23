"""Objective axes and metric comparison owned by orchestration policy.

A run measures its candidates in a **metric space**: a set of directed
objective axes plus the relative benchmark tolerance below which two readings
are indistinguishable. :class:`MetricSpace` owns every question that needs both
of those facts -- "is this reading better?", "does this row dominate that
one?", "which rows are on the frontier?", "which reading is best?" -- so that
noise and direction appear as parameters here and nowhere else. Callers hand it
measurements and consume a :class:`MetricComparison`; they do not assemble a
comparison out of a value, a direction, and a tolerance.

The space is built once from the task's ``objectives.toml`` and persisted with
the run, so the loop, ``--resume`` reprojection, and the server read path all
compare within the same space instead of being handed a tolerance through a
call chain (issue #507).

``MetricComparison`` is persisted by hypothesis history and owned here with
the comparison policy that produces it.
"""

from __future__ import annotations

from dataclasses import dataclass as standard_dataclass
from enum import StrEnum
from statistics import fmean
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator
from pydantic.dataclasses import dataclass

from vs_core.api import (
    EvidenceId,
    EvidenceKey,
    EvidenceKind,
    EvidenceRef,
    ObservationStatus,
    RequestId,
    RunView,
    Scope,
    Value,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence


__all__ = [
    "AcceptedReading",
    "Direction",
    "EvidenceReading",
    "FrameworkBenchmarkOutcome",
    "HostContention",
    "HostSampleView",
    "Measurement",
    "MetricComparison",
    "MetricRow",
    "MetricSpace",
    "Objective",
    "PartialRow",
    "accept_readings",
    "judged_evidence",
    "mean_metrics",
    "pooled_benchmarks",
    "reading_of",
    "with_declared_directions",
]


class MetricComparison(StrEnum):
    """How one measurement relates to the measurement it is compared against.

    ``WITHIN_NOISE`` and ``INCOMPARABLE`` are deliberately distinct.
    ``WITHIN_NOISE`` means both readings exist on the same axis and their
    difference does not exceed the declared measurement tolerance, so the
    comparison ran and found no result. ``INCOMPARABLE`` means the comparison
    could not run at all: a reading is missing, the axis has no known
    direction, or the runs give no estimate of the noise. Consumers that collapse them still have to say so explicitly.
    """

    BETTER = "better"
    WORSE = "worse"
    WITHIN_NOISE = "within_noise"
    INCOMPARABLE = "incomparable"


@standard_dataclass(frozen=True, slots=True)
class FrameworkBenchmarkOutcome:
    """Policy-interpreted benchmark headline and full measured row."""

    feedback: str | None = None
    metric_name: str | None = None
    metric_value: float | None = None
    metric_direction: Literal["max", "min"] | None = None
    metric_unit: str | None = None
    row: Mapping[str, float] | None = None


@dataclass(frozen=True, config=ConfigDict(extra="forbid"))
class Objective:
    """One directed axis of the fitness frontier.

    ``name`` is the key in a metric row the profiler must report.
    ``direction`` is ``"max"`` or ``"min"``; the framework flips the sign when
    comparing min-objectives so dominance logic can treat every axis as
    "higher is better" internally.
    """

    name: str
    direction: Literal["max", "min"]
    relative_noise: Annotated[FiniteFloat | None, Field(ge=0.0, lt=1.0)] = Field(
        default=None, exclude_if=lambda value: value is None
    )

    def signed(self, value: float) -> float:
        """Return *value* flipped to "higher is better" semantics.

        Used by the dominance helper so callers don't have to branch on
        direction. Min objectives are negated; max objectives pass through.
        """
        return value if self.direction == "max" else -value


@dataclass(frozen=True, config=ConfigDict(extra="forbid"))
class Measurement:
    """One benchmark reading, named by the axis it was taken on.

    ``direction`` is data about the reading, not a comparison parameter: a
    record written before its axis was declared in ``objectives.toml`` carries
    the direction the framework used at the time. When it is absent the space's
    configured axis supplies it.
    """

    metric: str
    value: float
    direction: Literal["max", "min"] | None = None


class MetricSpace(BaseModel):
    """The directed axes and measurement tolerance one run compares within.

    ``relative_noise`` is the run's declared benchmark variation, read from
    ``[pareto] relative_noise``. A reading is better than a baseline only when
    its direction-signed improvement exceeds ``abs(baseline) * relative_noise``;
    symmetrically for worse. A zero fraction therefore reproduces strict
    comparison, and so does a zero baseline, for which a relative tolerance has
    no scale.

    The default is the empty strict space. It is the compatibility value for
    run state written before the space was persisted, and the only sanctioned
    way to get a zero tolerance: no function anywhere takes a tolerance
    argument that could silently default to it.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    objectives: tuple[Objective, ...] = ()
    relative_noise: Annotated[FiniteFloat, Field(ge=0.0, lt=1.0)] = 0.0

    @model_validator(mode="after")
    def _unique_axes(self) -> MetricSpace:
        names = [objective.name for objective in self.objectives]
        if len(set(names)) != len(names):
            message = "objective names must be unique"
            raise ValueError(message)
        return self

    # -- axes ---------------------------------------------------------------

    @property
    def primary(self) -> Objective | None:
        """Return the axis that names the run's headline metric, if any."""
        return self.objectives[0] if self.objectives else None

    def axis(self, metric: str | None) -> Objective | None:
        """Return the configured axis named *metric*, if the space has one."""
        if metric is None:
            return None
        return next((item for item in self.objectives if item.name == metric), None)

    def signed_primary(self, value: float) -> float:
        """Orient a headline value so larger ranks better in this space.

        The empty compatibility space keeps the original scalar maximization
        semantics. A configured space applies its primary axis direction;
        callers use this only for values representing that headline axis.
        """
        primary = self.primary
        return value if primary is None else primary.signed(value)

    def direction(self, measurement: Measurement | None) -> Literal["max", "min"] | None:
        """Resolve which way is better for *measurement*.

        The reading's own direction wins, so a record measured before its axis
        was configured keeps the meaning it was written with; otherwise the
        configured axis decides.
        """
        if measurement is None:
            return None
        if measurement.direction is not None:
            return measurement.direction
        axis = self.axis(measurement.metric)
        return axis.direction if axis is not None else None

    # -- comparison ---------------------------------------------------------

    def compare(
        self,
        candidate: Measurement | None,
        baseline: Measurement | None,
    ) -> MetricComparison:
        """Order *candidate* against *baseline* within this space.

        A missing reading, mismatched axes, or an axis with no known direction
        yields ``INCOMPARABLE``: the framework must not read a verdict out of
        evidence it does not have.
        """
        direction = self.direction(candidate)
        if (
            candidate is None
            or baseline is None
            or direction is None
            or candidate.metric != baseline.metric
        ):
            return MetricComparison.INCOMPARABLE
        improvement = (
            candidate.value - baseline.value
            if direction == "max"
            else baseline.value - candidate.value
        )
        axis = self.axis(baseline.metric)
        fraction = (
            axis.relative_noise
            if axis is not None and axis.relative_noise is not None
            else self.relative_noise
        )
        tolerance = abs(baseline.value) * fraction
        if improvement > tolerance:
            return MetricComparison.BETTER
        if improvement < -tolerance:
            return MetricComparison.WORSE
        return MetricComparison.WITHIN_NOISE

    def best[T](
        self,
        points: Sequence[T],
        reading: Callable[[T], Measurement | None],
    ) -> T | None:
        """Return the member of *points* whose reading leads on its axis.

        Ties, including ties within the tolerance, keep the earlier member, so
        callers order *points* to express their own tie-break. Members with no
        reading, or on an axis with no known direction, are skipped.
        """
        best: T | None = None
        best_reading: Measurement | None = None
        for point in points:
            item = reading(point)
            if item is None or self.direction(item) is None:
                continue
            if best_reading is None or self.compare(item, best_reading) is MetricComparison.BETTER:
                best, best_reading = point, item
        return best

    def compare_metrics(
        self, candidate: tuple[MetricRow, ...], baseline: tuple[MetricRow, ...]
    ) -> MetricComparison:
        """Compare complete objective rows by noise-aware Pareto dominance.

        Configured axes supply directions, regardless of metric order or a
        reporting default on a secondary metric. Tradeoffs and missing axes
        are incomparable. With no configured axes, preserve headline comparison.

        This is the one dominance rule: ``BETTER`` is "no objective worse beyond
        its tolerance and at least one better beyond it", whatever the other
        objectives say, so a large gain on one axis is never lost to a headline
        that moved within its noise. `no_worse_metrics` and `dominates` read it.
        """
        return _dominance(self._metric_comparisons(candidate, baseline))

    def no_worse_metrics(
        self, candidate: tuple[MetricRow, ...], baseline: tuple[MetricRow, ...]
    ) -> bool:
        """Whether every objective is present and none regresses beyond its tolerance.

        Unlike reversing dominance, this rejects a tradeoff with a material
        regression, even when another objective improves.
        """
        return self.compare_metrics(candidate, baseline) in {
            MetricComparison.BETTER,
            MetricComparison.WITHIN_NOISE,
        }

    def objective_comparisons(
        self, candidate: Mapping[str, float], baseline: Mapping[str, float]
    ) -> dict[str, MetricComparison]:
        """How ``candidate`` stands against ``baseline`` on each configured axis, by name."""
        return {
            axis.name: self.compare(_reading(axis, candidate), _reading(axis, baseline))
            for axis in self.objectives
        }

    def _metric_comparisons(
        self, candidate: tuple[MetricRow, ...], baseline: tuple[MetricRow, ...]
    ) -> tuple[MetricComparison, ...]:
        if not self.objectives:
            return (
                self.compare(
                    Measurement(candidate[0].name, candidate[0].value, candidate[0].direction)
                    if candidate
                    else None,
                    Measurement(baseline[0].name, baseline[0].value, baseline[0].direction)
                    if baseline
                    else None,
                ),
            )
        mine = {item.name: item.value for item in candidate}
        theirs = {item.name: item.value for item in baseline}
        return tuple(self.objective_comparisons(mine, theirs).values())

    def compare_to_best(
        self,
        candidate: Measurement | None,
        prior: Sequence[Measurement],
    ) -> MetricComparison:
        """Order *candidate* against the best of *prior* on the same axis.

        An empty history is ``BETTER``: the first trusted reading advances an
        archive that holds nothing. A candidate whose direction is unknown is
        ``INCOMPARABLE`` even then, because nothing about it can be ordered.
        """
        if candidate is None or self.direction(candidate) is None:
            return MetricComparison.INCOMPARABLE
        best = self.best(
            [item for item in prior if item.metric == candidate.metric],
            lambda item: item,
        )
        if best is None:
            return MetricComparison.BETTER
        return self.compare(candidate, best)

    # -- frontier -----------------------------------------------------------

    def dominates(self, a: Mapping[str, float], b: Mapping[str, float]) -> bool:
        """Return whether row *a* materially dominates row *b*.

        A row dominates only when it is no worse than *b* within the declared
        tolerance on every configured axis and better by more than that
        tolerance on at least one. A missing axis makes the rows incomparable.
        This intentionally retains near-equal alternatives instead of
        converting a sub-noise fluctuation into an automatic rollback.
        """
        if not self.objectives:
            return False
        return (
            _dominance(tuple(self.objective_comparisons(a, b).values())) is MetricComparison.BETTER
        )

    def frontier[T](
        self,
        points: Sequence[T],
        row: Callable[[T], Mapping[str, float]],
    ) -> list[T]:
        """Return the members of *points* that nothing else dominates."""
        return [
            point
            for point in points
            if not any(
                other is not point and self.dominates(row(other), row(point)) for other in points
            )
        ]

    def complete(self, row: Mapping[str, float]) -> bool:
        """Return whether *row* carries a value for every configured axis."""
        return bool(self.objectives) and all(objective.name in row for objective in self.objectives)


def _dominance(comparisons: Sequence[MetricComparison]) -> MetricComparison:
    """One verdict over per-objective ones: no objective worse and at least one better is ``BETTER``.

    An objective that cannot be compared, or a better one beside a worse one (a tradeoff),
    leaves the rows incomparable; all objectives within their noise is ``WITHIN_NOISE``.
    """
    found = set(comparisons)
    if not found or MetricComparison.INCOMPARABLE in found:
        return MetricComparison.INCOMPARABLE
    found.discard(MetricComparison.WITHIN_NOISE)
    if not found:
        return MetricComparison.WITHIN_NOISE
    return found.pop() if len(found) == 1 else MetricComparison.INCOMPARABLE


def _reading(objective: Objective, row: Mapping[str, float]) -> Measurement | None:
    value = row.get(objective.name)
    if value is None:
        return None
    return Measurement(metric=objective.name, value=value, direction=objective.direction)


# -- decoded readings of core evidence ------------------------------------------------------
#
# Core evidence carries identity and artifacts but no numbers. The numeric facts arrive through
# a declared interpretation operation, never by parsing artifacts inside a strategy, as typed
# readings. These are the immutable shapes every strategy keeps in its state and hands to
# prompt contexts, and the one rule that binds a reading to the record it decodes.

type Finite = Annotated[float, Field(allow_inf_nan=False)]
type Direction = Literal["max", "min"]


class MetricRow(Value):
    """One measured quantity on a declared axis."""

    name: str = Field(min_length=1)
    value: Finite
    direction: Direction
    unit: str | None = None


class PartialRow(Value):
    """What a failed benchmark measured before it stopped, as its evaluator reported it."""

    name: str = Field(min_length=1)
    value: Finite
    direction: Direction
    unit: str | None = None
    target: Finite | None = None
    completed: int | None = Field(default=None, ge=0)
    required: int | None = Field(default=None, gt=0)
    progress_unit: str | None = None


class HostSampleView(Value):
    """One host-contention reading beside a benchmark. ``None`` means unavailable.

    ``gpu_processes`` is ``None`` when no query ran, and empty when a query ran and listed nobody.
    """

    load_1: float | None = None
    load_5: float | None = None
    load_15: float | None = None
    cpu_count: int | None = Field(default=None, ge=0)
    memory_free_bytes: int | None = Field(default=None, ge=0)
    memory_pressure: str | None = None
    gpu_processes: tuple[str, ...] | None = None


class HostContention(Value):
    """Host contention at the start and end of one benchmark of one revision."""

    start: HostSampleView
    end: HostSampleView


class EvidenceReading(Value):
    """Typed reading of one core `EvidenceRef`, decoded by the evidence owner."""

    evidence_id: EvidenceId
    kind: EvidenceKind
    passed: bool
    stage: str = Field(min_length=1)
    protocol: int = Field(default=1, ge=1)
    metrics: tuple[MetricRow, ...] = ()
    partial: PartialRow | None = None
    feedback: str = ""
    unmet_requirement: str | None = Field(default=None, exclude_if=lambda value: value is None)
    """The capability a failed benchmark's evaluator named as unmet, when it named one."""
    host_snapshot: HostContention | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    """Host contention around this benchmark, when the executor could read the host."""

    def headline(self) -> MetricRow | None:
        """The first reported metric, the benchmark's headline reading."""
        return self.metrics[0] if self.metrics else None


class AcceptedReading(EvidenceReading):
    """A reading matched to exactly one requested core evidence record.

    The owner names a reading by evidence ID only, and an ID is unique only within
    its source request. Acceptance binds the reading to the full `EvidenceKey`, so
    every later lookup, assessment source and parent identity uses the key.
    """

    source_request: RequestId

    @property
    def key(self) -> EvidenceKey:
        """The run-wide identity of the evidence this reading decodes."""
        return EvidenceKey(source_request=self.source_request, evidence_id=self.evidence_id)


def reading_of[R: EvidenceReading](readings: tuple[R, ...], kind: EvidenceKind) -> R | None:
    """The latest decoded reading of one evidence kind, or None when absent."""
    matching = [item for item in readings if item.kind is kind]
    return matching[-1] if matching else None


_SAMPLES_TO_POOL = 2


def mean_metrics(rows: Sequence[tuple[MetricRow, ...]]) -> tuple[MetricRow, ...]:
    """The mean of each metric over the rows that report it, in the order first reported.

    Direction and unit are those of the first row that reports the metric. A metric reported by
    one row is that row's.
    """
    values: dict[str, list[float]] = {}
    first: dict[str, MetricRow] = {}
    for row in rows:
        for metric in row:
            first.setdefault(metric.name, metric)
            values.setdefault(metric.name, []).append(metric.value)
    return tuple(
        first[name].model_copy(update={"value": fmean(found)}) for name, found in values.items()
    )


def pooled_benchmarks[R: EvidenceReading](readings: tuple[R, ...]) -> tuple[R, ...]:
    """``readings`` with their passed benchmarks pooled into the latest one, stating their mean.

    Several trusted benchmark records of one revision are samples of one measurement; whoever
    compares the revision reads their mean (`mean_metrics`), never the sample that came last.
    One benchmark, or none that passed, leaves ``readings`` as they are.
    """
    samples = [item for item in readings if item.kind is EvidenceKind.BENCHMARK and item.passed]
    if len(samples) < _SAMPLES_TO_POOL:
        return readings
    pooled = samples[-1].model_copy(update={"metrics": mean_metrics([s.metrics for s in samples])})
    kept = tuple(item for item in readings if item not in samples)
    return (*kept, pooled)


def with_declared_directions[R: EvidenceReading](
    readings: tuple[R, ...], declared: Mapping[str, Direction]
) -> tuple[R, ...]:
    """``readings`` whose rows state the direction the task declared for their axis.

    The evaluator reports a direction for its headline only, and the evidence decoder fills in
    ``max`` for every other row, so an axis the task minimises (TTFT) was shown to agents as
    ``better: max``. The declared objective is the one authority on which way is better; a
    row on no declared axis keeps what it was decoded with.
    """
    return tuple(
        item.model_copy(
            update={
                "metrics": tuple(
                    row.model_copy(update={"direction": declared[row.name]})
                    if row.name in declared
                    else row
                    for row in item.metrics
                )
            }
        )
        for item in readings
    )


def accept_readings(
    readings: tuple[EvidenceReading, ...], requested: tuple[EvidenceRef, ...]
) -> tuple[AcceptedReading, ...] | str:
    """Bind each reading to the one requested record it decodes, or say why not.

    A reading must name a requested evidence ID that no other requested record
    shares, at most once, with the record's own kind, and may claim a pass only for
    a record whose observation succeeded.
    """
    accepted: list[AcceptedReading] = []
    for reading in readings:
        matches = tuple(item for item in requested if item.evidence_id == reading.evidence_id)
        if len(matches) != 1:
            return f"reading {reading.evidence_id.root} names no single requested record"
        (ref,) = matches
        if reading.kind is not ref.kind:
            return f"reading {reading.evidence_id.root} has kind {reading.kind.value}"
        if reading.passed and ref.status is not ObservationStatus.SUCCEEDED:
            return f"reading {reading.evidence_id.root} passed over a {ref.status.value} record"
        if any(item.evidence_id == reading.evidence_id for item in accepted):
            return f"reading {reading.evidence_id.root} repeated"
        accepted.append(AcceptedReading(**reading.model_dump(), source_request=ref.source_request))
    return tuple(accepted)


def judged_evidence(view: RunView, scope: Scope) -> tuple[EvidenceRef, ...]:
    """The trusted evaluations a workstream's roles measured, oldest first.

    A judge reviews the candidate its implementer built in the same scope, and an agent measures
    it through the evaluation tool; core records each result as trusted `local-validation`
    evidence of that scope. A prompt cites these as ``cited_evidence`` so the runtime states
    what they measured (value, unit, direction, verdict) from the evidence ledger.
    """
    held = (
        item
        for item in view.measurements
        if item.provenance == "trusted"
        and item.purpose == "local-validation"
        and item.scope == scope
    )
    return tuple(sorted(held, key=lambda item: item.observation_sequence))
