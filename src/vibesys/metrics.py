"""Objective axes, and the one comparison of readings projected onto them.

A run measures its candidates in a **metric space**: a set of directed
objective axes plus the relative benchmark noise a task declares. Whether a
reading is better, worse or within noise is decided once, by vs-core
(``vs_core.api.judge`` per objective and ``dominance`` over them: the
statistic, the stated default noise and the dominance rule are defined there
and nowhere else). :class:`MetricSpace` only says which objectives a comparison
judges, which way is better on each, and how much noise the task declares for
it; every question that needs a verdict -- "is this reading better?", "does
this row dominate that one?", "which rows are on the frontier?", "which reading
is best?" -- asks :meth:`MetricSpace.compare`, so there is no second rule to
disagree with the first. Callers hand it measurements and consume a
``ComparisonVerdict``; they do not assemble a comparison out of a value, a
direction, and a tolerance.

The space is built once from the task's ``objectives.toml`` and persisted with
the run, so the loop, ``--resume`` reprojection, and the server read path all
compare within the same space instead of being handed a tolerance through a
call chain (issue #507).
"""

from __future__ import annotations

from dataclasses import dataclass as standard_dataclass
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator
from pydantic.dataclasses import dataclass

from vs_core.api import (
    ComparisonVerdict,
    EvidenceId,
    EvidenceKey,
    EvidenceKind,
    EvidenceRef,
    ObservationStatus,
    RequestId,
    Runs,
    RunView,
    Scope,
    Value,
    dominance,
    judge,
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

    ``runs`` and ``spread`` state what ``value`` rests on, which the comparison
    needs for its noise (`vs_core.api.Runs`): ``value`` is the mean of ``runs``
    runs whose relative standard deviation is ``spread``. A reading that states
    neither is one run of unknown spread, which the comparison judges under the
    task's declared noise, else the stated default (``vs_core.api.DEFAULT_RELATIVE_NOISE``).
    """

    metric: str
    value: float
    direction: Literal["max", "min"] | None = None
    runs: Annotated[int, Field(ge=1)] = 1
    spread: Annotated[FiniteFloat, Field(ge=0.0)] = 0.0

    @property
    def stands(self) -> Runs:
        """The runs this reading is the mean of, as the comparison takes them."""
        return Runs(mean=self.value, count=self.runs, spread=self.spread)


class MetricSpace(BaseModel):
    """The directed axes and declared noise one run compares within.

    ``relative_noise`` is the run's declared benchmark variation of one run,
    read from ``[pareto] relative_noise``; an axis may declare its own, which
    wins (`noise_of`). Neither is a tolerance of its own: they are the
    ``declared`` argument of vs-core's judgement, which turns them, and the runs a
    reading rests on, into the band a difference must exceed. A task that
    declares none is judged under vs-core's stated default, never a zero band.
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

    def noise_of(self, metric: str) -> float:
        """The relative variation of one run the task declares for ``metric``; 0 declares none.

        The axis's own declaration wins, else the space's: the same precedence as the task's
        (`vs_core.api.ComparisonPolicy.floor_of`), so the product and core never read one
        task's noise two ways.
        """
        axis = self.axis(metric)
        if axis is not None and axis.relative_noise is not None:
            return axis.relative_noise
        return self.relative_noise

    def compare(
        self, candidate: Sequence[Measurement], base: Sequence[Measurement]
    ) -> ComparisonVerdict:
        """``candidate`` against ``base`` by vs-core's rule: the one comparison in the system.

        Each objective is judged by ``vs_core.api.judge`` under the noise the task declares for
        it (`noise_of`) and the runs each side's reading rests on, and the verdicts give the
        result by ``vs_core.api.dominance``: ``BETTER`` is no objective worse and at least one
        better, whatever the others did; a tradeoff is ``WORSE``; ``WITHIN_NOISE`` is every
        objective undecided; an objective that cannot be judged (a reading one side lacks, a
        base of zero, no known direction) leaves the result ``INCONCLUSIVE``.

        The objectives judged are the space's configured axes, which supply the directions
        whatever order the readings come in or default a secondary reading carries. With none
        configured, the headline (the first reading) of each side is judged, in the direction it
        states.
        """
        if not self.objectives:
            return self._headline(candidate, base)
        mine = _by_metric(candidate)
        theirs = _by_metric(base)
        return dominance(
            self._judged(mine.get(axis.name), theirs.get(axis.name), axis.direction)
            for axis in self.objectives
        )

    def compare_rows(
        self, candidate: Sequence[MetricRow], base: Sequence[MetricRow]
    ) -> ComparisonVerdict:
        """`compare` for the rows of a benchmark, which state the runs each value rests on."""
        return self.compare([row.reading() for row in candidate], [row.reading() for row in base])

    def no_worse_metrics(self, candidate: Sequence[MetricRow], base: Sequence[MetricRow]) -> bool:
        """Whether no objective of ``candidate`` is worse than ``base``'s, and every one could be judged.

        Rejects a tradeoff, whatever the other objective gained, and a comparison that is
        inconclusive: a regression is never assumed away.
        """
        return self.compare_rows(candidate, base) in {
            ComparisonVerdict.BETTER,
            ComparisonVerdict.WITHIN_NOISE,
        }

    def _headline(
        self, candidate: Sequence[Measurement], base: Sequence[Measurement]
    ) -> ComparisonVerdict:
        """The first reading of each side, when no axis is configured to name the objectives."""
        mine = candidate[0] if candidate else None
        theirs = base[0] if base else None
        if mine is None or theirs is None or mine.metric != theirs.metric:
            return ComparisonVerdict.INCONCLUSIVE
        return self._judged(mine, theirs, mine.direction)

    def _judged(
        self, mine: Measurement | None, theirs: Measurement | None, direction: Direction | None
    ) -> ComparisonVerdict:
        """One objective's verdict: inconclusive without both readings and a direction."""
        if mine is None or theirs is None or direction is None:
            return ComparisonVerdict.INCONCLUSIVE
        return judge(
            mine.stands, theirs.stands, direction=direction, declared=self.noise_of(mine.metric)
        ).verdict

    def best[T](
        self,
        points: Sequence[T],
        reading: Callable[[T], Measurement | None],
    ) -> T | None:
        """Return the member of *points* whose reading leads on its axis.

        Ties, including ties within the noise, keep the earlier member, so
        callers order *points* to express their own tie-break. Members with no
        reading, or on an axis with no known direction, are skipped.
        """
        best: T | None = None
        best_reading: Measurement | None = None
        for point in points:
            item = reading(point)
            if item is None or self.direction(item) is None:
                continue
            if best_reading is None or self.compare((item,), (best_reading,)) is (
                ComparisonVerdict.BETTER
            ):
                best, best_reading = point, item
        return best

    def compare_to_best(
        self,
        candidate: Measurement | None,
        prior: Sequence[Measurement],
    ) -> ComparisonVerdict:
        """Order *candidate* against the best of *prior* on the same axis.

        An empty history is ``BETTER``: the first trusted reading advances an
        archive that holds nothing. A candidate whose direction is unknown is
        ``INCONCLUSIVE`` even then, because nothing about it can be ordered.
        """
        if candidate is None or self.direction(candidate) is None:
            return ComparisonVerdict.INCONCLUSIVE
        best = self.best(
            [item for item in prior if item.metric == candidate.metric],
            lambda item: item,
        )
        if best is None:
            return ComparisonVerdict.BETTER
        return self.compare((candidate,), (best,))

    # -- frontier -----------------------------------------------------------

    def dominates(self, a: Mapping[str, float], b: Mapping[str, float]) -> bool:
        """Return whether row *a* dominates row *b* by the rule of `compare`.

        A row dominates only when no configured axis is worse beyond its noise and at
        least one is better beyond it. A missing axis leaves the comparison inconclusive.
        This intentionally retains near-equal alternatives instead of
        converting a sub-noise fluctuation into an automatic rollback.
        """
        if not self.objectives:
            return False
        return self.compare(self._axes_of(a), self._axes_of(b)) is ComparisonVerdict.BETTER

    def _axes_of(self, row: Mapping[str, float]) -> tuple[Measurement, ...]:
        """The readings of *row* on the configured axes it carries, one run each."""
        return tuple(
            Measurement(metric=axis.name, value=row[axis.name], direction=axis.direction)
            for axis in self.objectives
            if axis.name in row
        )

    def frontier[T](
        self,
        points: Sequence[T],
        row: Callable[[T], Mapping[str, float]],
    ) -> list[T]:
        """Return the members of *points* that nothing else dominates.

        The judgement is read against a base's size (a ratio), so near its edge two rows can each
        beat the other, one on each objective: neither is then behind the other, and a row
        removes another only if the other does not remove it in return. The frontier of
        any rows with a complete one is never empty for want of that.
        """
        return [
            point
            for point in points
            if not any(
                other is not point
                and self.dominates(row(other), row(point))
                and not self.dominates(row(point), row(other))
                for other in points
            )
        ]

    def complete(self, row: Mapping[str, float]) -> bool:
        """Return whether *row* carries a value for every configured axis."""
        return bool(self.objectives) and all(objective.name in row for objective in self.objectives)


def _by_metric(readings: Sequence[Measurement]) -> dict[str, Measurement]:
    """The readings by metric, the first of a metric a side reports twice."""
    found: dict[str, Measurement] = {}
    for item in readings:
        found.setdefault(item.metric, item)
    return found


# -- decoded readings of core evidence ------------------------------------------------------
#
# Core evidence carries identity and artifacts but no numbers. The numeric facts arrive through
# a declared interpretation operation, never by parsing artifacts inside a strategy, as typed
# readings. These are the immutable shapes every strategy keeps in its state and hands to
# prompt contexts, and the one rule that binds a reading to the record it decodes.

type Finite = Annotated[float, Field(allow_inf_nan=False)]
type Direction = Literal["max", "min"]


class MetricRow(Value):
    """One measured quantity on a declared axis, and the runs its value rests on."""

    name: str = Field(min_length=1)
    value: Finite
    direction: Direction
    unit: str | None = None
    runs: int = Field(default=1, ge=1, exclude_if=lambda value: value == 1)
    """How many trusted runs ``value`` is the mean of (`mean_metrics`); a row that states none is one."""
    spread: Annotated[float, Field(allow_inf_nan=False, ge=0.0)] = Field(
        default=0.0, exclude_if=lambda value: value == 0
    )
    """The runs' relative standard deviation; 0 for a single run, or runs that all read alike."""

    def reading(self) -> Measurement:
        """This row as the reading a comparison judges (`MetricSpace.compare`)."""
        return Measurement(
            metric=self.name,
            value=self.value,
            direction=self.direction,
            runs=self.runs,
            spread=self.spread,
        )


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
    one row is that row's. Each row is one run: the mean states how many rows it is the mean of
    and the runs' spread (`vs_core.api.Runs`), which a comparison needs for its noise.
    """
    values: dict[str, list[float]] = {}
    first: dict[str, MetricRow] = {}
    for row in rows:
        for metric in row:
            first.setdefault(metric.name, metric)
            values.setdefault(metric.name, []).append(metric.value)
    return tuple(
        first[name] if len(found) == 1 else first[name].model_copy(update=_runs_of(found))
        for name, found in values.items()
    )


def _runs_of(values: Sequence[float]) -> dict[str, float]:
    """The fields of a row that is the mean of ``values``: the mean, how many, and their spread."""
    stood = Runs.of(values)
    return {"value": stood.mean, "runs": stood.count, "spread": stood.spread}


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
