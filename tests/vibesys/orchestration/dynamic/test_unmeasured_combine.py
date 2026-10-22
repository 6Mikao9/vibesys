"""A combine may name any implemented revision, measured or not.

Facts stay on the revision that recorded them. The combination is a new revision and starts
with none of its parts' facts. Main moves only when that same revision has both trusted facts
and its benchmark is no worse than main's.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pytest
from hypothesis import assume, example, given, settings
from hypothesis import strategies as st
from tests.vibesys.orchestration.dynamic._host import TERMS
from tests.vibesys.orchestration.dynamic._values import (
    BASE,
    combine_entry,
    evidence_of,
    hypothesis_record,
    portfolio,
    revision,
    run_record,
    settlement_of,
    snapshot,
    workstream_start,
)

from vibesys.hypothesis import HypothesisOutcome
from vibesys.metrics import MetricRow
from vibesys.orchestration.dynamic.search.api import (
    MainLine,
    MainMeasurement,
    MergeOutcome,
    MergeReport,
    RoundRecord,
    RunRecord,
    Scheduled,
    Submission,
    WorkKind,
    WorkstreamDone,
    validate,
)
from vs_core.api.search import AttemptId, RevisionRef
from vs_sim.api.testing import examples

_MAIN = 100
"""Main's headline throughput. A combination at this value is not worse."""
_KIND = Literal["submitted", "round", "parent"]


@dataclass(frozen=True)
class _Part:
    """One implemented revision: waiting, finished with its own facts, or an offered parent."""

    kind: _KIND
    name: str
    accuracy: bool | None = None
    benchmark: bool | None = None


@dataclass(frozen=True)
class _Case:
    """Parts to combine, and the facts the combination later records on its own revision."""

    parts: tuple[_Part, ...]
    accuracy: bool | None
    benchmark: bool | None
    throughput: int


def _coverage(*, accuracy: bool | None, benchmark: bool | None) -> str:
    """Facts one revision has recorded on itself: a pass, and whether a benchmark exists."""
    passed = accuracy is True
    has_benchmark = benchmark is not None
    if passed and has_benchmark:
        return "both"
    if passed:
        return "accuracy"
    if has_benchmark:
        return "benchmark"
    return "none"


def _part_facts(part: _Part) -> str:
    """The facts the part's own revision carries, from how the part was recorded."""
    if part.kind == "submitted":
        return "none"
    if part.kind == "parent":
        return "both" if part.benchmark else "accuracy"
    return _coverage(accuracy=part.accuracy, benchmark=part.benchmark)


def _may_move(*, accuracy: bool | None, benchmark: bool | None, throughput: int) -> bool:
    """Both trusted facts on this revision, and a benchmark no worse than main's."""
    return accuracy is True and benchmark is True and throughput >= _MAIN


@st.composite
def _cases(draw: st.DrawFn) -> _Case:
    """Implemented revisions, at least one not an offered parent, and the combination's own facts."""
    names = draw(st.lists(st.sampled_from("abcdefgh"), min_size=2, max_size=4, unique=True))
    parts: list[_Part] = []
    for name in names:
        kind = draw(st.sampled_from(("submitted", "round", "parent")))
        if kind == "submitted":
            parts.append(_Part("submitted", name))
        elif kind == "parent":
            parts.append(_Part("parent", name, benchmark=draw(st.booleans())))
        else:
            parts.append(
                _Part(
                    "round",
                    name,
                    accuracy=draw(st.sampled_from((None, True, False))),
                    benchmark=draw(st.sampled_from((None, True, False))),
                )
            )
    assume(any(part.kind != "parent" for part in parts))
    return _Case(
        parts=tuple(parts),
        accuracy=draw(st.sampled_from((None, True, False))),
        benchmark=draw(st.sampled_from((None, True, False))),
        throughput=draw(st.sampled_from((50, _MAIN))),
    )


def _record(parts: tuple[_Part, ...]) -> RunRecord:
    """A run whose parts are parents, finished rounds, or candidates still waiting to be measured."""
    run = run_record()
    parents = []
    for part in parts:
        made = revision(f"{part.name}-r1")
        if part.kind == "parent":
            parents.append(snapshot(part.name, 1, value=10.0 if part.benchmark else None))
        elif part.kind == "submitted":
            run.submit(
                Submission(
                    key=f"key-{part.name}",
                    work_id=part.name,
                    kind=WorkKind.IMPLEMENT,
                    candidate=made,
                    parent=BASE,
                    at=0,
                )
            )
        else:
            recorded = hypothesis_record(part.name, HypothesisOutcome.INCONCLUSIVE)
            run.hypotheses[part.name] = recorded.model_copy(
                update={
                    "rounds": (
                        _round(
                            name=part.name,
                            candidate=made,
                            accuracy=part.accuracy,
                            benchmark=part.benchmark,
                        ),
                    )
                }
            )
    run.parents = tuple(parents)
    return run


def _round(
    *, name: str, candidate: RevisionRef, accuracy: bool | None, benchmark: bool | None
) -> RoundRecord:
    return RoundRecord(
        sequence=1,
        attempt=AttemptId(root=f"attempt-{name}"),
        outcome=HypothesisOutcome.INCONCLUSIVE,
        candidate=candidate,
        accuracy_passed=accuracy,
        benchmark_passed=benchmark,
    )


@pytest.mark.regression
@settings(max_examples=examples(pr=25, nightly=80))
@example(
    case=_Case(
        parts=(_Part("submitted", "a"), _Part("parent", "b", benchmark=True)),
        accuracy=None,
        benchmark=None,
        throughput=50,
    )
)
@given(case=_cases())
def test_an_unmeasured_candidate_may_be_combined_and_facts_stay_on_their_revision(
    case: _Case,
) -> None:
    """A combination of an unmeasured candidate is a plan; its facts and main are its own."""
    run = _record(case.parts)
    picks = tuple((part.name, f"{part.name}-r1") for part in case.parts)
    result = validate(
        portfolio(combine_entry("k", *picks)),
        run.world().ledger,
        run.offered(),
        capacity=4,
        offer=None,
    )
    assert result.valid, result.violations

    before = {item.hypothesis_id: item.facts.value for item in run.implemented()}
    assert "k" not in before
    for part in case.parts:
        assert before[part.name] == _part_facts(part)

    made = revision("k-r1")
    run.planned(Scheduled(check=result))
    (start,) = run.ready_to_start(1)
    assert [(part.hypothesis_id, part.revision.revision_id.root) for part in start.combine] == list(
        picks
    )
    run.submit(
        Submission(
            key=start.key,
            work_id="k",
            kind=WorkKind.IMPLEMENT,
            candidate=made,
            parent=start.parent,
            at=0,
        )
    )
    assert next(row.facts.value for row in run.implemented() if row.hypothesis_id == "k") == "none"
    recorded = hypothesis_record("k", HypothesisOutcome.INCONCLUSIVE)
    run.hypotheses["k"] = recorded.model_copy(
        update={
            "rounds": (
                _round(name="k", candidate=made, accuracy=case.accuracy, benchmark=case.benchmark),
            )
        }
    )
    after = {item.hypothesis_id: item.facts.value for item in run.implemented()}
    assert after["k"] == _coverage(accuracy=case.accuracy, benchmark=case.benchmark)
    for part in case.parts:
        assert after[part.name] == before[part.name]

    start = workstream_start(WorkKind.MERGE)
    done = WorkstreamDone(
        start=start,
        failure=None,
        settlement=settlement_of(eligible=True, retention="candidate", candidate=made, name="k"),
        evidence=evidence_of(
            accuracy=case.accuracy,
            benchmark=case.benchmark,
            throughput=float(case.throughput),
            candidate=made,
        ),
        merge=MergeReport(outcome=MergeOutcome.MERGED, summary="merged", revision=made),
    )
    line = MainLine(revision=BASE, baseline=start.baseline)
    line.measurement = MainMeasurement(
        revision=BASE,
        merge_id="m0",
        metrics=(MetricRow(name="throughput", value=float(_MAIN), direction="max"),),
        accuracy_passed=True,
        benchmark_passed=True,
    )
    line.finished(done, TERMS)
    assert (line.revision == made) is _may_move(
        accuracy=case.accuracy, benchmark=case.benchmark, throughput=case.throughput
    )


@pytest.mark.regression
@example(accuracy=True, benchmark=True)
@given(
    accuracy=st.sampled_from((None, True, False)),
    benchmark=st.sampled_from((None, True, False)),
)
def test_facts_follow_the_commit_across_labels_but_never_follow_it_to_another_commit(
    *, accuracy: bool | None, benchmark: bool | None
) -> None:
    run = run_record()
    for name, candidate, acc, bench in (
        ("source", revision("same"), accuracy, benchmark),
        ("alias", revision("same"), None, None),
        ("child", revision("different"), None, None),
    ):
        record = hypothesis_record(name, HypothesisOutcome.INCONCLUSIVE)
        run.hypotheses[name] = record.model_copy(
            update={
                "rounds": (_round(name=name, candidate=candidate, accuracy=acc, benchmark=bench),)
            }
        )
    facts = {row.hypothesis_id: row.facts.value for row in run.implemented()}
    assert facts["source"] == facts["alias"] == _coverage(accuracy=accuracy, benchmark=benchmark)
    assert facts["child"] == "none"


@pytest.mark.regression
@example(rows=(("a", True, None), ("b", None, True)))
@example(rows=(("a", True, None), ("a", False, True)))
@given(
    rows=st.lists(
        st.tuples(
            st.sampled_from(("a", "b", "c")),
            st.sampled_from((None, True, False)),
            st.sampled_from((None, True, False)),
        ),
        min_size=1,
        max_size=8,
    ).map(tuple)
)
def test_revision_coverage_never_splices_facts_from_different_round_revisions(
    rows: tuple[tuple[str, bool | None, bool | None], ...],
) -> None:
    run = run_record()
    record = hypothesis_record("combined", HypothesisOutcome.INCONCLUSIVE)
    rounds = tuple(
        _round(name="combined", candidate=revision(name), accuracy=acc, benchmark=bench).model_copy(
            update={"sequence": i + 1}
        )
        for i, (name, acc, bench) in enumerate(rows)
    )
    run.hypotheses["combined"] = record.model_copy(update={"rounds": rounds})
    facts = {row.revision.revision_id.root: row.facts.value for row in run.implemented()}
    for name in {name for name, _, _ in rows}:
        own = [(acc, bench) for candidate, acc, bench in rows if candidate == name]
        accuracy = any(acc is True for acc, _ in own) and not any(acc is False for acc, _ in own)
        has_benchmark = any(bench is not None for _, bench in own)
        assert facts[name] == _coverage(
            accuracy=accuracy, benchmark=True if has_benchmark else None
        )
