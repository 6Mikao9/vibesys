"""Every place the product decides better, worse or within noise reads one rule: vs-core's.

The sites are the settlement of a candidate against the input (serial and dynamic searches alike),
the fast-forward of main, the soundness of a merge, the headline against the input, the composite
against its parts and the frontier. Each is driven through its public interface with generated
rows and a generated task (declared noises, runs and spreads), and the answer it gives is held to
what `vs_core.api.judge` and `dominance` say of the same rows. There is no product statistic
left to disagree with: a site that decided on its own would fail here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hypothesis import example, given, settings
from hypothesis import strategies as st
from tests.vibesys.orchestration.dynamic._host import TERMS
from tests.vibesys.orchestration.dynamic._values import (
    BASE,
    evidence_ref,
    reading_of_ref,
    revision,
    settlement_of,
    workstream_start,
)

from vibesys.candidates.api import (
    InputReading,
    Measured,
    beats_input,
    improves_on,
    measurement,
    space_of,
)
from vibesys.metrics import AcceptedReading, MetricRow
from vibesys.orchestration.dynamic.search.api import (
    CandidateMeasured,
    CombineSource,
    Evidence,
    MainLine,
    MergeOutcome,
    MergeReport,
    PartsVerdict,
    Proof,
    WorkKind,
    WorkstreamDone,
    best_part,
    versus_parts,
)
from vs_core.api import (
    ComparisonVerdict,
    EvidenceKind,
    Runs,
    TaskObjective,
    dominance,
    judge,
)
from vs_core.api.values import ObjectiveNoise
from vs_sim.api.testing import examples

if TYPE_CHECKING:
    from vibesys.metrics import Direction
    from vs_core.api import TaskTerms

_NAMES = ("tpot_ms_avg", "ttft_ms_avg", "completed_requests")
_VALUES = st.sampled_from((20.0, 40.0, 49.6, 52.0, 60.0, 100.0, 150.0, 300.0))
_SPREADS = st.sampled_from((0.0, 0.0, 0.02, 0.1))


def _noise(relative: float) -> ObjectiveNoise:
    return ObjectiveNoise(floor=relative, relative_noise=relative, provenance="test")


@st.composite
def tasks(draw: st.DrawFn) -> TaskTerms:
    """A task of one to three objectives, each declaring its noise or none, and a task noise."""
    names = draw(st.lists(st.sampled_from(_NAMES), min_size=1, max_size=3, unique=True))
    return TERMS.model_copy(
        update={
            "objectives": tuple(
                TaskObjective(
                    name=name,
                    direction=draw(st.sampled_from(("max", "min"))),
                    noise=draw(st.none() | st.sampled_from((0.03, 0.08)).map(_noise)),
                )
                for name in names
            ),
            "relative_noise": draw(st.sampled_from((0.0, 0.02, 0.1))),
        }
    )


@st.composite
def sides(draw: st.DrawFn, terms: TaskTerms) -> tuple[MetricRow, ...]:
    """One side's rows over the task's objectives: a value, the runs it rests on, their spread."""
    rows = []
    for objective in terms.objectives:
        runs = draw(st.integers(min_value=1, max_value=4))
        rows.append(
            MetricRow(
                name=objective.name,
                value=draw(_VALUES),
                direction=objective.direction,
                runs=runs,
                spread=draw(_SPREADS) if runs > 1 else 0.0,
            )
        )
    return tuple(rows)


def the_rule(
    terms: TaskTerms, mine: tuple[MetricRow, ...], theirs: tuple[MetricRow, ...]
) -> ComparisonVerdict:
    """What vs-core says of ``mine`` against ``theirs``, from the task's declared noise alone."""
    verdicts = []
    for objective, candidate, base in zip(terms.objectives, mine, theirs, strict=True):
        declared = (
            objective.noise.relative_noise if objective.noise is not None else terms.relative_noise
        )
        verdicts.append(
            judge(
                Runs(mean=candidate.value, count=candidate.runs, spread=candidate.spread),
                Runs(mean=base.value, count=base.runs, spread=base.spread),
                direction=objective.direction,
                declared=declared,
            ).verdict
        )
    return dominance(verdicts)


def _benchmark(name: str, rows: tuple[MetricRow, ...], *, passed: bool = True) -> AcceptedReading:
    ref = evidence_ref(f"benchmark-{name}", EvidenceKind.BENCHMARK, revision(name))
    return reading_of_ref(ref, passed=passed, metrics=rows)


def _accuracy(name: str, *, passed: bool = True) -> AcceptedReading:
    ref = evidence_ref(f"accuracy-{name}", EvidenceKind.CORRECTNESS, revision(name))
    return reading_of_ref(ref, passed=passed)


def _input_of(rows: tuple[MetricRow, ...]) -> Measured:
    return Measured(reading=InputReading(accuracy_passed=True, benchmark_passed=True, metrics=rows))


@st.composite
def cases(
    draw: st.DrawFn,
) -> tuple[TaskTerms, tuple[MetricRow, ...], tuple[MetricRow, ...]]:
    """A task, a candidate's rows and the base's rows on its objectives."""
    terms = draw(tasks())
    return terms, draw(sides(terms)), draw(sides(terms))


@settings(max_examples=examples(pr=80, nightly=2000), deadline=None)
@given(case=cases())
@example(
    case=(
        TERMS.model_copy(
            update={
                "objectives": (
                    TaskObjective(name="tpot_ms_avg", direction="min"),
                    TaskObjective(name="ttft_ms_avg", direction="min"),
                ),
                "relative_noise": 0.0,
            }
        ),
        (
            MetricRow(name="tpot_ms_avg", value=49.6, direction="min"),
            MetricRow(name="ttft_ms_avg", value=17000.0, direction="min"),
        ),
        (
            MetricRow(name="tpot_ms_avg", value=49.6, direction="min"),
            MetricRow(name="ttft_ms_avg", value=29797.0, direction="min"),
        ),
    )
)
def test_every_site_that_decides_better_reads_the_one_rule(
    case: tuple[TaskTerms, tuple[MetricRow, ...], tuple[MetricRow, ...]],
) -> None:
    terms, mine, theirs = case
    expected = the_rule(terms, mine, theirs)
    better = expected is ComparisonVerdict.BETTER
    space = space_of(terms)

    # settlement against the input, which the serial and the dynamic searches share
    assert beats_input(_input_of(theirs), (_benchmark("c", mine),), terms) is better

    # the fast-forward of main to a candidate built on it
    start = workstream_start(WorkKind.IMPLEMENT, 1, "h1").model_copy(update={"parent": BASE})
    line = MainLine(revision=BASE, baseline=_input_of(theirs))
    line.measured(
        start,
        CandidateMeasured(
            key=start.key,
            candidate=revision("c"),
            eligible=True,
            accuracy=_accuracy("c"),
            benchmark=_benchmark("c", mine),
        ),
        terms,
    )
    assert (line.revision == revision("c")) is better

    # the soundness of a merge: no objective worse, every one judged
    merging = workstream_start(WorkKind.MERGE, 2, "m1").model_copy(update={"parent": BASE})
    merge = MainLine(revision=BASE, baseline=_input_of(theirs))
    done = WorkstreamDone(
        start=merging,
        failure=None,
        settlement=settlement_of(eligible=True, retention="candidate", candidate=revision("m")),
        evidence=Evidence(
            readings=(_accuracy("m"), _benchmark("m", mine)),
            proof=Proof(refs=()),
        ),
        merge=MergeReport(outcome=MergeOutcome.MERGED, revision=revision("m")),
    )
    sound = merge.finished(done, terms).outcome is MergeOutcome.MERGED
    assert sound is (expected in {ComparisonVerdict.BETTER, ComparisonVerdict.WITHIN_NOISE})

    # the frontier and the dominance of one row over another
    assert space.dominates(
        {row.name: row.value for row in mine}, {row.name: row.value for row in theirs}
    ) is (
        the_rule(
            terms,
            tuple(row.model_copy(update={"runs": 1, "spread": 0.0}) for row in mine),
            tuple(row.model_copy(update={"runs": 1, "spread": 0.0}) for row in theirs),
        )
        is ComparisonVerdict.BETTER
    )


@settings(max_examples=examples(pr=60, nightly=1000), deadline=None)
@given(
    mine=_VALUES,
    base=_VALUES,
    direction=st.sampled_from(("max", "min")),
    declared=st.sampled_from((0.0, 0.03, 0.1)),
)
def test_the_headline_against_the_input_and_the_composite_against_its_part_read_the_rule(
    mine: float, base: float, direction: Direction, declared: float
) -> None:
    terms = TERMS.model_copy(
        update={
            "objectives": (TaskObjective(name="throughput", direction=direction),),
            "relative_noise": declared,
        }
    )
    space = space_of(terms)
    row = MetricRow(name="throughput", value=mine, direction=direction)
    against = MetricRow(name="throughput", value=base, direction=direction)
    expected = judge(
        Runs(mean=mine, count=1),
        Runs(mean=base, count=1),
        direction=direction,
        declared=declared,
    ).verdict

    assert improves_on(
        measurement(row), measurement(against), space=space, when_input_unmeasured=False
    ) is (expected is ComparisonVerdict.BETTER)

    part = CombineSource(hypothesis_id="p", revision=revision("p"), built_on=BASE, metric=against)
    assert best_part((part,), space) is part
    got = versus_parts((row,), (part,), space)
    assert (
        got
        is {
            ComparisonVerdict.BETTER: PartsVerdict.BETTER,
            ComparisonVerdict.WORSE: PartsVerdict.WORSE,
            ComparisonVerdict.WITHIN_NOISE: PartsVerdict.WITHIN_NOISE,
            ComparisonVerdict.INCONCLUSIVE: PartsVerdict.UNKNOWN,
        }[expected]
    )
