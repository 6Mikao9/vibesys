"""Samples of one measured input, their comparison with a base, and when a base is measured.

A *measurement* is every conclusive run of one measured input under one evaluation. Its runs
are ``Sample``s of it: they are aggregated, never picked among, and none is refused because
there are several. This module owns the pure decisions over samples:

- ``evaluation_of`` names an evaluation (everything but the commit and purpose), so two runs of
  one input in one host and gates are samples of one measurement;
- ``compare`` compares a candidate's samples with its base's: a ``Comparison``;
- ``base_missing``, ``remeasures_base`` and ``remeasures_candidate`` decide when either side
  is measured again, and nothing else does.

The statistic
-------------

A comparison judges every objective its samples report with the one statistic of
`vs_core._dominance` (`judge`) and gives the dominance result over them (`dominance`): a
candidate that is no worse on any objective and better on one is ``better``, whatever the
headline did. An objective's noise is the task's declared one when it states it
(`ComparisonPolicy.floor_of`: the objective's own tolerance, else the task's
``relative_noise``), else what the samples show, else a stated default; it is never zero, so
every comparison has a verdict. A close call is a comparison whose every objective is within its
noise (`close_call`): more samples could still make it a dominating result or a regression, and
a decided result is never a close call.

When each side is measured
--------------------------

The base is measured once per (measured input, evaluation): the first time a candidate is
compared with it and no run of it is held or running, and again when every run of it a
candidate waited on ended without a sample (`vs_core._base_runs`). It is measured again only
when its samples are stale (older than ``TaskTerms.base_staleness`` on the run clock) or when a
candidate's result is a close call (`close_call`), because one more base sample narrows the
noise. A close call never takes a base past `_base_max_samples` fresh samples:
``BASE_MAX_SAMPLES`` (2, so at most one extra base run) when every objective declares its noise,
and ``MIN_SAMPLES_WITHOUT_FLOOR`` (3, so at most two extra base runs) when some does not, the
fewest that show a spread of their own. Nor does it chase staleness: when base samples go stale faster than a base
run takes, the run a close call earned can land without growing the base's fresh samples (the
sample it was meant to join expired meanwhile), and that run is the last one a close call earns
either side of that comparison (`base_run_stalled`). The stop counts only the runs core took
for the comparison (its scope and candidate input), so a later candidate of the attempt,
compared once the base went stale, takes its own runs of the base. Every run of an identity in
a scope counts toward its submission limit (`vs_core._base_runs`), so an attempt's base is run
at most ``MAX_SUBMISSIONS_PER_MEASUREMENT`` times in its scope, however many candidate inputs
its agent measures, and core keeps at most one run of each side in flight per scope.

Core's runs serve the comparison only. The agent's turn never waits for them (they are not its
attempt's history, `vs_core._evaluation_history`); the comparison it reads next grows as they
land. The strategy's verdict does wait: a Measure that asks for the comparison
(``Measure.compared``) is compared with the attempt's base too (`vs_core._base_runs.base_of`),
and ``ctx.evaluation.measure_compared`` hands the comparison back once no run that feeds it is
under way (`comparison_settling`), or as it stands when its bound is spent. A verdict taken on
the first run alone would leave every close call open.

An objective that declares no noise has it estimated from the candidate's own runs too, and
nobody else gives them: an agent's call for content it already measured is answered with that
evaluation, and the official check adds none. So a close call on such an objective also earns
the candidate's input more runs in its scope (`remeasures_candidate`), up to
``MIN_SAMPLES_WITHOUT_FLOOR`` in all. When every objective declares its noise one run a side
gives a verdict, and core takes no run of the candidate. A benchmark whose runs all read alike
shows no spread and falls back to the stated default noise, so its runs are bounded too.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ._dominance import VERDICT_NOISES, Runs, dominance, judge
from ._submission_receipts import receipt_ended
from ._values import digest
from .types.common import (
    MAX_SUBMISSIONS_PER_MEASUREMENT,
    EvidenceKind,
    ObservationStatus,
    RevisionRef,
    Value,
)
from .types.evaluation import (
    EvidenceRef,
    MeasurementIdentity,
    MeasurementStageIdentity,
    OwnedJob,
    PreparedSubmissionReceipt,
)
from .types.measurement import (
    BaseRef,
    Comparison,
    ComparisonSettled,
    ComparisonVerdict,
    Direction,
    EffectVerdict,
    ObjectiveComparison,
    Reading,
    Sample,
    StatedComparison,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from .types.common import EvidenceKey, RequestId, RunFacts, Scope, Seconds, TaskTerms
    from .types.evaluation import EvaluationState, SubmissionReceipt
    from .types.kernel import RunState

__all__ = [
    "BASE_MAX_SAMPLES",
    "MAX_CORE_BASE_RUNS",
    "MIN_SAMPLES_WITHOUT_FLOOR",
    "SETTLE_RUNS",
    "ComparisonPolicy",
    "Measured",
    "Moment",
    "as_answer",
    "base_in_flight",
    "base_missing",
    "base_run_stalled",
    "claimed_base",
    "close_call",
    "closed_answers",
    "compare",
    "comparison_of",
    "comparison_of_result",
    "comparison_settling",
    "comparisons_of",
    "comparisons_settled",
    "comparisons_to_publish",
    "effect_resolution",
    "effect_verdict",
    "evaluation_of",
    "feeding_run_under_way",
    "fresh",
    "latest_comparisons",
    "measures",
    "moment_of",
    "opened_answer",
    "policy_of",
    "remeasures_base",
    "remeasures_candidate",
    "restated_answers",
    "run_readings",
    "run_under_way",
    "running_baseline",
    "sample_of",
    "samples_of",
    "scope_samples",
    "stated_comparisons",
    "terms_policy",
]

SETTLE_RUNS = 3
"""How many measurement limits a comparison may keep settling for after an official run ends:
one for each run a close call earns a side without a declared floor. A flow waits for the
comparison that long at most, and the official record stops moving then
(``OwnedJob.answer_until``)."""
BASE_MAX_SAMPLES = 2
"""The most fresh samples a close call earns a base under a declared floor: one run, then one
more, never a chase.

A base run costs as much as a candidate's, so a close call narrows the noise with one more base
sample and stops there."""
MIN_SAMPLES_WITHOUT_FLOOR = 3
"""The runs each side takes to estimate an undeclared objective's noise from its own spread.

Three a side is the fewest that give each side's spread two degrees of freedom; it is the most
fresh samples a close call earns a base then, and the most runs of its input a close call earns
a candidate."""
MAX_CORE_BASE_RUNS = MIN_SAMPLES_WITHOUT_FLOOR * MAX_SUBMISSIONS_PER_MEASUREMENT
"""The most runs of an attempt's base core takes in its scope over the attempt's lifetime.

The base is one measurement however many candidate inputs the agent measures, and its samples
go stale (``TaskTerms.base_staleness``), so a long attempt measures it again for a later
candidate. This is enough for five comparisons each to take the base to the runs that estimate
an undeclared noise, and it is the limit of the submission budget core opens for the base
(`vs_core._base_runs`), so it holds whoever asked for each run.
"""


@dataclass(frozen=True, slots=True)
class Measured:
    """The key of one measurement: an evaluation of one measured input."""

    evaluation: str
    measured_input: str


@dataclass(frozen=True, slots=True)
class ComparisonPolicy:
    """What the task fixes about comparing: its noise floors, base staleness and axes."""

    noise_floor: float = 0.0
    """The declared relative variation of one run on a metric with no floor of its own
    (``TaskTerms.relative_noise``)."""
    metric_floors: tuple[tuple[str, float], ...] = ()
    """The floor each objective declares for itself (``TaskObjective.noise``), by metric name."""
    base_max_age: float | None = None
    """Seconds after which a base sample is stale; None never lets one go stale."""
    directions: tuple[tuple[str, Direction], ...] = ()
    """The task's objectives by metric name, for a reading that states no direction."""

    def floor_of(self, metric: str) -> float:
        """The relative variation of one run the task declares for ``metric``.

        The objective's own declared tolerance when it has one, else the task's
        ``relative_noise``: the most specific declaration wins, as it does for the product's
        ``MetricSpace``, so the two never read one task's noise two ways.
        """
        return dict(self.metric_floors).get(metric, self.noise_floor)

    def declares_floor(self, metric: str) -> bool:
        """Whether the task stated the noise of ``metric``; a floor of 0 states nothing (module docstring)."""
        return self.floor_of(metric) > 0

    @property
    def headline_floor_declared(self) -> bool:
        """Whether the task stated the noise of its headline metric, which every comparison states.

        A sample carries the headline reading of its run (`sample_of`), and the objectives come
        headline first (``TaskTerms.objectives``). With no objectives only ``relative_noise`` can
        state it.
        """
        headline = next((name for name, _ in self.directions), None)
        return self.noise_floor > 0 if headline is None else self.declares_floor(headline)


@dataclass(frozen=True, slots=True)
class Moment:
    """When a decision over samples is taken (the run clock) and the policy it is taken under."""

    now: Seconds
    policy: ComparisonPolicy


def moment_of(run: RunState) -> Moment:
    """The moment of a run's own clock, under the comparison policy its task fixes."""
    return Moment(now=run.now_at, policy=policy_of(run.facts))


def policy_of(facts: RunFacts) -> ComparisonPolicy:
    """The comparison policy a run's task fixes; the default for a run without task terms."""
    return terms_policy(facts.task)


def terms_policy(task: TaskTerms | None) -> ComparisonPolicy:
    """The comparison policy ``task`` fixes; the default for no task terms."""
    if task is None:
        return ComparisonPolicy()
    return ComparisonPolicy(
        noise_floor=task.relative_noise,
        metric_floors=tuple(
            (item.name, item.noise.relative_noise)
            for item in task.objectives
            if item.noise is not None
        ),
        base_max_age=task.base_staleness,
        directions=tuple((item.name, item.direction) for item in task.objectives),
    )


class _Evaluation(Value):
    """What two runs share when they are the same measurement of an input."""

    evaluator_digest: str
    workload_digest: str
    environment_digest: str
    recipe_digest: str
    stages: tuple[MeasurementStageIdentity, ...]
    accuracy_stage: str | None
    accuracy_gated: tuple[EvidenceKind, ...]


def evaluation_of(identity: MeasurementIdentity) -> str:
    """The digest of an evaluation: the identity of a run less its commit, purpose and input.

    Two runs share it exactly when they are the same measurement of an input: the same
    evaluator, workload, environment (host), recipe, gates and accuracy gating.
    """
    return digest(
        _Evaluation(
            evaluator_digest=identity.evaluator_digest,
            workload_digest=identity.workload_digest,
            environment_digest=identity.environment_digest,
            recipe_digest=identity.recipe_digest,
            stages=identity.stages,
            accuracy_stage=identity.accuracy_stage,
            accuracy_gated=identity.accuracy_gated,
        )
    )


def measures(evidence: EvidenceRef) -> bool:
    """Whether ``evidence`` is a measurement: a trusted, successful benchmark record with readings.

    Only such a record gives a sample (`sample_of`) and only such a record states an answer
    (`as_answer`): a failed or untrusted record keeps the reading its run reported.
    """
    return (
        evidence.kind == EvidenceKind.BENCHMARK
        and evidence.status == ObservationStatus.SUCCEEDED
        and evidence.provenance == "trusted"
        and bool(evidence.readings)
    )


def sample_of(
    job: OwnedJob, evidence: EvidenceRef, observed_at: Seconds, measured_input: str | None = None
) -> Sample | None:
    """The sample a finished run gives, or None when it is not a measurement record.

    The run must be a framework run (not a re-read of one: ``reuses`` is unset), and its
    benchmark record trusted, successful and carrying the readings of its objectives. The input is the
    plan's own or, for a run whose plan names none, the ``measured_input`` the runtime
    digested at its commit; with neither there is no sample, since a sample cannot exist
    without its commit and the input the evaluation read there.
    """
    plan = job.plan
    named = plan.measured_input or measured_input
    if (
        job.reuses is not None
        or not measures(evidence)
        or named is None
        or not isinstance(plan.candidate, RevisionRef)
    ):
        return None
    return Sample(
        run=job.submission_id,
        scope=job.scope,
        commit=plan.candidate,
        measured_input=named,
        evaluation=evaluation_of(MeasurementIdentity.from_plan(plan, plan.candidate)),
        gates=tuple(stage.stage_id for stage in plan.stages),
        host=plan.environment_digest,
        dedicated_resources=plan.dedicated_measurement_resources,
        readings=evidence.readings,
        observed_at=observed_at,
        base=job.base,
    )


def as_answer(
    state: EvaluationState, job: OwnedJob, evidence: EvidenceRef, now_at: Seconds
) -> EvidenceRef:
    """``evidence`` as the official measurement's answer: each reading is the samples' mean.

    The official measurement of an input is answered by every sample the scope holds of it, the
    run that answers included when it is a new one (`vs_core._measurement_reuse`). The
    benchmark record that carries the answer through the ledger, and so to every reader that
    ranks candidates, states the mean of those samples on every objective (`scope_samples`):
    exactly the candidate values the comparison with the base states (`comparison_of`), never
    one run's readings. A later sample of the same input in the same scope (a close call's run
    after the official check) restates the record (`restated_answers`), so the record and the
    settled comparison are one figure. The run's own readings stay in its ``Sample``. Once an
    answer of the input in the scope has closed (``OwnedJob.answer_until``), every official
    record of it states the figures that answer closed on (`_closed_answer`), so a run that
    lands later does not part the ledger from the verdict the strategy took.

    Only a measurement record states an answer (`measures`): a failed or untrusted record
    keeps the readings its run reported, so a failed run never reads as the mean of runs that
    passed. Any other record is returned as it is, and so is a reading no held sample reports.
    """
    plan = job.plan
    if (
        plan.purpose != "official"
        or not measures(evidence)
        or plan.measured_input is None
        or not isinstance(plan.candidate, RevisionRef)
    ):
        return evidence
    measured = Measured(
        evaluation_of(MeasurementIdentity.from_plan(plan, plan.candidate)), plan.measured_input
    )
    closed = _closed_answer(state, job, measured, now_at)
    if closed is not None:
        closed_values = {item.metric: item.value for item in closed}
        return evidence.model_copy(
            update={
                "readings": tuple(
                    item.model_copy(update={"value": closed_values[item.metric]})
                    if item.metric in closed_values
                    else item
                    for item in evidence.readings
                )
            }
        )
    held = scope_samples(state, job.scope, measured)
    own = sample_of(job, evidence, 0.0)
    if own is not None and all(s.run != own.run for s in held):
        held = (*held, own)
    return evidence.model_copy(
        update={"readings": tuple(_mean_reading(item, held) for item in evidence.readings)}
    )


def _mean_reading(reading: Reading, samples: tuple[Sample, ...]) -> Reading:
    """``reading`` stating the mean of ``samples``' readings of its objective, if they all report it."""
    found = [_reading_of(sample, reading.metric) for sample in samples]
    if not found or any(item is None for item in found):
        return reading
    return reading.model_copy(update={"value": _mean(tuple(item.value for item in found if item))})


def _reading_of(sample: Sample, metric: str) -> Reading | None:
    """The reading ``sample`` took of the objective ``metric``, if its run reported it."""
    return next((item for item in sample.readings if item.metric == metric), None)


def _closed_answer(
    state: EvaluationState, job: OwnedJob, measured: Measured, now_at: Seconds
) -> tuple[Reading, ...] | None:
    """The readings the first closed official answer of ``measured`` in ``job``'s scope states.

    None while no answer of it has closed: no official job of it whose ``answer_until`` has come.
    """
    for other in state.jobs:
        plan = other.plan
        if (
            other.submission_id != job.submission_id
            and other.answer_until is not None
            and other.answer_until <= now_at
            and other.scope == job.scope
            and plan.purpose == "official"
            and plan.measured_input == measured.measured_input
            and isinstance(plan.candidate, RevisionRef)
            and evaluation_of(MeasurementIdentity.from_plan(plan, plan.candidate))
            == measured.evaluation
        ):
            for record in other.evidence:
                if measures(record):
                    return record.readings
    return None


def restated_answers(state: EvaluationState, sample: Sample, now_at: Seconds) -> EvaluationState:
    """``state`` with the open official answers of ``sample``'s input in its scope stating their mean.

    The official measurement's ledger record states the mean of every sample its scope holds
    of its input (`as_answer`), so a sample that lands after the answer was stored restates it:
    in the ledger and in the job that carries it. The record keeps its identity; only its
    headline reading moves. Only while the answer is open (``OwnedJob.answer_until``): once the
    flow that waited for it took its verdict, the record states that verdict's figure for good.
    """
    restated: dict[EvidenceKey, EvidenceRef] = {}
    for job in state.jobs:
        plan = job.plan
        if (
            job.answer_until is not None
            and now_at < job.answer_until
            and job.scope == sample.scope
            and plan.purpose == "official"
            and plan.measured_input == sample.measured_input
            and isinstance(plan.candidate, RevisionRef)
            and evaluation_of(MeasurementIdentity.from_plan(plan, plan.candidate))
            == sample.evaluation
        ):
            for record in job.evidence:
                answer = as_answer(state, job, record, now_at)
                if answer != record:
                    restated[record.key] = answer
    if not restated:
        return state

    def now(records: tuple[EvidenceRef, ...]) -> tuple[EvidenceRef, ...]:
        return tuple(restated.get(record.key, record) for record in records)

    return state.model_copy(
        update={
            "evidence": now(state.evidence),
            "jobs": tuple(
                job.model_copy(update={"evidence": now(job.evidence)})
                if any(record.key in restated for record in job.evidence)
                else job
                for job in state.jobs
            ),
        }
    )


def opened_answer(state: EvaluationState, job: OwnedJob, now_at: Seconds) -> EvaluationState:
    """``state`` with the answer of ``job``, an official run that just ended, open to restating.

    It stays open for ``SETTLE_RUNS`` times the run's limit, the longest a flow waits for its
    comparison to settle (`closed_answers` closes it sooner, when it settles).
    """
    if not job.terminal or job.plan.purpose != "official" or job.answer_until is not None:
        return state
    until = now_at + SETTLE_RUNS * job.plan.time_bound
    return state.model_copy(
        update={
            "jobs": tuple(
                j.model_copy(update={"answer_until": until})
                if j.submission_id == job.submission_id
                else j
                for j in state.jobs
            )
        }
    )


def closed_answers(state: EvaluationState, moment: Moment) -> EvaluationState:
    """``state`` with every open answer whose comparison no longer settles closed now.

    A flow that waits for the comparison reads it once it is not settling (on core's report,
    `comparisons_settled`), and takes its verdict on the record as it stands then; a sample
    that lands later restates nothing (`restated_answers`), so the round's figure and the
    ledger's stay one. An answer with no comparison is closed at once: its flow does not wait.
    """
    now_at = moment.now
    open_jobs = [
        job for job in state.jobs if job.answer_until is not None and now_at < job.answer_until
    ]
    if not open_jobs:
        return state
    settling = {
        (stated.comparison.scope, stated.comparison.candidate_input)
        for stated in stated_comparisons(state, moment)
        if stated.settling
    }
    closing = {
        job.submission_id
        for job in open_jobs
        if (job.scope, job.plan.measured_input) not in settling
    }
    if not closing:
        return state
    return state.model_copy(
        update={
            "jobs": tuple(
                job.model_copy(update={"answer_until": now_at})
                if job.submission_id in closing
                else job
                for job in state.jobs
            )
        }
    )


def run_readings(
    state: EvaluationState, job: OwnedJob, evidence: EvidenceRef
) -> tuple[Reading, ...]:
    """The readings ``job``'s own run took for ``evidence``: its sample's, else the record's.

    An official run's ledger record states the measurement's answer (`as_answer`), so what the
    run itself read is its ``Sample``.
    """
    if not evidence.readings:
        return ()
    own = next((s for s in state.samples if s.run == job.submission_id), None)
    return evidence.readings if own is None else own.readings


def samples_of(state: EvaluationState, measured: Measured) -> tuple[Sample, ...]:
    """Every sample of one measurement, in the order its runs ended."""
    return tuple(
        sample
        for sample in state.samples
        if sample.evaluation == measured.evaluation
        and sample.measured_input == measured.measured_input
    )


def scope_samples(state: EvaluationState, scope: Scope, measured: Measured) -> tuple[Sample, ...]:
    """The samples of one measurement that runs in ``scope`` gave, in the order they ended.

    A candidate's side of a comparison is its own scope's samples only (`comparison_of`): each
    attempt, and each generation of a reopened one, is compared on the runs it made, though
    another holds samples of the same input. Everything that counts a candidate's samples
    counts them here, so the comparison and the official answer (`as_answer`) never disagree
    on how many there are.
    """
    return tuple(s for s in samples_of(state, measured) if s.scope == scope)


def fresh(samples: Iterable[Sample], moment: Moment) -> tuple[Sample, ...]:
    """The samples young enough to count at ``moment``."""
    limit = moment.policy.base_max_age
    return tuple(s for s in samples if limit is None or moment.now - s.observed_at <= limit)


def _mean(values: tuple[float, ...]) -> float:
    return sum(values) / len(values)


def _direction(
    metric: str, samples: tuple[Sample, ...], policy: ComparisonPolicy
) -> Direction | None:
    """Which way is better for ``metric``: the one every sample's reading states, else the task's."""
    stated = {item.direction for s in samples if (item := _reading_of(s, metric)) is not None}
    if len(stated) == 1 and None not in stated:
        return next(iter(stated))
    if stated != {None}:
        return None
    return dict(policy.directions).get(metric)


def _compared_metrics(candidate: tuple[Sample, ...], base: tuple[Sample, ...]) -> tuple[str, ...]:
    """The objectives every sample of both sides reports, in the candidate's latest reading order."""
    both = (*candidate, *base)
    return tuple(
        item.metric
        for item in candidate[-1].readings
        if all(_reading_of(sample, item.metric) is not None for sample in both)
    )


def compare(
    candidate: tuple[Sample, ...],
    base: tuple[Sample, ...],
    *,
    scope: Scope,
    anchor: BaseRef,
    policy: ComparisonPolicy,
) -> Comparison | None:
    """The candidate's samples compared with the base's on every objective, or None.

    ``anchor`` names the base the samples are compared with. An objective is compared when every
    sample of both sides reports it and a direction is known for it (the readings' own, else the
    task's); each is judged by the one statistic (`vs_core._dominance.judge`) under the noise the
    task declares for it, and the comparison's result is the dominance over them (`dominance`).
    They cannot be compared when either side is empty or no objective is left to compare.
    """
    if not candidate or not base:
        return None
    both = (*candidate, *base)
    objectives: list[ObjectiveComparison] = []
    for metric in _compared_metrics(candidate, base):
        direction = _direction(metric, both, policy)
        if direction is None:
            continue
        mine = _readings_of(candidate, metric)
        theirs = _readings_of(base, metric)
        judged = judge(
            Runs.of(tuple(item.value for item in mine)),
            Runs.of(tuple(item.value for item in theirs)),
            direction=direction,
            declared=policy.floor_of(metric),
        )
        objectives.append(
            ObjectiveComparison(
                metric=metric,
                unit=mine[-1].unit,
                direction=direction,
                candidate_value=_mean(tuple(item.value for item in mine)),
                base_value=_mean(tuple(item.value for item in theirs)),
                ratio=judged.ratio,
                noise=judged.noise,
                verdict=judged.verdict,
            )
        )
    if not objectives:
        return None
    last = candidate[-1]
    return Comparison(
        scope=scope,
        candidate=last.commit,
        candidate_input=last.measured_input,
        base=anchor.commit,
        base_input=anchor.measured_input,
        verdict=dominance(item.verdict for item in objectives),
        candidate_samples=len(candidate),
        base_samples=len(base),
        objectives=tuple(objectives),
    )


def _readings_of(samples: tuple[Sample, ...], metric: str) -> tuple[Reading, ...]:
    return tuple(item for s in samples if (item := _reading_of(s, metric)) is not None)


def comparison_of(
    state: EvaluationState, scope: Scope, measured: Measured, moment: Moment
) -> Comparison | None:
    """The scope's candidate input compared with its base, over every sample held at ``moment``.

    Every run of the input in the scope is a sample of it, whoever asked for the run (the
    strategy's own check adds one to the agent's); the base is the one an agent's run named.
    """
    mine, anchor = _anchored(state, scope, measured)
    if anchor is None:
        return None
    base = fresh(samples_of(state, Measured(measured.evaluation, anchor.measured_input)), moment)
    return compare(mine, base, scope=scope, anchor=anchor, policy=moment.policy)


def _anchored(
    state: EvaluationState, scope: Scope, measured: Measured
) -> tuple[tuple[Sample, ...], BaseRef | None]:
    """The scope's samples of the input and the base its latest anchored sample names."""
    mine = scope_samples(state, scope, measured)
    return mine, next((s.base for s in reversed(mine) if s.base is not None), None)


def comparisons_of(state: EvaluationState, moment: Moment) -> tuple[Comparison, ...]:
    """The current comparison of every (scope, candidate input) that has a base's samples."""
    seen: dict[tuple[str, str, str], Sample] = {}
    for sample in state.samples:
        if sample.base is not None:
            key = (sample.scope.model_dump_json(), sample.evaluation, sample.measured_input)
            seen[key] = sample
    found = (
        comparison_of(state, s.scope, Measured(s.evaluation, s.measured_input), moment)
        for s in seen.values()
    )
    return tuple(item for item in found if item is not None)


def latest_comparisons(
    state: EvaluationState, scope: Scope, inputs: Iterable[str], moment: Moment
) -> tuple[Comparison, ...]:
    """The comparison each of the scope's candidate ``inputs`` has now, over the samples held.

    Each (input, base input) appears at most once, in the order its comparison was first
    logged; an input with no comparison logged yet (its base is not measured) is left out. The
    log is append-only, so its newest entry may rest on base samples that have since gone
    stale: what is given is the comparison the samples fresh at ``moment`` make
    (`comparison_of`). When only some base samples went stale it is recomputed over the rest,
    and when none is fresh the input is left out, so no reader is told a verdict whose base no
    longer counts.
    """
    wanted = frozenset(inputs)
    subjects = dict.fromkeys(
        (comparison.candidate_input, comparison.base_input)
        for comparison in state.comparisons
        if comparison.scope == scope and comparison.candidate_input in wanted
    )
    found: list[Comparison] = []
    for candidate_input, base_input in subjects:
        latest = next(
            (
                s
                for s in reversed(state.samples)
                if s.scope == scope
                and s.measured_input == candidate_input
                and s.base is not None
                and s.base.measured_input == base_input
            ),
            None,
        )
        if latest is None:
            continue
        now = comparison_of(state, scope, Measured(latest.evaluation, candidate_input), moment)
        if now is not None and now.base_input == base_input and now not in found:
            found.append(now)
    return tuple(found)


def comparison_settling(state: EvaluationState, comparison: Comparison) -> bool:
    """Whether a run that will feed ``comparison`` has not ended: it changes when that run lands.

    Any run of either side counts, whoever asked for it: a run of its base in any scope (core's,
    another candidate's, or the strategy's run core claimed, `_base_under_way`), since a base's
    samples are shared by every candidate compared with it, and a run of the candidate's input
    in the comparison's own scope (`base_in_flight`).
    """
    evaluation = next(
        (
            s.evaluation
            for s in reversed(state.samples)
            if s.scope == comparison.scope
            and s.measured_input == comparison.candidate_input
            and s.base is not None
            and s.base.measured_input == comparison.base_input
        ),
        None,
    )
    if evaluation is None:
        return False
    base = BaseRef(commit=comparison.base, measured_input=comparison.base_input)
    return feeding_run_under_way(
        state, comparison.scope, Measured(evaluation, comparison.candidate_input), base
    )


def feeding_run_under_way(
    state: EvaluationState, scope: Scope, candidate: Measured, base: BaseRef
) -> bool:
    """Whether a run that feeds the scope's comparison of ``candidate`` with ``base`` is under way.

    A run of the base in any scope (core's, another candidate's, or the strategy's run core
    claimed, `_base_under_way`), since a base's samples are shared by every candidate compared
    with it, and a run of the candidate's input in its own scope (`base_in_flight`).
    """
    measured_base = Measured(candidate.evaluation, base.measured_input)
    return _base_under_way(state, base, measured_base) or base_in_flight(state, candidate, scope)


def comparisons_settled(
    before: EvaluationState, after: EvaluationState, moment: Moment
) -> tuple[ComparisonSettled, ...]:
    """Every comparison that was settling in ``before`` and is settled in ``after``.

    Read from the view's own statement of each comparison (`stated_comparisons`), so the report
    and the ``settling`` a flow reads are one test: a flow that read a comparison settling is
    told exactly once, in the step whose run's end leaves no run feeding it (after the runs that
    end started), and never while one still does. A comparison stops settling only when a run
    under way ends (`run_under_way`) or a sample lands, so any other step costs nothing here.
    """
    # Samples are only ever appended.
    if _runs_under_way(before) <= _runs_under_way(after) and len(before.samples) == len(
        after.samples
    ):
        return ()
    settling = {
        _subject(stated.comparison)
        for stated in stated_comparisons(before, moment)
        if stated.settling
    }
    if not settling:
        return ()
    return tuple(
        ComparisonSettled(
            scope=stated.comparison.scope,
            candidate_input=stated.comparison.candidate_input,
            base_input=stated.comparison.base_input,
        )
        for stated in stated_comparisons(after, moment)
        if not stated.settling and _subject(stated.comparison) in settling
    )


def _subject(comparison: Comparison) -> tuple[Scope, str, str]:
    return (comparison.scope, comparison.candidate_input, comparison.base_input)


def _runs_under_way(state: EvaluationState) -> frozenset[RequestId]:
    """The request of every run admitted and not ended (`run_under_way`)."""
    return frozenset(
        receipt.request_id
        for budget in state.submission_budgets
        for receipt in budget.receipts
        if isinstance(receipt, PreparedSubmissionReceipt) and run_under_way(state, receipt)
    )


def stated_comparisons(state: EvaluationState, moment: Moment) -> tuple[StatedComparison, ...]:
    """Every comparison the samples held make now, each with whether it is still settling."""
    return tuple(
        StatedComparison(
            comparison=comparison,
            settling=comparison_settling(state, comparison),
            answer_until=_answer_until(state, comparison),
        )
        for comparison in comparisons_of(state, moment)
    )


def _answer_until(state: EvaluationState, comparison: Comparison) -> Seconds | None:
    """When the scope's latest official answer of the candidate input stops moving, if any."""
    return next(
        (
            job.answer_until
            for job in reversed(state.jobs)
            if job.answer_until is not None
            and job.scope == comparison.scope
            and job.plan.measured_input == comparison.candidate_input
        ),
        None,
    )


def comparisons_to_publish(
    state: EvaluationState,
    scope: Scope,
    covered: Iterable[str],
    told: Iterable[tuple[Comparison, bool]],
    moment: Moment,
) -> tuple[tuple[Comparison, ...], tuple[str, ...]]:
    """The comparisons a resume tells the scope's agent of, and the inputs of those still settling.

    ``covered`` are the candidate inputs the wait's jobs measured; ``told`` is every comparison
    the scope's earlier resumes told of, oldest first, each with whether it was still settling
    then. Of the comparisons the scope holds now (`latest_comparisons`), one is told when its
    input is covered, or when its verdict or whether it is settling differs from what the last
    resume that told of it said (one no resume told of differs). Core's runs land after the
    resume that waited for the candidate, so an agent that resumed on a provisional comparison
    learns where it ended at its next resume, whatever that resume waited for. A comparison is
    settling while a run of either side that will feed it has not ended (`comparison_settling`).
    What a resume tells of is the result and every objective's verdict (`_standing`), so an
    objective that moved to a verdict the agent was not told is told again.
    """
    last: dict[tuple[str, str], tuple[tuple[ComparisonVerdict, ...], bool]] = {}
    for comparison, settling in told:
        last[comparison.candidate_input, comparison.base_input] = (_standing(comparison), settling)
    wanted = frozenset(covered)
    held = (c.candidate_input for c in state.comparisons if c.scope == scope)
    found: list[Comparison] = []
    settling_now: list[str] = []
    for comparison in latest_comparisons(state, scope, held, moment):
        settling = comparison_settling(state, comparison)
        subject = (comparison.candidate_input, comparison.base_input)
        if comparison.candidate_input in wanted or last.get(subject) != (
            _standing(comparison),
            settling,
        ):
            found.append(comparison)
            if settling and comparison.candidate_input not in settling_now:
                settling_now.append(comparison.candidate_input)
    return tuple(found), tuple(settling_now)


def _standing(comparison: Comparison) -> tuple[ComparisonVerdict, ...]:
    """The result of a comparison and of each of its objectives: what a resume tells the agent."""
    return (comparison.verdict, *(item.verdict for item in comparison.objectives))


def effect_verdict(comparison: Comparison, minimum_ratio: float) -> EffectVerdict:
    """What ``comparison`` says of an expected effect: the smallest ratio that justifies a change.

    The expected effect of a plan is a bar on its headline objective (the comparison's first),
    judged by the same statistic as the comparison: ``minimum_ratio`` is the bar over the base
    in the metric's own direction (above one for a ``max`` metric, below one for ``min``; one
    asks for any improvement). The candidate's gain over the base is held against the gain the
    bar asks for, with the headline's own noise and the critical number of noises::

        margin = gain - required gain
        supported     if margin >  critical * noise
        disproven     if margin < -critical * noise
        within_noise  otherwise

    A headline that cannot be judged (a base mean of zero) is ``inconclusive``, whatever the
    bar. With a bar of one this is exactly the headline's own verdict.
    """
    resolution = effect_resolution(comparison)
    if resolution is None:
        return EffectVerdict.INCONCLUSIVE
    headline = comparison.objectives[0]
    sign = 1.0 if headline.direction == "max" else -1.0
    margin = sign * (headline.ratio - minimum_ratio)
    if margin > resolution:
        return EffectVerdict.SUPPORTED
    if margin < -resolution:
        return EffectVerdict.DISPROVEN
    return EffectVerdict.WITHIN_NOISE


def effect_resolution(comparison: Comparison) -> float | None:
    """How far a candidate's ratio must clear a plan's bar for `effect_verdict` to call it.

    The critical number of noises times the headline's noise, in ratio units: a candidate within
    this of its bar is ``within_noise``, so a bar the design cannot expect to clear by this much
    is one the framework cannot call. None when the headline cannot be judged.
    """
    headline = comparison.objectives[0]
    if headline.verdict is ComparisonVerdict.INCONCLUSIVE:
        return None
    return VERDICT_NOISES * headline.noise


def comparison_of_result(
    state: EvaluationState, moment: Moment, scope: Scope, candidates: Iterable[RevisionRef]
) -> Comparison | None:
    """The current comparison of the candidate a finished measurement measured, if it has one.

    The measurement's own run, a re-read of an earlier run, or any other run of the same commit
    in the scope is a sample of one input, so the input is the latest sample's of the scope at
    one of the ``candidates`` (the commits the result's evidence names); None when none was
    sampled or the input has no base's samples.
    """
    wanted = tuple(candidates)
    sample = next(
        (s for s in reversed(state.samples) if s.scope == scope and s.commit in wanted), None
    )
    if sample is None:
        return None
    return comparison_of(state, scope, Measured(sample.evaluation, sample.measured_input), moment)


def close_call(comparison: Comparison) -> bool:
    """Whether the candidate's result is not yet called: every objective is within its noise.

    That is a ``within_noise`` result (`dominance`): no objective is better or worse beyond its
    tolerance, so more samples could still turn it into a dominating result or a regression. A
    result that dominates the base, or one with an objective worse beyond its tolerance, is
    decided and no close call, whatever the remaining objectives would say with more samples; so
    is one that cannot be judged (a zero base mean), which more samples do not change.
    """
    return comparison.verdict is ComparisonVerdict.WITHIN_NOISE


def declares_noise(comparison: Comparison, policy: ComparisonPolicy) -> bool:
    """Whether the task declares the noise of every objective ``comparison`` judged.

    Otherwise some objective's noise is estimated from the samples (`vs_core._dominance`), and
    more samples of either side improve the estimate.
    """
    return all(policy.declares_floor(item.metric) for item in comparison.objectives)


def _base_max_samples(comparison: Comparison, policy: ComparisonPolicy) -> int:
    """The most fresh samples a close call earns a base (module docstring).

    ``BASE_MAX_SAMPLES`` when every objective declares its noise; otherwise
    ``MIN_SAMPLES_WITHOUT_FLOOR``, the fewest that show a spread.
    """
    return BASE_MAX_SAMPLES if declares_noise(comparison, policy) else MIN_SAMPLES_WITHOUT_FLOOR


def run_under_way(state: EvaluationState, receipt: SubmissionReceipt | None) -> bool:
    """Whether the run ``receipt`` admitted has not ended: the one test of a run in flight.

    Its submission has not ended (`receipt_ended`), or the job it named has not, or core is
    still collecting that job's records: its sample, if any, lands only then. Every reader of
    a comparison's runs reads it (`runs_in_flight`, `running_baseline`), and so does the
    report that a comparison settled (`comparisons_settled`). A registered operation's job is
    no run of a comparison's: it gives no sample (`vs_core._base_runs.record_samples`), so
    nothing waits for it to end.
    """
    if not isinstance(receipt, PreparedSubmissionReceipt) or any(
        job.request_id == receipt.request_id for job in state.registered_jobs
    ):
        return False
    return not receipt_ended(receipt) or any(
        job.submission_id == receipt.request_id and (not job.terminal or job.collecting is not None)
        for job in state.jobs
    )


def runs_in_flight(state: EvaluationState, measured: Measured, scope: Scope | None = None) -> int:
    """How many runs of this measurement were admitted and have not ended (``scope``'s, or any)."""
    count = 0
    for budget in state.submission_budgets:
        identity = budget.identity
        if (
            (scope is not None and budget.scope != scope)
            or identity.measured_input != measured.measured_input
            or evaluation_of(identity) != measured.evaluation
        ):
            continue
        if run_under_way(state, budget.receipts[-1] if budget.receipts else None):
            count += 1
    return count


def base_in_flight(state: EvaluationState, measured: Measured, scope: Scope | None = None) -> bool:
    """Whether a run of this measurement was admitted and has not ended (``scope``'s, or any)."""
    return runs_in_flight(state, measured, scope) > 0


def running_baseline(state: EvaluationState, base: BaseRef) -> RequestId | None:
    """The strategy's own run of ``base`` that has not ended, if one is under way.

    The strategy measures the run's input from a plan that names no measured input, so
    `base_in_flight` (which matches on one) does not see it. It is the same revision under the
    same gates and host as an agent's base run would be, so a second run would measure the base
    twice: a wasted job on a contended cluster.
    """
    for budget in state.submission_budgets:
        identity = budget.identity
        latest = budget.receipts[-1] if budget.receipts else None
        if (
            identity.purpose == "baseline"
            and identity.measured_input is None
            and identity.candidate == base.commit
            and isinstance(latest, PreparedSubmissionReceipt)
            and run_under_way(state, latest)
        ):
            return latest.request_id
    return None


def claimed_base(state: EvaluationState, request_id: RequestId) -> BaseRef | None:
    """The base a claimed run of the strategy's is counted as samples of, if it is claimed."""
    claim = next((c for c in state.base_claims if c.request_id == request_id), None)
    return None if claim is None else claim.base


def base_missing(state: EvaluationState, measured: Measured, moment: Moment) -> bool:
    """Whether the base must be measured: no fresh sample of it is held or being taken."""
    held = fresh(samples_of(state, measured), moment)
    return not held and not base_in_flight(state, measured)


def base_run_stalled(
    state: EvaluationState, comparison: Comparison, evaluation: str, moment: Moment
) -> bool:
    """Whether a close call's base run for this comparison landed without growing the base.

    Core records how many fresh samples of the base were held when it asked for a run
    (``CoreRun.fresh_when_asked``); a close call asks only while one is held. A run that landed
    with a sample, yet left no more fresh samples than that, replaced one that went stale while
    it ran, so the base goes stale faster than it runs and a verdict that needs more fresh base
    samples cannot be reached for this candidate: another run of either side would do the same.

    Only the runs core took for this comparison (its scope and candidate input) count. An
    earlier candidate's base runs landed while that candidate's samples were fresh; once they
    went stale they say nothing of how fast the base goes stale, and counting them would stop
    every later candidate of the attempt at one run a side.
    """
    base = Measured(evaluation, comparison.base_input)
    taken = samples_of(state, base)
    landed = {sample.run for sample in taken}
    held = len(fresh(taken, moment))
    return any(
        run.scope == comparison.scope
        and run.candidate_input == comparison.candidate_input
        and run.request_id in landed
        and run.fresh_when_asked >= max(held, 1)
        for run in state.core_runs
    )


def remeasures_base(
    state: EvaluationState, comparison: Comparison, evaluation: str, moment: Moment
) -> bool:
    """Whether a close call is worth one more run of the base: it narrows the noise.

    Only while the base holds fewer than `_base_max_samples` fresh samples, no run of it is under
    way (core's in any scope, or the strategy's own run core claimed), and core's last base run
    in the scope grew its fresh samples (`base_run_stalled`), so a close call is never chased
    with base run after base run.
    """
    base = Measured(evaluation, comparison.base_input)
    held = fresh(samples_of(state, base), moment)
    return (
        close_call(comparison)
        and len(held) < _base_max_samples(comparison, moment.policy)
        and not _base_under_way(
            state, BaseRef(commit=comparison.base, measured_input=comparison.base_input), base
        )
        and not base_run_stalled(state, comparison, evaluation, moment)
    )


def _base_under_way(state: EvaluationState, ref: BaseRef, base: Measured) -> bool:
    """Whether a run of the base has not ended: core's own, or the strategy's run it claimed."""
    claimed = {claim.request_id for claim in state.base_claims}
    running = running_baseline(state, ref)
    return base_in_flight(state, base) or (running is not None and running in claimed)


def remeasures_candidate(
    state: EvaluationState, comparison: Comparison, evaluation: str, moment: Moment
) -> bool:
    """Whether a close call is worth one more run of the candidate's input, in its scope.

    Only while some objective's noise is undeclared, the candidate holds fewer than
    ``MIN_SAMPLES_WITHOUT_FLOOR`` runs and none of its input is running in the scope: its side
    needs that many to show a spread of its own (module docstring), and only core can take them,
    since a repeat of the same content is answered with the evaluation already made. Not once
    the base is known to go stale faster than it runs (`base_run_stalled`): its side cannot get
    there.
    """
    candidate = Measured(evaluation, comparison.candidate_input)
    return (
        not declares_noise(comparison, moment.policy)
        and close_call(comparison)
        and comparison.candidate_samples < MIN_SAMPLES_WITHOUT_FLOOR
        and not base_in_flight(state, candidate, comparison.scope)
        and not base_run_stalled(state, comparison, evaluation, moment)
    )
