"""Main moves only when one revision has both trusted facts, whatever order they arrived in."""

from __future__ import annotations

import json

from hypothesis import example, given, settings
from hypothesis import strategies as st
from tests.vibesys.orchestration.dynamic._host import TERMS
from tests.vibesys.orchestration.dynamic._values import (
    BASE,
    BASELINE_THROUGHPUT,
    CANDIDATE,
    evidence_of,
    revision,
    settlement_of,
    workstream_start,
)

from vibesys.candidates.api import (
    InputReading,
    Measured,
    improves_on,
    measurement,
    reading,
    space_of,
)
from vibesys.metrics import MetricRow
from vibesys.orchestration.dynamic.search.api import (
    Evidence,
    MainLine,
    MainState,
    MergeOutcome,
    MergeReport,
    WorkKind,
    WorkstreamDone,
    WorkstreamStart,
)
from vs_core.api import (
    ArtifactId,
    ArtifactRef,
    EvidenceKind,
    MeasurementRecipe,
    RunView,
    StageBudget,
)
from vs_evaluation.api import SubmitCall
from vs_runtime.api.core import AgentEvaluationPolicy
from vs_sim.api.testing import examples

_GATES = ("accuracy", "benchmark")


def _facts(updates: tuple[tuple[str, bool, int], ...]) -> tuple[bool | None, bool | None, float]:
    """Last write of each gate wins; a gate that never arrived stays unknown."""
    accuracy: bool | None = None
    benchmark: bool | None = None
    throughput = 0.0
    for gate, passed, value in updates:
        if gate == "accuracy":
            accuracy = passed
        else:
            benchmark = passed
            throughput = float(value)
    return accuracy, benchmark, throughput


def _may_become_main(*, accuracy: bool | None, benchmark: bool | None, throughput: float) -> bool:
    """Both facts, accuracy passing, and a benchmark no worse than main's."""
    return accuracy is True and benchmark is True and throughput >= BASELINE_THROUGHPUT


@settings(max_examples=examples(pr=40, nightly=200))
@example(updates=(("benchmark", True, 200),))
@example(updates=(("accuracy", True, 0), ("benchmark", True, 100)))
@example(updates=(("benchmark", True, 10), ("accuracy", False, 0)))
@given(
    updates=st.lists(
        st.tuples(st.sampled_from(_GATES), st.booleans(), st.integers(0, 200)),
        max_size=8,
    ).map(tuple)
)
def test_main_advances_only_with_both_facts_on_the_same_revision(
    updates: tuple[tuple[str, bool, int], ...],
) -> None:
    accuracy, benchmark, throughput = _facts(updates)
    start = workstream_start(WorkKind.MERGE)
    done = WorkstreamDone(
        start=start,
        failure=None,
        settlement=settlement_of(eligible=True, retention="candidate", candidate=CANDIDATE),
        evidence=evidence_of(
            accuracy=accuracy, benchmark=benchmark, throughput=throughput, candidate=CANDIDATE
        ),
        merge=MergeReport(outcome=MergeOutcome.MERGED, summary="merged", revision=CANDIDATE),
    )
    line = MainLine(revision=BASE, baseline=start.baseline)
    line.finished(done, TERMS)
    became_main = line.revision == CANDIDATE
    assert became_main is _may_become_main(
        accuracy=accuracy, benchmark=benchmark, throughput=throughput
    )
    if accuracy is not True:
        assert line.revision == BASE


# A run view saved before plans were repeated: no ``measured_plans`` key. The search then
# measures the task's own stages, which is the default an agent that named no steps still gets.
_OLD_RUN_VIEW = {
    "run": {"owner": {"root": "run", "kind": "run"}, "generation": 0},
    "now_at": 0.0,
    "deadline_at": 1000.0,
    "facts": {
        "objective": "two opaque items",
        "options": [],
        "constraints": [],
        "baseline": {"revision_id": {"root": "base", "kind": "revision"}, "digest": "base-digest"},
        "evaluator_digest": "evaluator",
        "workload_digest": "workload",
        "environment_digest": "environment",
        "artifacts": [],
        "task": None,
    },
    "capabilities": {"lifecycle": [], "operations": []},
    "limits": {
        "max_attempts": 1,
        "max_turns": 1,
        "max_parallel": 1,
        "pool_capacities": [],
        "max_refunds": 0,
        "max_adoption_rounds": 1,
        "request_retries": {"limit": 0, "backoff": 1.0, "backoff_cap": 30.0},
        "cancellation_bound": 60.0,
        "cleanup_bound": 60.0,
        "reconciliation_bound": 60.0,
        "observe_interval": 10.0,
        "observe_backoff_cap": 120.0,
        "queue_wait_budget": 43200.0,
        "queue_notice_interval": 600.0,
    },
    "attempts": [],
    "measurements": [],
    "settlements": [],
    "comparisons": [],
}

_RUN_RECIPE = "run"
_Fact = tuple[bool, float]
"""A gate's passing bit and, for a benchmark, its throughput. The first record stands."""


def _policy() -> AgentEvaluationPolicy:
    recipe = MeasurementRecipe(
        evaluator_digest="evaluator",
        workload_digest="workload",
        environment_digest="environment",
        recipe=ArtifactRef(artifact_id=ArtifactId(root="recipe"), digest="recipe"),
        stages=(
            StageBudget(stage_id="accuracy", execution_seconds=1.0),
            StageBudget(stage_id="benchmark", execution_seconds=1.0),
        ),
        queue_allowance=1.0,
        accuracy_stage="accuracy",
        accuracy_gated=(EvidenceKind.BENCHMARK,),
    )
    return AgentEvaluationPolicy.from_recipe(recipe)


def _start(*, benchmark_passed: bool) -> WorkstreamStart:
    baseline = Measured(
        reading=InputReading(
            accuracy_passed=True,
            benchmark_passed=benchmark_passed,
            metrics=(MetricRow(name="throughput", value=BASELINE_THROUGHPUT, direction="max"),),
        )
    )
    return workstream_start(WorkKind.MERGE).model_copy(update={"baseline": baseline})


def _old_main(start: WorkstreamStart) -> MainLine:
    """Main as a checkpoint from before step choice resumed it: the input, generation zero."""
    state = MainState(
        revision=BASE,
        generation=0,
        measurement=None,
        merges=(),
        rounds=(),
        merged=(),
    )
    return MainLine.resumed(start.baseline, MainState.model_validate_json(state.model_dump_json()))


def _apply(
    policy: AgentEvaluationPolicy,
    ledger: dict[tuple[str, str, str], _Fact],
    known: dict[tuple[str, str], set[str]],
    event: tuple[object, ...],
) -> None:
    """Record the gates ``event`` actually runs. A named gate already stored is not replaced."""
    _kind, revision, steps, recipe, accuracy_passed, benchmark_passed, throughput = event
    assert isinstance(revision, str)
    assert isinstance(recipe, str)
    assert isinstance(steps, tuple)
    assert isinstance(accuracy_passed, bool)
    assert isinstance(benchmark_passed, bool)
    assert isinstance(throughput, int)
    held = known.setdefault((revision, recipe), set())
    named = tuple(step for step in steps if isinstance(step, str))
    stages = policy.stages_to_run(named, recorded=frozenset(held) if named else frozenset())
    for stage in ("accuracy", "benchmark"):
        if named and stage in held:
            assert stage not in stages
    fresh = {
        "accuracy": (bool(accuracy_passed), 0.0),
        "benchmark": (bool(benchmark_passed), float(throughput)),
    }
    for stage in stages:
        if stage not in fresh:
            continue
        ledger[(revision, recipe, stage)] = fresh[stage]
        held.add(stage)


def _headline(line: MainLine) -> tuple[bool | None, MetricRow | None]:
    if line.measurement is not None:
        metrics = line.measurement.metrics
        return line.measurement.benchmark_passed, metrics[0] if metrics else None
    seen = None if line.baseline is None else reading(line.baseline)
    if seen is None:
        return None, None
    return seen.benchmark_passed, seen.metrics[0] if seen.metrics else None


def _worse(new: float, main: MetricRow | None) -> bool:
    row = MetricRow(name="throughput", value=new, direction="max")
    return main is not None and improves_on(
        measurement(main),
        measurement(row),
        space=space_of(TERMS),
        when_input_unmeasured=False,
    )


def _promote(
    line: MainLine, start: WorkstreamStart, revision_name: str, evidence: Evidence
) -> None:
    candidate = revision(revision_name)
    done = WorkstreamDone(
        start=start,
        failure=None,
        settlement=settlement_of(eligible=True, retention="candidate", candidate=candidate),
        evidence=evidence,
        merge=MergeReport(outcome=MergeOutcome.MERGED, summary="merged", revision=candidate),
    )
    line.finished(done, TERMS)


@settings(max_examples=examples(pr=25, nightly=120))
@example(
    events=(("submit", "c1", ("benchmark",), "run", True, True, 200), ("promote", "c1")),
    main_benchmark_passed=False,
)
@example(
    events=(("submit", "c1", ("accuracy",), "run", True, True, 200), ("promote", "c1")),
    main_benchmark_passed=False,
)
@example(
    events=(
        ("submit", "c1", ("benchmark",), "run", True, True, 200),
        ("submit", "c1", ("benchmark",), "run", False, False, 1),
        ("submit", "c1", ("accuracy",), "run", True, False, 1),
        ("promote", "c1"),
    ),
    main_benchmark_passed=True,
)
@example(
    events=(
        ("submit", "c1", ("accuracy",), "run", True, True, 0),
        ("submit", "c2", ("benchmark",), "run", True, True, 200),
        ("promote", "c1"),
        ("promote", "c2"),
    ),
    main_benchmark_passed=True,
)
@example(
    events=(
        ("submit", "c1", ("benchmark", "accuracy"), "other", True, True, 200),
        ("promote", "c1"),
    ),
    main_benchmark_passed=True,
)
@example(
    events=(
        ("submit", "c1", (), "run", True, True, 200),
        ("promote", "c1"),
    ),
    main_benchmark_passed=True,
)
@given(
    events=st.lists(
        st.one_of(
            st.tuples(
                st.just("submit"),
                st.sampled_from(("c1", "c2")),
                st.one_of(
                    st.just(()),
                    st.tuples(st.sampled_from(_GATES)),
                    st.permutations(list(_GATES)).map(tuple),
                ),
                st.sampled_from((_RUN_RECIPE, "other")),
                st.booleans(),
                st.booleans(),
                st.integers(0, 200),
            ),
            st.tuples(st.just("promote"), st.sampled_from(("c1", "c2"))),
        ),
        max_size=6,
    ).map(tuple),
    main_benchmark_passed=st.booleans(),
)
def test_main_never_advances_without_both_facts_on_that_revision(
    events: tuple[tuple[object, ...], ...],
    *,
    main_benchmark_passed: bool,
) -> None:
    """Facts stay on the revision and recipe that recorded them, in whatever order they arrive.

    A saved run view from before this change loads with no chosen plans. A named gate that was
    already recorded is reused, not replaced. Main moves only onto a revision that itself holds
    a passing accuracy result and a benchmark no worse than main.
    """
    restored = RunView.model_validate_json(json.dumps(_OLD_RUN_VIEW))
    assert restored.measured_plans == ()
    call = SubmitCall.model_validate({"action": "submit", "token": "t"})
    assert call.steps == ()

    policy = _policy()
    start = _start(benchmark_passed=main_benchmark_passed)
    line = _old_main(start)
    ledger: dict[tuple[str, str, str], _Fact] = {}
    known: dict[tuple[str, str], set[str]] = {}
    for event in events:
        if event[0] == "submit":
            _apply(policy, ledger, known, event)
            continue
        name = event[1]
        assert isinstance(name, str)
        accuracy = ledger.get((name, _RUN_RECIPE, "accuracy"))
        benchmark = ledger.get((name, _RUN_RECIPE, "benchmark"))
        before = line.revision
        main_passed, main_row = _headline(line)
        _promote(
            line,
            start,
            name,
            evidence_of(
                accuracy=None if accuracy is None else accuracy[0],
                benchmark=None if benchmark is None else benchmark[0],
                throughput=0.0 if benchmark is None else benchmark[1],
                candidate=revision(name),
            ),
        )
        if line.revision == before:
            continue
        assert line.revision == revision(name)
        assert accuracy is not None
        assert accuracy[0] is True
        assert benchmark is not None
        lost = benchmark[0] is not True and main_passed is True
        assert not lost
        assert not _worse(benchmark[1], main_row)


def test_the_default_order_records_both_gates_and_moves_main() -> None:
    policy = _policy()
    ledger: dict[tuple[str, str, str], _Fact] = {}
    known: dict[tuple[str, str], set[str]] = {}
    _apply(policy, ledger, known, ("submit", "c1", (), "run", True, True, 200))
    assert tuple(policy.stages_to_run(())) == ("accuracy", "benchmark")
    assert ledger[("c1", "run", "accuracy")] == (True, 0.0)
    assert ledger[("c1", "run", "benchmark")] == (True, 200.0)

    start = _start(benchmark_passed=True)
    line = _old_main(start)
    _promote(
        line,
        start,
        "c1",
        evidence_of(accuracy=True, benchmark=True, throughput=200.0, candidate=revision("c1")),
    )
    assert line.revision == revision("c1")


def test_a_named_gate_already_recorded_keeps_the_first_fact() -> None:
    policy = _policy()
    ledger: dict[tuple[str, str, str], _Fact] = {}
    known: dict[tuple[str, str], set[str]] = {}
    _apply(policy, ledger, known, ("submit", "c1", ("benchmark",), "run", True, True, 200))
    _apply(policy, ledger, known, ("submit", "c1", ("benchmark",), "run", False, False, 1))
    _apply(policy, ledger, known, ("submit", "c1", ("accuracy",), "run", True, False, 1))
    assert ledger[("c1", "run", "benchmark")] == (True, 200.0)
    assert ledger[("c1", "run", "accuracy")] == (True, 0.0)

    start = _start(benchmark_passed=True)
    line = _old_main(start)
    _promote(
        line,
        start,
        "c1",
        evidence_of(accuracy=True, benchmark=True, throughput=200.0, candidate=revision("c1")),
    )
    assert line.revision == revision("c1")
