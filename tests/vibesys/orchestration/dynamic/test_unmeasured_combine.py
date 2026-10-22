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
