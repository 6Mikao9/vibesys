"""Main moves at a candidate's settled trusted measurement, once, whatever order things happen in.

A candidate built on main whose trusted accuracy and benchmark checks passed and whose benchmark
dominates main's (no objective worse, one better, by vs-core's rule) becomes main the moment its
measurement is reported, not when its workstream closes; a candidate that does not dominate, was
built on an older main, failed a check or is not eligible leaves main where it was. The root
takes each measurement once, at its first sight: the report, or the close of the workstream when
the report was lost with a restart. The verdict on it is derived, not taken once
(`test_main_judged_from_facts`).

These properties run the record under generated lineages and generated orders of starts,
measurements and closes, beside a model that states the rule on its own, and crash the root at
any durable boundary: it resumes from its checkpoint and is told again what it may have been told
before, and its record must be the one the uninterrupted run reaches.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from hypothesis import example, given, settings
from hypothesis import strategies as st
from tests.vibesys.orchestration.dynamic._host import TERMS
from tests.vibesys.orchestration.dynamic._values import (
    BASE,
    config,
    evidence_ref,
    reading_of_ref,
    result_of,
    revision,
    settlement_of,
    workstream_start,
)

from vibesys.candidates.api import InputReading, Measured
from vibesys.hypothesis import HypothesisOutcome
from vibesys.metrics import MetricRow
from vibesys.orchestration.dynamic.search.api import (
    CandidateMeasured,
    Closing,
    CoreHolds,
    Evidence,
    Proof,
    RecordState,
    RunRecord,
    WorkKind,
    WorkstreamDone,
    WorkstreamStart,
    measured_by_workstream,
)
from vs_core.api import (
    ComparisonVerdict,
    EvidenceKind,
    Runs,
    Settle,
    TaskObjective,
    dominance,
    judge,
)
from vs_sim.api.testing import examples

if TYPE_CHECKING:
    from vs_core.api import RevisionRef, TaskTerms

AXES = ("tpot_ms_avg", "ttft_ms_avg")
INPUT_READING = (49.6, 29797.0)
TPOTS = (40.0, 49.6, 51.0, 60.0)
TTFTS = (12000.0, 17000.0, 28000.0, 29797.0, 40000.0)
NOISES = (0.0, 0.03, 0.1)


def rows_of(tpot: float, ttft: float) -> tuple[MetricRow, ...]:
    """A benchmark's rows: both objectives are minimised, TPOT the headline."""
    return (
        MetricRow(name=AXES[0], value=tpot, direction="min", unit="ms"),
        MetricRow(name=AXES[1], value=ttft, direction="min", unit="ms"),
    )


def terms_of(noise: float) -> TaskTerms:
    return TERMS.model_copy(
        update={
            "objectives": tuple(TaskObjective(name=name, direction="min") for name in AXES),
            "relative_noise": noise,
        }
    )


@dataclass(frozen=True)
class Work:
    """One workstream the generator plans: what it was built on and what its measurement says."""

    on_main: bool
    """Whether it is started on the main of the moment it starts (else on an older revision)."""
    tpot: float
    ttft: float
    accuracy: bool
    benchmark: bool
    eligible: bool
    reported: bool
    """Whether its report reaches the root (else only the close of the workstream does)."""
    repeated: bool
    """Whether its report is told twice (a restarted workstream sends it again)."""

    @property
    def retained(self) -> bool:
        return self.accuracy

    @property
    def settles_eligible(self) -> bool:
        return self.eligible and self.accuracy and self.benchmark


WORKS = st.builds(
    Work,
    on_main=st.booleans(),
    tpot=st.sampled_from(TPOTS),
    ttft=st.sampled_from(TTFTS),
    accuracy=st.sampled_from([True, True, True, False]),
    benchmark=st.sampled_from([True, True, True, False]),
    eligible=st.sampled_from([True, True, True, False]),
    reported=st.booleans(),
    repeated=st.booleans(),
)


@st.composite
def schedules(draw: st.DrawFn) -> tuple[tuple[Work, ...], tuple[int, ...]]:
    """Workstreams and an order of their steps: each index starts, is measured, then closes."""
    works = tuple(draw(st.lists(WORKS, min_size=1, max_size=5)))
    steps = [index for index in range(len(works)) for _ in range(3)]
    return works, tuple(draw(st.permutations(steps)))


@dataclass
class Model:
    """Where main is, stated from the rule and nothing else of the record."""

    noise: float
    main: RevisionRef = BASE
    reading: tuple[float, float] = INPUT_READING
    parents: dict[int, RevisionRef] = field(default_factory=dict)
    seen: set[int] = field(default_factory=set)
    moves: list[int] = field(default_factory=list)

    def started(self, index: int, work: Work) -> RevisionRef:
        self.parents[index] = self.main if work.on_main else revision("older")
        return self.parents[index]

    def first_sight(self, index: int, work: Work) -> None:
        if index in self.seen:
            return
        self.seen.add(index)
        if self.parents[index] != self.main or not work.settles_eligible:
            return
        verdicts = (
            judge(
                Runs(mean=mine, count=1),
                Runs(mean=theirs, count=1),
                direction="min",
                declared=self.noise,
            ).verdict
            for mine, theirs in zip((work.tpot, work.ttft), self.reading, strict=True)
        )
        if dominance(verdicts) is ComparisonVerdict.BETTER:
            self.main, self.reading = revision(f"c{index}"), (work.tpot, work.ttft)
            self.moves.append(index)


def candidate_of(index: int) -> RevisionRef:
    return revision(f"c{index}")


def evidence_for(index: int, work: Work) -> Evidence:
    candidate = candidate_of(index)
    accuracy = evidence_ref(f"accuracy-{index}", EvidenceKind.CORRECTNESS, candidate)
    benchmark = evidence_ref(f"benchmark-{index}", EvidenceKind.BENCHMARK, candidate)
    return Evidence(
        readings=(
            reading_of_ref(accuracy, passed=work.accuracy),
            reading_of_ref(benchmark, passed=work.benchmark, metrics=rows_of(work.tpot, work.ttft)),
        ),
        proof=Proof(refs=(accuracy, benchmark)),
    )


def start_of(index: int, parent: RevisionRef) -> WorkstreamStart:
    return workstream_start(WorkKind.IMPLEMENT, index + 1, f"h{index}").model_copy(
        update={"parent": parent}
    )


def report_of(index: int, work: Work, start: WorkstreamStart) -> CandidateMeasured | None:
    """What the workstream reports once it asserted its settlement; None when it kept nothing."""
    candidate = candidate_of(index)
    settle = Settle(
        assessments=(),
        eligible=work.settles_eligible,
        retention="candidate" if work.retained else "discard",
        outcome="succeeded",
        candidate=candidate if work.retained else None,
    )
    closing = Closing(
        result=result_of(HypothesisOutcome.SUPPORTED, review=True, revision_=candidate),
        evidence=evidence_for(index, work),
    )
    return measured_by_workstream(start, closing, settle, terms_of(0.0))


def done_of(index: int, work: Work, start: WorkstreamStart) -> WorkstreamDone:
    candidate = candidate_of(index)
    return WorkstreamDone(
        start=start,
        failure=None,
        generation=3,
        settlement=settlement_of(
            eligible=work.settles_eligible,
            retention="candidate" if work.retained else "discard",
            candidate=candidate if work.retained else None,
            name=str(index),
        ),
        result=result_of(HypothesisOutcome.SUPPORTED, review=True, revision_=candidate),
        evidence=evidence_for(index, work),
    )


def fresh_record(noise: float) -> RunRecord:
    baseline = Measured(
        reading=InputReading(
            accuracy_passed=True, benchmark_passed=True, metrics=rows_of(*INPUT_READING)
        )
    )
    selected = config()
    return RunRecord(selected, BASE, baseline, terms_of(noise), admissions=selected.start_budget)


@dataclass
class Driver:
    """A record and the model it is checked against, told one step of one workstream at a time."""

    works: tuple[Work, ...]
    noise: float
    record: RunRecord = field(init=False)
    model: Model = field(init=False)
    starts: dict[int, WorkstreamStart] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.record, self.model = fresh_record(self.noise), Model(self.noise)

    def tell(self, index: int, step: int) -> None:
        """Tell the record step ``step`` of workstream ``index``: it starts, is measured, closes."""
        work = self.works[index]
        if step == 0:
            start = self.starts.get(index) or start_of(index, self.model.started(index, work))
            self.starts[index] = start
            self.record.running[start.key] = start
        elif step == 1:
            report = report_of(index, work, self.starts[index])
            if work.reported and report is not None:
                self.record.candidate_measured(report)
                self.model.first_sight(index, work)
                if work.repeated:
                    self.record.candidate_measured(report)
        else:
            self.record.finished(done_of(index, work, self.starts[index]))
            self.model.first_sight(index, work)

    def crash(self, state: RecordState) -> None:
        """The root dies and a restart resumes it from ``state``, through the bytes it was written as."""
        self.record = RunRecord.resumed(
            self.record.config,
            BASE,
            terms_of(self.noise),
            RecordState.model_validate_json(state.model_dump_json()),
            CoreHolds(
                self.record.budget, tuple(item.settlement for item in self.record.kept.values())
            ),
        )


def play(
    works: tuple[Work, ...],
    order: tuple[int, ...],
    noise: float,
    *,
    crashes: frozenset[int] = frozenset(),
    redo: bool = False,
) -> Driver:
    """Run ``order`` against a record and a model, checking main after every step.

    A step at a position in ``crashes`` is followed by a crash of the root: it resumes from the
    checkpoint taken just before the step (and is told the step again, ``redo``) or just after it.
    """
    driver = Driver(works, noise)
    taken: dict[int, int] = {}
    for position, index in enumerate(order):
        step = taken.get(index, 0)
        taken[index] = step + 1
        before = driver.record.state()
        driver.tell(index, step)
        if position in crashes:
            driver.crash(before if redo else driver.record.state())
            if redo:
                # told the step again; the model judged it already, and a second sight leaves it be
                driver.tell(index, step)
        assert driver.record.main.revision == driver.model.main, (position, order, works)
        assert driver.record.main.generation == len(driver.model.moves)
    return driver


@settings(max_examples=examples(pr=60, nightly=2000), deadline=None)
@given(case=schedules(), noise=st.sampled_from(NOISES))
@example(
    case=(
        (
            Work(
                on_main=True,
                tpot=49.6,
                ttft=17000.0,
                accuracy=True,
                benchmark=True,
                eligible=True,
                reported=True,
                repeated=False,
            ),
            Work(
                on_main=True,
                tpot=49.6,
                ttft=12000.0,
                accuracy=True,
                benchmark=True,
                eligible=True,
                reported=True,
                repeated=False,
            ),
        ),
        (0, 1, 0, 1, 0, 1),
    ),
    noise=0.03,
)
def test_main_moves_exactly_at_the_first_measurement_that_dominates_it_and_never_otherwise(
    case: tuple[tuple[Work, ...], tuple[int, ...]], noise: float
) -> None:
    """The lineage, the order and the verdicts are drawn; the model restates the rule on its own."""
    works, order = case
    driver = play(works, order, noise)

    assert [item.merge_id for item in driver.record.main.merges] == [
        f"h{i}" for i in driver.model.moves
    ]
    assert all(item.fast_forward for item in driver.record.main.merges)
    # every workstream was judged exactly once, at its first sight
    assert driver.model.seen == set(range(len(works)))


@settings(max_examples=examples(pr=60, nightly=2000), deadline=None)
@given(
    case=schedules(),
    noise=st.sampled_from(NOISES),
    crashes=st.sets(st.integers(0, 14), max_size=4),
    redo=st.booleans(),
)
def test_a_crash_at_any_boundary_recovers_the_main_and_the_record_the_run_reaches_without_it(
    case: tuple[tuple[Work, ...], tuple[int, ...]],
    noise: float,
    crashes: set[int],
    *,
    redo: bool,
) -> None:
    """The root resumes from its checkpoint and may be told a step again: nothing moves twice."""
    works, order = case
    plain = play(works, order, noise).record
    crashed = play(works, order, noise, crashes=frozenset(crashes), redo=redo).record

    assert crashed.state() == plain.state()
    assert crashed.main.revision == plain.main.revision


def test_a_measurement_that_did_not_dominate_the_bar_dominates_once_the_bar_moves() -> None:
    """The verdict is derived from the facts: a bar that later reads far higher makes it a gain.

    The candidate's TTFT is within noise of the input's when its measurement lands, so main stays.
    The input's bar later rests on more samples and reads far higher: the same measurement now
    dominates it, and main moves to the candidate without anything being measured again.
    """
    work = Work(
        on_main=True,
        tpot=49.6,
        ttft=28000.0,
        accuracy=True,
        benchmark=True,
        eligible=False,
        reported=True,
        repeated=False,
    )
    record = fresh_record(0.0)
    start = start_of(0, BASE)
    record.running[start.key] = start

    report = report_of(0, work, start)
    assert report is not None
    record.candidate_measured(report)
    assert record.main.revision == BASE

    record.refined(
        Measured(
            reading=InputReading(
                accuracy_passed=True,
                benchmark_passed=True,
                metrics=rows_of(49.6, 60000.0),
                samples=3,
            )
        )
    )
    assert record.main.revision == candidate_of(0)
    record.finished(done_of(0, work, start))

    assert record.main.revision == candidate_of(0)
    assert [item.merge_id for item in record.main.merges] == ["h0"]
