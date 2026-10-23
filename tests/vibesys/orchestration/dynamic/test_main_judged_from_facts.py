"""Main's verdict on a candidate is derived from the facts, not frozen at the first sight of them.

A candidate that holds main and dominates what main reads becomes main; what main reads is the
input's bar until a candidate moves it, and the bar changes as the ledger takes more samples of
the input, and as a sample reports an objective the earlier ones did not. So the verdict is a
pure function of the candidate's recorded measurement, the revision it was built on, and the bar
now: a measurement that could not be judged on an objective the bar lacked (inconclusive, never
dropped) moves main as soon as the bar reports it, whatever arrived first, and nothing is
measured again.

The shape that went wrong in a live run: a candidate with TTFT and TPOT, a bar with TPOT only,
and a later sample of the input that reports TTFT. These tests run it exactly, then generate
objective sets and arrival orders beside a model that restates the rule, crash the root at any
boundary, and resume checkpoints from before the measured candidates were kept.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from hypothesis import example, given, settings
from hypothesis import strategies as st
from tests.vibesys.orchestration.dynamic._host import TERMS
from tests.vibesys.orchestration.dynamic._values import (
    BASE,
    config,
    evidence_ref,
    hypothesis_record,
    reading_of_ref,
    result_of,
    revision,
    settlement_of,
    workstream_start,
)

from vibesys.candidates.api import Baseline, InputReading, Measured
from vibesys.hypothesis import HypothesisOutcome
from vibesys.metrics import MetricRow, mean_metrics
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
    assertion,
    measured_by_workstream,
)
from vs_core.api import (
    ComparisonVerdict,
    EvidenceKind,
    Runs,
    TaskObjective,
    dominance,
    judge,
)
from vs_sim.api.testing import examples

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from vs_core.api import RevisionRef, TaskTerms

type Way = Literal["min", "max"]
AXES: tuple[tuple[str, Way], ...] = (
    ("tpot_ms_avg", "min"),
    ("ttft_ms_avg", "min"),
    ("completed_requests", "max"),
)
BASE_READING: Mapping[str, float] = {
    "tpot_ms_avg": 50.0,
    "ttft_ms_avg": 30000.0,
    "completed_requests": 2.0,
}
NOISE = 0.05


def terms_of(noise: float = NOISE) -> TaskTerms:
    return TERMS.model_copy(
        update={
            "objectives": tuple(TaskObjective(name=name, direction=way) for name, way in AXES),
            "relative_noise": noise,
        }
    )


def rows_of(reading: Mapping[str, float]) -> tuple[MetricRow, ...]:
    """The rows of a benchmark that reports the axes ``reading`` holds, in the axes' order."""
    return tuple(
        MetricRow(name=name, value=reading[name], direction=way, unit=None)
        for name, way in AXES
        if name in reading
    )


def bar_of(samples: Sequence[Mapping[str, float]]) -> Measured:
    """The input as the ledger states it after ``samples``: the mean per axis of those reporting it."""
    return Measured(
        reading=InputReading(
            accuracy_passed=True,
            benchmark_passed=True,
            metrics=mean_metrics([rows_of(sample) for sample in samples]),
            samples=len(samples),
        )
    )


def reading_with(factors: Mapping[str, float]) -> dict[str, float]:
    """A candidate's reading: the input's, each axis scaled by its factor."""
    return {name: BASE_READING[name] * factors.get(name, 1.0) for name, _ in AXES}


def evidence_for(index: int, reading: Mapping[str, float]) -> Evidence:
    candidate = revision(f"c{index}")
    accuracy = evidence_ref(f"accuracy-{index}", EvidenceKind.CORRECTNESS, candidate)
    benchmark = evidence_ref(f"benchmark-{index}", EvidenceKind.BENCHMARK, candidate)
    return Evidence(
        readings=(
            reading_of_ref(accuracy, passed=True),
            reading_of_ref(benchmark, passed=True, metrics=rows_of(reading)),
        ),
        proof=Proof(refs=(accuracy, benchmark)),
    )


@dataclass(frozen=True)
class Built:
    """A candidate the generator plans: what it was built on and what its benchmark read."""

    parent: int | None
    """The index of the candidate it is built on; None: the run's input."""
    reading: Mapping[str, float]
    sound: bool = True


def start_of(index: int, built: Built) -> WorkstreamStart:
    parent = BASE if built.parent is None else revision(f"c{built.parent}")
    return workstream_start(WorkKind.IMPLEMENT, index + 1, f"h{index}").model_copy(
        update={"parent": parent}
    )


def report_of(
    index: int, built: Built, start: WorkstreamStart, bar: Baseline | None
) -> CandidateMeasured | None:
    """What the workstream reports when its measurement lands: the settlement it asserted.

    The settlement is the one the search asserts against the input as it stands then (``bar``):
    eligible only when the candidate beats that bar, a verdict taken once and never revisited.
    What is not frozen is whether the candidate is sound.
    """
    candidate = revision(f"c{index}")
    outcome = HypothesisOutcome.SUPPORTED if built.sound else HypothesisOutcome.BLOCKED
    closing = Closing(
        result=result_of(outcome, review=True, revision_=candidate),
        evidence=evidence_for(index, built.reading),
        input=bar,
    )
    settle = assertion(start, closing, terms_of(), bar)
    return measured_by_workstream(start, closing, settle, terms_of())


def done_of(index: int, built: Built, start: WorkstreamStart) -> WorkstreamDone:
    candidate = revision(f"c{index}")
    outcome = HypothesisOutcome.SUPPORTED if built.sound else HypothesisOutcome.BLOCKED
    return WorkstreamDone(
        start=start,
        failure=None,
        generation=3,
        settlement=settlement_of(
            eligible=False, retention="candidate", candidate=candidate, name=str(index)
        ),
        result=result_of(outcome, review=True, revision_=candidate),
        evidence=evidence_for(index, built.reading),
    )


def fresh_record(first: Mapping[str, float]) -> RunRecord:
    selected = config()
    return RunRecord(selected, BASE, bar_of([first]), terms_of(), admissions=selected.start_budget)


def resumed(record: RunRecord, state: RecordState) -> RunRecord:
    """The record a restart resumes from ``state``, through the bytes it was written as."""
    return RunRecord.resumed(
        record.config,
        BASE,
        terms_of(),
        RecordState.model_validate_json(state.model_dump_json()),
        CoreHolds(record.budget, tuple(item.settlement for item in record.kept.values())),
    )


# -- the exact shape ------------------------------------------------------------------------------


def test_a_candidate_the_bar_could_not_judge_on_ttft_moves_main_when_the_bar_reports_it() -> None:
    """The live run's shape, replayed: TPOT within noise, TTFT 40% better, the bar has no TTFT.

    The candidate's TTFT cannot be judged against a bar that does not report it, so main stays
    (the verdict is inconclusive, not a win and not silently dropped). A later sample of the input
    reports TTFT: the same recorded measurement now dominates, and main moves to the candidate
    without the candidate being measured again. Closing the workstream afterwards changes nothing.
    """
    tpot_only = {"tpot_ms_avg": 53.4, "completed_requests": 2.0}
    candidate = Built(
        parent=None,
        reading={"tpot_ms_avg": 52.9, "ttft_ms_avg": 19845.0, "completed_requests": 2.0},
    )
    record = fresh_record(tpot_only)
    start = start_of(0, candidate)
    record.running[start.key] = start

    report = report_of(0, candidate, start, record.baseline)
    assert report is not None
    assert report.eligible is False  # the settlement's own verdict on the bar it saw, frozen
    assert report.sound is True
    record.candidate_measured(report)
    assert record.main.revision == BASE

    record.refined(bar_of([tpot_only, {**tpot_only, "ttft_ms_avg": 35000.0}]))
    assert record.main.revision == revision("c0")
    assert [item.merge_id for item in record.main.merges] == ["h0"]
    assert record.main.merges[0].fast_forward

    record.finished(done_of(0, candidate, start))
    assert record.main.revision == revision("c0")
    assert record.main.generation == 1


def test_a_candidate_that_is_not_sound_never_moves_main_whatever_the_bar_reports() -> None:
    tpot_only = {"tpot_ms_avg": 53.4, "completed_requests": 2.0}
    candidate = Built(
        parent=None,
        reading={"tpot_ms_avg": 52.9, "ttft_ms_avg": 19845.0, "completed_requests": 2.0},
        sound=False,
    )
    record = fresh_record(tpot_only)
    start = start_of(0, candidate)
    record.running[start.key] = start
    report = report_of(0, candidate, start, record.baseline)
    assert report is not None
    record.candidate_measured(report)

    record.refined(bar_of([tpot_only, {**tpot_only, "ttft_ms_avg": 35000.0}]))

    assert record.main.revision == BASE


# -- the class: any objective sets, any arrival order -------------------------------------------------

GAPS = {"better": 0.5, "same": 1.0, "worse": 2.0}
"""How far a candidate reads from the input on an axis: far enough that no run count changes the
verdict (the noise of one run a side is the widest), or exactly equal."""


def scaled(axis: str, gap: str) -> float:
    """The factor a candidate reads on ``axis`` for ``gap``: below 1 is better on a minimised axis."""
    way = dict(AXES)[axis]
    factor = GAPS[gap]
    return factor if way == "min" else 1 / factor


@st.composite
def candidates_of(draw: st.DrawFn, count: int) -> tuple[Built, ...]:
    """Candidates whose axes each read better, equal or worse than the input, built on the input
    or on an earlier candidate (so a move of main can open the way for the next)."""
    built: list[Built] = []
    for index in range(count):
        gaps = {name: draw(st.sampled_from(sorted(GAPS))) for name, _ in AXES}
        parent = draw(st.one_of(st.none(), st.integers(0, index - 1))) if index else None
        built.append(
            Built(
                parent=parent,
                reading=reading_with({name: scaled(name, gap) for name, gap in gaps.items()}),
                sound=draw(st.sampled_from([True, True, True, False])),
            )
        )
    return tuple(built)


@st.composite
def worlds(
    draw: st.DrawFn,
) -> tuple[tuple[tuple[str, ...], ...], tuple[Built, ...], tuple[str, ...]]:
    """The axes each sample of the input reports, the candidates, and the order things arrive in.

    Each sample reports a non-empty set of axes (a benchmark from before an objective was
    declared reports fewer). The arrivals are the later samples (``"b"``, in order) and the
    candidates' measurements (their index), interleaved at random.
    """
    names = [name for name, _ in AXES]
    samples = tuple(
        tuple(draw(st.lists(st.sampled_from(names), min_size=1, unique=True)))
        for _ in range(draw(st.integers(1, 4)))
    )
    built = draw(candidates_of(draw(st.integers(1, 4))))
    arrivals = [*("b" for _ in samples[1:]), *(str(index) for index in range(len(built)))]
    return samples, built, tuple(draw(st.permutations(arrivals)))


def sample_reading(axes: Sequence[str]) -> dict[str, float]:
    return {name: BASE_READING[name] for name in axes}


def dominates(mine: Mapping[str, float], theirs: Mapping[str, float]) -> bool:
    """The rule, restated: every axis judged, a missing one inconclusive, then dominance."""
    verdicts = [
        judge(
            Runs(mean=mine[name], count=1),
            Runs(mean=theirs[name], count=1),
            direction=way,
            declared=NOISE,
        ).verdict
        if name in mine and name in theirs
        else ComparisonVerdict.INCONCLUSIVE
        for name, way in AXES
    ]
    return dominance(verdicts) is ComparisonVerdict.BETTER


def final_main(
    samples: Sequence[Sequence[str]], built: Sequence[Built], order: Sequence[int]
) -> RevisionRef:
    """Where main ends, from the facts alone: the final bar, the candidates in arrival order.

    The first candidate that holds main and dominates it becomes main, and so on until none does.
    """
    reported = {name for axes in samples for name in axes}
    main, reading = BASE, {name: BASE_READING[name] for name in reported}
    while True:
        for index in order:
            candidate = built[index]
            parent = BASE if candidate.parent is None else revision(f"c{candidate.parent}")
            if candidate.sound and parent == main and dominates(candidate.reading, reading):
                main, reading = revision(f"c{index}"), candidate.reading
                break
        else:
            return main


@dataclass
class Driver:
    """A record told the arrivals of a world, one at a time, in the order drawn."""

    samples: tuple[tuple[str, ...], ...]
    built: tuple[Built, ...]
    record: RunRecord = field(init=False)
    starts: dict[int, WorkstreamStart] = field(default_factory=dict)
    taken: int = 1
    landed: list[int] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.record = fresh_record(sample_reading(self.samples[0]))

    def bars(self) -> list[dict[str, float]]:
        """The samples of the input the ledger holds now."""
        return [sample_reading(axes) for axes in self.samples[: self.taken]]

    def arrive(self, what: str) -> None:
        if what == "b":
            self.taken += 1
            self.record.refined(bar_of(self.bars()))
            return
        index = int(what)
        start = start_of(index, self.built[index])
        self.starts[index] = start
        self.record.running[start.key] = start
        report = report_of(index, self.built[index], start, bar_of(self.bars()))
        assert report is not None
        self.record.candidate_measured(report)
        self.landed.append(index)

    def close_all(self) -> None:
        for index in self.landed:
            self.record.finished(done_of(index, self.built[index], self.starts[index]))

    def crash(self) -> None:
        self.record = resumed(self.record, self.record.state())


@settings(max_examples=examples(pr=80, nightly=3000), deadline=None)
@given(world=worlds(), closes=st.booleans())
@example(
    world=(
        (
            ("tpot_ms_avg", "completed_requests"),
            ("tpot_ms_avg", "ttft_ms_avg", "completed_requests"),
        ),
        (Built(parent=None, reading=reading_with({"ttft_ms_avg": 0.5})),),
        ("0", "b"),
    ),
    closes=False,
)
def test_main_ends_where_the_facts_put_it_whatever_order_the_bar_and_the_candidates_arrive_in(
    world: tuple[tuple[tuple[str, ...], ...], tuple[Built, ...], tuple[str, ...]], *, closes: bool
) -> None:
    """The base's samples (each reporting some objectives) and the candidates arrive in any order.

    A candidate is judged on an objective only once the bar reports it; the verdict is taken again
    whenever the bar moves. So main ends where the final bar and the candidates, in the order they
    landed, put it, however the arrivals interleaved.
    """
    samples, built, arrivals = world
    driver = Driver(samples, built)
    for what in arrivals:
        driver.arrive(what)
    if closes:
        driver.close_all()

    expected = final_main(samples, built, driver.landed)
    assert driver.record.main.revision == expected, (arrivals, samples)
    # the moves are the chain of candidates that each held the main before
    assert driver.record.main.generation == len(driver.record.main.merges)
    assert all(item.fast_forward for item in driver.record.main.merges)


@settings(max_examples=examples(pr=60, nightly=2000), deadline=None)
@given(world=worlds(), crashes=st.sets(st.integers(0, 8), max_size=4))
def test_a_crash_at_any_arrival_resumes_the_record_the_uninterrupted_run_reaches(
    world: tuple[tuple[tuple[str, ...], ...], tuple[Built, ...], tuple[str, ...]],
    crashes: set[int],
) -> None:
    """The root resumes from its checkpoint after any arrival: the record is the same one."""
    samples, built, arrivals = world
    plain = Driver(samples, built)
    crashed = Driver(samples, built)
    for position, what in enumerate(arrivals):
        plain.arrive(what)
        crashed.arrive(what)
        if position in crashes:
            crashed.crash()

    assert crashed.record.state() == plain.record.state()
    assert crashed.record.main.revision == plain.record.main.revision


# -- checkpoints from before the measured candidates were kept ---------------------------------------


def test_a_checkpoint_from_before_the_candidates_were_kept_is_judged_from_its_rounds_on_resume() -> (
    None
):
    """An existing run replays and gets the verdicts it was denied, without a hand edit.

    A run saved by the build that judged each measurement once holds the candidate's finished
    round and a bar that has since grown, with main still at the input. On resume the record
    derives the measured candidates from the rounds (``awaiting`` is None in such a checkpoint)
    and judges main from them: it moves to the candidate. Saving that and resuming again is a
    fixed point.
    """
    tpot_only = {"tpot_ms_avg": 53.4, "completed_requests": 2.0}
    candidate = Built(
        parent=None,
        reading={"tpot_ms_avg": 52.9, "ttft_ms_avg": 19845.0, "completed_requests": 2.0},
    )
    record = fresh_record(tpot_only)
    record.hypotheses["h0"] = hypothesis_record("h0")
    start = start_of(0, candidate)
    record.running[start.key] = start
    report = report_of(0, candidate, start, record.baseline)
    assert report is not None
    record.candidate_measured(report)
    record.finished(done_of(0, candidate, start))
    assert record.main.revision == BASE
    assert [item.sequence for item in record.hypotheses["h0"].rounds] == [1]

    saved = record.state()
    legacy = saved.model_copy(
        update={
            "baseline": bar_of([tpot_only, {**tpot_only, "ttft_ms_avg": 35000.0}]),
            "main": saved.main.model_copy(update={"awaiting": None}),
        }
    )
    assert legacy.main.awaiting is None

    again = resumed(record, legacy)
    assert again.main.revision == revision("c0")
    assert [item.merge_id for item in again.main.merges] == ["h0"]

    twice = resumed(again, again.state())
    assert twice.state() == again.state()
