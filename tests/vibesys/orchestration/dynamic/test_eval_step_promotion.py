"""Main moves only when one revision has both trusted facts, whatever order they arrived in."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st
from tests.vibesys.orchestration.dynamic._host import TERMS
from tests.vibesys.orchestration.dynamic._values import (
    BASE,
    BASELINE_THROUGHPUT,
    CANDIDATE,
    SCOPE,
    evidence_of,
    evidence_ref,
    result_of,
    revision,
    settlement_of,
    workstream_start,
)

from vibesys.candidates.api import (
    EligibleRound,
    InputReading,
    Measured,
    Unranked,
    choose_winner,
    improves_on,
    measurement,
    space_of,
)
from vibesys.hypothesis import HypothesisOutcome
from vibesys.metrics import AcceptedReading, EvidenceReading, MetricRow, accept_readings
from vibesys.orchestration.dynamic.search.api import (
    Closing,
    ComposedJobs,
    Evidence,
    MainLine,
    MeasuredJob,
    MergeOutcome,
    MergeReport,
    Proof,
    WorkKind,
    WorkstreamDone,
    WorkstreamStart,
    assertion,
    compose_jobs,
    options,
    round_of,
    snapshot_of,
)
from vibesys.serial_search.api import beats_input as serial_beats_input
from vs_core.api import (
    ArtifactId,
    ArtifactRef,
    EvidenceKind,
    EvidenceRef,
    MeasurementPlan,
    MeasurementRecipe,
    MeasurementResult,
    ObservationStatus,
    RequestId,
    RevisionRef,
    RunView,
    Scope,
    StageBudget,
    TaskObjective,
    TaskTerms,
)
from vs_core.api.search import trusted_keys
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


# stages, recipe digest, purpose, scope generation, accuracy passed, benchmark passed,
# benchmark throughput, whether the official record was reused, candidate name.
_Row = tuple[tuple[str, ...], str, str, int, bool, bool, int, bool, str]
_TASK = "recipe"


@dataclass(frozen=True)
class _Built:
    """The plans and the evidence a measurement composed, with the row each record came from."""

    jobs: tuple[MeasuredJob, ...]
    rows: dict[object, _Row]


def _built(rows: tuple[_Row, ...]) -> _Built:
    """Plans from the task recipe, then one fake result per plan, in the rows' order.

    The stage list is what the agent policy would run. The official plan is what
    ``measurement_of`` submits. A benchmark-only plan carries an empty accuracy gate.
    """
    policy = _policy()
    recipe = policy.measurement
    jobs: list[MeasuredJob] = []
    by_key: dict[object, _Row] = {}
    for index, row in enumerate(rows):
        (
            stages,
            digest,
            purpose,
            generation,
            accuracy_passed,
            benchmark_passed,
            _throughput,
            reused,
            name,
        ) = row
        planned = policy.stages_to_run(stages)
        assert planned == stages
        candidate = revision(name)
        plan = _plan(recipe, candidate, purpose, planned)
        agent = policy.plan(candidate, measured_input="tree", now_at=0.0, stages=planned)
        assert agent.accuracy_gated == plan.accuracy_gated
        assert (EvidenceKind.BENCHMARK in plan.accuracy_gated) is ("accuracy" in planned)
        if digest != plan.recipe.digest:
            plan = plan.model_copy(
                update={"recipe": plan.recipe.model_copy(update={"digest": digest})}
            )
        scope = Scope(owner=SCOPE.owner, generation=generation)
        refs: list[EvidenceRef] = []
        if "accuracy" in planned:
            refs.append(
                _record(
                    f"accuracy-{index}",
                    EvidenceKind.CORRECTNESS,
                    candidate,
                    scope,
                    (purpose, accuracy_passed, reused, index),
                )
            )
        if "benchmark" in planned:
            refs.append(
                _record(
                    f"benchmark-{index}",
                    EvidenceKind.BENCHMARK,
                    candidate,
                    scope,
                    (purpose, benchmark_passed, reused, index),
                )
            )
        for ref in refs:
            by_key[ref.key] = row
        jobs.append(
            MeasuredJob(
                plan=plan,
                result=MeasurementResult(
                    scope=scope,
                    source_request=None if not refs else refs[0].source_request,
                    evidence=tuple(refs),
                    status=ObservationStatus.SUCCEEDED
                    if all(ref.status is ObservationStatus.SUCCEEDED for ref in refs)
                    else ObservationStatus.FAILED,
                ),
            )
        )
    return _Built(jobs=tuple(jobs), rows=by_key)


def _plan(
    recipe: MeasurementRecipe, candidate: RevisionRef, purpose: str, stages: tuple[str, ...]
) -> MeasurementPlan:
    """The official plan for one purpose the rows draw."""
    if purpose == "official":
        return recipe.plan(candidate, purpose="official", now_at=0.0, stages=stages)
    if purpose == "local-validation":
        return recipe.plan(candidate, purpose="local-validation", now_at=0.0, stages=stages)
    message = f"unexpected purpose {purpose}"
    raise AssertionError(message)


def _record(
    name: str,
    kind: EvidenceKind,
    candidate: RevisionRef,
    scope: Scope,
    facts: tuple[str, bool, bool, int],
) -> EvidenceRef:
    purpose, passed, reused, index = facts
    ref = evidence_ref(name, kind, candidate, request=f"job-{index}")
    return ref.model_copy(
        update={
            "purpose": purpose,
            "scope": scope,
            "status": ObservationStatus.SUCCEEDED if passed else ObservationStatus.FAILED,
            "reused_from": RequestId(root=f"reused-{index}") if reused else None,
        }
    )


def _matches(row: _Row, *, candidate: str, generation: int) -> bool:
    """Whether ``row`` is official evidence of ``candidate`` under the task recipe at ``generation``."""
    stages, digest, purpose, scope, _accuracy, _benchmark, _throughput, _reused, name = row
    return (
        digest == _TASK
        and purpose == "official"
        and scope == generation
        and name == candidate
        and bool(stages)
    )


def _proved(rows: tuple[_Row, ...], *, candidate: str, generation: int) -> bool:
    """A passing official accuracy of the task recipe, with no failed one beside it."""
    passed = False
    failed = False
    for row in rows:
        if not _matches(row, candidate=candidate, generation=generation):
            continue
        if "accuracy" not in row[0]:
            continue
        if row[4]:
            passed = True
        else:
            failed = True
    return passed and not failed


def _oracle(rows: tuple[_Row, ...]) -> tuple[bool, tuple[bool, float] | None]:
    """The decisive accuracy and benchmark of the workspace's candidate, last write wins."""
    accuracy: bool | None = None
    benchmark: tuple[bool, float] | None = None
    proved = _proved(rows, candidate="candidate", generation=0)
    for row in rows:
        if not _matches(row, candidate="candidate", generation=0):
            continue
        (
            stages,
            _digest,
            _purpose,
            _scope,
            accuracy_passed,
            benchmark_passed,
            throughput,
            _reused,
            _name,
        ) = row
        if "accuracy" in stages:
            accuracy = accuracy_passed
        if "benchmark" in stages and ("accuracy" in stages or proved):
            benchmark = (benchmark_passed, float(throughput))
    return accuracy is True, benchmark


def _worse(throughput: float) -> bool:
    new = MetricRow(name="throughput", value=throughput, direction="max")
    main = MetricRow(name="throughput", value=BASELINE_THROUGHPUT, direction="max")
    return improves_on(
        measurement(main),
        measurement(new),
        space=space_of(TERMS),
        when_input_unmeasured=False,
    )


def _beats(throughput: float) -> bool:
    new = MetricRow(name="throughput", value=throughput, direction="max")
    main = MetricRow(name="throughput", value=BASELINE_THROUGHPUT, direction="max")
    return improves_on(
        measurement(new),
        measurement(main),
        space=space_of(TERMS),
        when_input_unmeasured=False,
    )


def _expect_main(
    *, accuracy_ok: bool, benchmark: tuple[bool, float] | None, main_passed: bool
) -> bool:
    if not accuracy_ok or benchmark is None:
        return False
    passed, throughput = benchmark
    lost = passed is not True and main_passed is True
    return not (_worse(throughput) or lost)


def _expect_win(*, accuracy_ok: bool, benchmark: tuple[bool, float] | None) -> bool:
    if not accuracy_ok or benchmark is None or benchmark[0] is not True:
        return False
    return _beats(benchmark[1])


def _assert_pairing(composed: ComposedJobs, built: _Built, rows: tuple[_Row, ...]) -> None:
    for ref in composed.decisive.evidence:
        row = built.rows[ref.key]
        stages, digest, purpose, generation, name = row[0], row[1], row[2], row[3], row[8]
        assert digest == _TASK
        assert purpose == "official"
        assert generation == ref.scope.generation
        assert name == ref.candidate.revision_id.root
        if ref.kind is EvidenceKind.BENCHMARK and "accuracy" not in stages:
            assert _proved(rows, candidate=name, generation=generation)
    for ref in composed.unverified:
        row = built.rows[ref.key]
        assert row[1] == _TASK
        assert row[2] == "official"
        assert "accuracy" not in row[0]
        assert ref.kind is EvidenceKind.BENCHMARK
        assert not _proved(rows, candidate=row[8], generation=row[3])


def _readings(refs: tuple[EvidenceRef, ...], built: _Built) -> tuple[AcceptedReading, ...]:
    raw = []
    for ref in refs:
        row = built.rows[ref.key]
        metrics = ()
        if ref.kind is EvidenceKind.BENCHMARK:
            metrics = (MetricRow(name="throughput", value=float(row[6]), direction="max"),)
        raw.append(
            EvidenceReading(
                evidence_id=ref.evidence_id,
                kind=ref.kind,
                passed=ref.status is ObservationStatus.SUCCEEDED,
                stage=ref.kind.value,
                metrics=metrics,
            )
        )
    accepted = accept_readings(tuple(raw), refs)
    assert not isinstance(accepted, str)
    return accepted


def _holds(rows: tuple[_Row, ...], *, main_benchmark_passed: bool) -> None:
    """Settlement, main, the parent offer and the winner see only accuracy-verified benchmarks."""
    built = _built(rows)
    composed = compose_jobs(built.jobs, recipe_digest=_TASK, accuracy_stage="accuracy")
    _assert_pairing(composed, built, rows)
    found = composed.decisive
    if found.scope != SCOPE:
        decisive_refs: tuple[EvidenceRef, ...] = ()
    else:
        keys = trusted_keys(found, scope=SCOPE, candidate=CANDIDATE, purpose="official")
        decisive_refs = tuple(ref for ref in found.evidence if ref.key in keys)
    extra = tuple(
        ref for ref in composed.unverified if ref.candidate == CANDIDATE and ref.scope == SCOPE
    )
    decisive_readings = _readings(decisive_refs, built)
    evidence = Evidence(
        readings=decisive_readings,
        proof=None if not decisive_refs else Proof(refs=decisive_refs),
        shown=decisive_readings + _readings(extra, built),
    )
    start = _start(benchmark_passed=main_benchmark_passed)
    closing = Closing(
        evidence=evidence,
        merge=MergeReport(outcome=MergeOutcome.MERGED, summary="merged", revision=CANDIDATE),
    )
    settled = assertion(start, closing, TERMS, start.baseline)
    done = WorkstreamDone(
        start=start,
        failure=None,
        settlement=settlement_of(
            eligible=settled.eligible,
            retention=settled.retention,
            candidate=settled.candidate,
        ),
        evidence=evidence,
        merge=closing.merge,
    )
    line = MainLine(revision=BASE, baseline=start.baseline)
    line.finished(done, TERMS)
    winner = choose_winner(
        tuple(line.rounds),
        space=space_of(TERMS),
        baseline=BASE,
        when_unranked=Unranked.LATEST,
    )
    record = round_of(done)
    impl: tuple[EligibleRound, ...] = ()
    if record.eligible and record.candidate is not None and record.settlement is not None:
        impl = (
            EligibleRound(
                hypothesis_id=start.plan.work_id,
                sequence=record.sequence,
                settlement=record.settlement,
                candidate=record.candidate,
                metrics=record.metrics,
            ),
        )
    impl_winner = choose_winner(
        impl, space=space_of(TERMS), baseline=BASE, when_unranked=Unranked.LATEST
    )
    assert impl_winner.revision == winner.revision
    accuracy_ok, benchmark = _oracle(rows)
    assert (line.revision == CANDIDATE) is _expect_main(
        accuracy_ok=accuracy_ok, benchmark=benchmark, main_passed=main_benchmark_passed
    )
    assert (winner.revision == CANDIDATE) is _expect_win(
        accuracy_ok=accuracy_ok, benchmark=benchmark
    )
    parent = snapshot_of(done)
    if parent is not None:
        assert accuracy_ok
        if parent.benchmark is not None:
            assert benchmark is not None
    if extra and not accuracy_ok:
        assert record.benchmark_passed is not None
        assert record.accuracy_passed is not True


_STAGES = st.one_of(
    st.just(("accuracy",)),
    st.just(("benchmark",)),
    st.just(("accuracy", "benchmark")),
    st.just(("benchmark", "accuracy")),
)
_ROW = st.tuples(
    _STAGES,
    st.sampled_from((_TASK, "other")),
    st.sampled_from(("official", "local-validation")),
    st.just(0),
    st.booleans(),
    st.booleans(),
    st.sampled_from((10, 50, 200)),
    st.booleans(),
    st.sampled_from(("candidate", "other")),
)
_SPLICE: _Row = (("benchmark",), _TASK, "official", 0, True, True, 200, False, "candidate")
_OTHER_ACCURACY: _Row = (("accuracy",), "other", "official", 0, True, True, 0, False, "candidate")
_TASK_ACCURACY: _Row = (("accuracy",), _TASK, "official", 0, True, True, 0, False, "candidate")


@pytest.mark.regression
@settings(max_examples=examples(pr=40, nightly=160), deadline=None)
@example(rows=(_SPLICE, _OTHER_ACCURACY), main_benchmark_passed=True)
@example(rows=(_OTHER_ACCURACY, _SPLICE), main_benchmark_passed=True)
@example(rows=(_SPLICE, _TASK_ACCURACY), main_benchmark_passed=True)
@example(rows=(_TASK_ACCURACY, _SPLICE), main_benchmark_passed=True)
@example(
    rows=(
        (("accuracy",), _TASK, "local-validation", 0, True, True, 0, True, "candidate"),
        _SPLICE,
    ),
    main_benchmark_passed=True,
)
@example(
    rows=(
        (("accuracy",), _TASK, "official", 0, True, True, 0, True, "candidate"),
        (("benchmark",), _TASK, "official", 0, True, True, 200, True, "candidate"),
    ),
    main_benchmark_passed=True,
)
@example(
    rows=(
        (("accuracy",), _TASK, "official", 0, False, True, 0, False, "candidate"),
        _SPLICE,
    ),
    main_benchmark_passed=True,
)
@example(
    rows=(
        _TASK_ACCURACY,
        (("benchmark",), _TASK, "official", 0, True, True, 200, False, "other"),
    ),
    main_benchmark_passed=True,
)
@given(rows=st.lists(_ROW, min_size=1, max_size=2).map(tuple), main_benchmark_passed=st.booleans())
def test_an_ungated_benchmark_counts_only_with_the_task_accuracy(
    rows: tuple[_Row, ...], *, main_benchmark_passed: bool
) -> None:
    """Separate plans, either order, decide a revision only with its official task accuracy.

    A benchmark whose plan omitted accuracy is informational until that same revision has a
    passing official accuracy under the task recipe, and a failed one of those withdraws it.
    Another recipe, purpose, scope or revision does not pair with it. This is the case the
    production combiner at 8c0228e0f got wrong: it concatenated the evidence and settlement
    adopted the spliced benchmark.
    """
    _holds(rows, main_benchmark_passed=main_benchmark_passed)


def test_an_accuracy_reading_from_another_scope_does_not_verify_the_benchmark() -> None:
    _holds(
        (
            (("accuracy",), _TASK, "official", 1, True, True, 0, False, "candidate"),
            _SPLICE,
        ),
        main_benchmark_passed=True,
    )


def test_a_saved_run_loads_with_no_chosen_plans_and_a_recorded_gate_is_not_repeated() -> None:
    restored = RunView.model_validate_json(json.dumps(_OLD_RUN_VIEW))
    assert restored.measured_plans == ()
    call = SubmitCall.model_validate({"action": "submit", "token": "t"})
    assert call.steps == ()
    policy = _policy()
    assert policy.stages_to_run(()) == ("accuracy", "benchmark")
    assert policy.stages_to_run(("benchmark",), recorded=frozenset({"benchmark"})) == ()


def test_a_task_without_an_accuracy_stage_keeps_its_benchmark() -> None:
    recipe = _policy().measurement.model_copy(update={"accuracy_stage": None, "accuracy_gated": ()})
    plan = recipe.plan(CANDIDATE, purpose="official", now_at=0.0, stages=("benchmark",))
    ref = evidence_ref("benchmark-only", EvidenceKind.BENCHMARK, CANDIDATE)
    result = MeasurementResult(
        scope=SCOPE,
        source_request=ref.source_request,
        evidence=(ref,),
        status=ObservationStatus.SUCCEEDED,
    )
    composed = compose_jobs(
        (MeasuredJob(plan=plan, result=result),), recipe_digest=_TASK, accuracy_stage=None
    )
    assert composed.unverified == ()
    assert composed.decisive.evidence == (ref,)


def test_a_profile_measurement_is_not_scored_as_an_unverified_benchmark() -> None:
    plan = _policy().measurement.plan(
        CANDIDATE, purpose="official", now_at=0.0, stages=("benchmark",)
    )
    plan = plan.model_copy(update={"purpose": "profile"})
    ref = evidence_ref("profile-benchmark", EvidenceKind.BENCHMARK, CANDIDATE)
    result = MeasurementResult(
        scope=SCOPE,
        source_request=ref.source_request,
        evidence=(ref,),
        status=ObservationStatus.SUCCEEDED,
    )
    composed = compose_jobs(
        (MeasuredJob(plan=plan, result=result),), recipe_digest=_TASK, accuracy_stage="accuracy"
    )
    assert composed.unverified == ()
    assert composed.decisive is result


# -- multi-objective promotion -----------------------------------------------------------------

_Axis = tuple[Literal["min", "max"], float, float]
_AXES = st.lists(
    st.tuples(
        st.sampled_from(("min", "max")),
        st.integers(0, 50000).map(float),
        st.integers(0, 50000).map(float),
    ),
    min_size=2,
    max_size=5,
).map(tuple)
_FLINT_AXES: tuple[_Axis, ...] = (
    ("min", 49.62709677419356, 50.36629032258063),
    ("min", 30577.0, 18744.17),
    ("max", 2.0, 2.0),
)


def _multi_evidence(metrics: tuple[MetricRow, ...]) -> Evidence:
    evidence = evidence_of(accuracy=True, benchmark=True, throughput=1.0)
    return evidence.model_copy(
        update={
            "readings": tuple(
                reading.model_copy(update={"metrics": metrics})
                if reading.kind is EvidenceKind.BENCHMARK
                else reading
                for reading in evidence.readings
            )
        }
    )


def _multi_start(
    metrics: tuple[MetricRow, ...], *, kind: WorkKind = WorkKind.IMPLEMENT
) -> WorkstreamStart:
    baseline = Measured(
        reading=InputReading(accuracy_passed=True, benchmark_passed=True, metrics=metrics)
    )
    return workstream_start(kind).model_copy(update={"baseline": baseline})


def _multi_rows(axes: tuple[_Axis, ...]) -> tuple[tuple[MetricRow, ...], tuple[MetricRow, ...]]:
    # Secondary metrics can carry a reporting default of max even when the
    # task declares min. Configured axes, rather than those defaults, govern.
    baseline = tuple(
        MetricRow(name=f"axis-{index}", value=base, direction="max")
        for index, (_, base, _) in enumerate(axes)
    )
    candidate = tuple(
        MetricRow(name=f"axis-{index}", value=value, direction="max")
        for index, (_, _, value) in enumerate(axes)
    )
    return baseline, candidate


def _multi_terms(axes: tuple[_Axis, ...], noise: float) -> TaskTerms:
    return TERMS.model_copy(
        update={
            "objectives": tuple(
                TaskObjective(name=f"axis-{index}", direction=direction)
                for index, (direction, _, _) in enumerate(axes)
            ),
            "relative_noise": noise,
        }
    )


@pytest.mark.regression
@example(axes=_FLINT_AXES, noise=0.05, reverse=False)
@example(axes=_FLINT_AXES, noise=0.05, reverse=True)
@example(axes=_FLINT_AXES, noise=0.0, reverse=False)
@example(axes=(("min", 100.0, 105.0), ("max", 100.0, 120.0)), noise=0.05, reverse=False)
@example(axes=(("min", 100.0, 106.0), ("max", 100.0, 120.0)), noise=0.05, reverse=False)
@given(axes=_AXES, noise=st.sampled_from((0.0, 0.01, 0.05)), reverse=st.booleans())
def test_promotion_merge_offer_and_winner_use_every_objective_with_its_noise(
    axes: tuple[_Axis, ...], noise: float, *, reverse: bool
) -> None:
    baseline, candidate = _multi_rows(axes)
    terms = _multi_terms(axes, noise)
    if reverse:
        terms = terms.model_copy(update={"objectives": tuple(reversed(terms.objectives))})
        candidate = tuple(reversed(candidate))
    gains = [
        value - base if direction == "max" else base - value for direction, base, value in axes
    ]
    tolerances = [abs(base) * noise for _, base, _ in axes]
    no_regression = all(
        gain >= -tolerance for gain, tolerance in zip(gains, tolerances, strict=True)
    )
    improves = no_regression and any(
        gain > tolerance for gain, tolerance in zip(gains, tolerances, strict=True)
    )
    start = _multi_start(baseline)
    evidence = _multi_evidence(candidate)
    closing = Closing(
        result=result_of(HypothesisOutcome.INCONCLUSIVE, review=True), evidence=evidence
    )
    proposed = assertion(start, closing, terms, start.baseline)
    assert proposed.eligible is improves
    assert start.baseline is not None
    assert serial_beats_input(start.baseline, evidence.readings, terms) is improves
    assert proposed.retention == "candidate"  # ties/tradeoffs still remain buildable
    done = WorkstreamDone(
        start=start,
        failure=None,
        result=closing.result,
        evidence=evidence,
        settlement=settlement_of(eligible=improves, retention="candidate"),
    )
    parent = snapshot_of(done)
    assert parent is not None
    line = MainLine(revision=BASE, baseline=start.baseline)
    offered = line.options(options((parent,)), terms, frozenset(), frozenset())
    assert offered.mergeable(start.plan.work_id) is improves
    rounds = tuple(
        EligibleRound(
            hypothesis_id=f"h{index}",
            sequence=index,
            settlement=settlement_of(
                eligible=True, retention="candidate", name=str(index)
            ).settlement_id,
            candidate=revision(f"c{index}"),
            metrics=metrics,
        )
        for index, metrics in enumerate((baseline, candidate))
    )
    winner = choose_winner(
        rounds, space=space_of(terms), baseline=BASE, when_unranked=Unranked.NONE
    )
    assert winner.hypothesis_id == ("h1" if improves else "h0")


@pytest.mark.regression
@example(axes=_FLINT_AXES, noise=0.05, missing=False)
@example(axes=(("min", 100.0, 106.0), ("max", 100.0, 120.0)), noise=0.05, missing=False)
@example(axes=_FLINT_AXES, noise=0.05, missing=True)
@given(axes=_AXES, noise=st.sampled_from((0.0, 0.01, 0.05)), missing=st.booleans())
def test_a_merge_cannot_hide_a_secondary_regression_or_missing_objective(
    axes: tuple[_Axis, ...], noise: float, *, missing: bool
) -> None:
    baseline, candidate = _multi_rows(axes)
    if missing:
        candidate = candidate[:-1]
    terms = _multi_terms(axes, noise)
    start = _multi_start(baseline, kind=WorkKind.MERGE)
    done = WorkstreamDone(
        start=start,
        failure=None,
        settlement=settlement_of(eligible=True, retention="candidate"),
        evidence=_multi_evidence(candidate),
        merge=MergeReport(outcome=MergeOutcome.MERGED, revision=CANDIDATE),
    )
    expected = not missing and all(
        (value - base if direction == "max" else base - value) >= -abs(base) * noise
        for direction, base, value in axes
    )
    line = MainLine(revision=BASE, baseline=start.baseline)
    record = line.finished(done, terms)
    assert (line.revision == CANDIDATE) is expected
    assert record.main_metrics == baseline
    resumed = MainLine.resumed(start.baseline, line.state())
    assert resumed.state() == line.state()
