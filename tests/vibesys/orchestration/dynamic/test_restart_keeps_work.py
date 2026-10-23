"""A code change mid-run keeps the dynamic search's work.

A run restarted after a code change resumes every flow from its latest checkpoint under the new
code. Whatever step the change comes after, and whatever order core answers in, the run must keep
what it had established:

- the input is measured exactly once over the whole run;
- every candidate the implementer handed the search is measured, or its code kept (a workspace
  settled as work in progress or as a candidate), never discarded;
- no workspace is opened twice, and every workspace is settled;
- the settled history (finished workstreams, hypothesis rounds) the run published survives.

A story drives the run to one point (the input in flight, a candidate in review, waiting for its
slot, measured, being interpreted, settling) and changes the code there; a property changes it
after generated steps, under a generated answer order.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass, field
from math import sqrt
from typing import TYPE_CHECKING, Literal

import pytest
from hypothesis import event, example, given, note, settings
from hypothesis import strategies as st
from tests.vibesys.orchestration.dynamic._host import (
    FACTS,
    INPUT_PATH,
    PLANNER_LABEL,
    Host,
    begin,
    config,
    core_view,
)
from tests.vibesys.orchestration.dynamic._main_watch import MainWatch
from tests.vibesys.orchestration.dynamic._values import (
    BASE,
    CANDIDATE,
    evidence_ref,
    workstream_start,
)
from tests.vibesys.vibesys_flows import start_flow

from vibesys.candidates.api import InputReading, Measured, input_metrics, space_of
from vibesys.dynamic_migration import migrate_objective_policy, recorded_objective_readings
from vibesys.metrics import MetricRow
from vibesys.orchestration.dynamic.search.api import (
    CoreHolds,
    DynamicConfig,
    DynamicResume,
    FinishedWork,
    InterpretEvidence,
    MergeOutcome,
    RunRecord,
    dynamic,
)
from vs_core.api import (
    ENVELOPE_SCHEMA_VERSION,
    AttemptRef,
    CheckpointKnown,
    ComparisonVerdict,
    EventCursor,
    EvidenceKind,
    FlowState,
    HostFence,
    HostId,
    InvocationId,
    InvocationRef,
    Measure,
    Operation,
    RequestTurn,
    Retained,
    RevisionRef,
    RunEnvelope,
    Scope,
    SessionId,
    Settle,
    StartAttempt,
    TaskObjective,
    Wait,
    Withdraw,
    WorkspaceRef,
    initial_state,
)
from vs_core.api.testing import RestartPolicy, code_changed
from vs_evaluation.api import (
    ContentDigest,
    EvidenceFingerprints,
    EvidenceMetric,
    EvidenceOutcome,
    TrustedEvidence,
)
from vs_evaluation.api import EvidenceKind as StageKind
from vs_runtime.api.core import RuntimeRecord
from vs_runtime.api.testing import FakeEvidenceLedger
from vs_sim.api.testing import examples

if TYPE_CHECKING:
    from collections.abc import Callable

    from vs_core.api.testing import Asked

CHANGED = code_changed(dynamic)
"""The dynamic search under other code: every flow restarts from its checkpoint when it runs."""


def _begin(cfg: DynamicConfig, policy: RestartPolicy | None = None) -> Host:
    run = start_flow(dynamic, cfg, facts=FACTS, restarting=policy or False, view=core_view(cfg))
    return Host(run, cfg)


@dataclass
class Seen:
    """What the test saw the run establish, to hold the end of the run to."""

    finished: dict[int, FinishedWork] = field(default_factory=dict)
    """Every finished workstream the run ever published, by its sequence."""
    rounds: dict[str, int] = field(default_factory=dict)
    """The most rounds the run ever published for each hypothesis."""
    main: MainWatch | None = None
    """What a run that merges published of main, checked after every answer."""


def _answer(host: Host, asked: Asked, seen: Seen) -> None:
    host.answer(asked)
    record = host.record
    if record is not None:
        for item in record.finished:
            seen.finished[item.sequence] = item
        for hypothesis in record.hypotheses:
            known = seen.rounds.get(hypothesis.hypothesis_id, 0)
            seen.rounds[hypothesis.hypothesis_id] = max(known, len(hypothesis.rounds))
    if seen.main is not None:
        seen.main.check(record)


def _play(
    host: Host,
    seen: Seen,
    picks: list[int],
    *,
    until: Callable[[Host], bool] | None = None,
    limit: int = 600,
) -> None:
    """Answer open waits, the ``picks``-th first, until ``until`` holds or nothing is left."""
    for index in range(limit):
        if until is not None and until(host):
            return
        waits = [item for item in host.run.open if item.until is not None]
        if not waits:
            if host.run.sleeping_until:
                host.run.advance_to(min(host.run.sleeping_until))
                continue
            assert until is None, f"the run ended before the point: {host.run.timeline()}"
            return
        pick = picks[index % len(picks)] if picks else 0
        _answer(host, waits[pick % len(waits)], seen)
    message = f"the run did not end in {limit} steps: {host.run.timeline()}"
    raise AssertionError(message)


def _waits(host: Host, *, kind: type, label: str | None = None) -> list[Asked]:
    return [
        item
        for item in host.run.open
        if isinstance(item.request, kind) and label in (None, item.label)
    ]


def _decisions[T](host: Host, kind: type[T]) -> list[T]:
    return [
        item.request
        for item in host.run.decisions
        if isinstance(item, Wait)
        and isinstance(item.request, kind)
        and not getattr(item.request, "reuse_only", False)  # core's own question, not a measurement
    ]


def _attempts_of(host: Host, path: str) -> set[str]:
    return {
        start.attempt_id.root
        for start in _decisions(host, StartAttempt)
        if start.decision_id.root.startswith(path)
    }


def _named(host: Host) -> set[RevisionRef]:
    """The revisions the search acted on: had reviewed, worked on, measured or settled.

    A candidate the implementer made counts once the search acted on it: a reply a restart cut
    off before the search had it (the turn is asked again) does not.
    """
    named: set[RevisionRef] = set()
    for item in host.run.decisions:
        match item:
            case Wait(request=RequestTurn(turn=turn)) if isinstance(turn.workspace, WorkspaceRef):
                named.add(turn.workspace.revision)
            case Wait(request=Measure(plan=plan)) if isinstance(plan.candidate, RevisionRef):
                named.add(plan.candidate)
            case Wait(request=Withdraw(disposition=Settle(candidate=candidate))) if candidate:
                named.add(candidate)
            case _:
                pass
    return named


def _kept(host: Host, asked: Asked, revision: RevisionRef) -> bool:
    """Whether the run measured ``revision`` or settled its workspace keeping its code."""
    measured = any(
        item.plan.candidate == revision and item.plan.purpose != "baseline"
        for item in _decisions(host, Measure)
    )
    attempts = _attempts_of(host, asked.path)
    settled = any(
        isinstance(item.target, AttemptRef)
        and item.target.attempt_id.root in attempts
        and isinstance(item.disposition, Settle)
        and item.disposition.candidate == revision
        and item.disposition.retention != "discard"
        for item in _decisions(host, Withdraw)
    )
    return measured or settled


def _check_kept_work(host: Host, seen: Seen) -> None:
    """The promises of a run restarted after code changes, checked once it is over."""
    where = host.run.timeline()
    assert host.run.finished is not None, f"the run did not finish: {where}"
    inputs = [item for item in _decisions(host, Measure) if item.plan.purpose == "baseline"]
    assert len(inputs) == 1, f"the input was measured {len(inputs)} times: {where}"
    starts = Counter(item.attempt_id.root for item in _decisions(host, StartAttempt))
    assert all(count == 1 for count in starts.values()), f"a workspace opened twice: {starts}"
    assert not host.unsettled, f"workspaces never settled: {host.unsettled}: {where}"
    named = _named(host)
    lost = [
        revision
        for asked, revision in host.made
        if revision in named and not _kept(host, asked, revision)
    ]
    assert not lost, f"candidates neither measured nor kept: {lost}: {where}"
    record = host.record
    assert record is not None
    finished = {item.sequence for item in record.finished}
    assert set(seen.finished) <= finished, f"finished work forgotten: {where}"
    rounds = {item.hypothesis_id: len(item.rounds) for item in record.hypotheses}
    assert all(rounds.get(key, 0) >= count for key, count in seen.rounds.items()), where


# -- stories: the code changes at one point of the run ---------------------------------------------


def _input_in_flight(host: Host) -> bool:
    return any(
        isinstance(item.request, Measure) and not item.request.reuse_only
        for item in host.input_waits()
    )


def _input_decoding(host: Host) -> bool:
    return any(isinstance(item.request, Operation) for item in host.input_waits())


def _implementing(host: Host) -> bool:
    return bool(_waits(host, kind=RequestTurn, label="implementer"))


def _reviewing(host: Host) -> bool:
    return bool(_waits(host, kind=RequestTurn, label="judge"))


def _candidate_measuring(host: Host) -> bool:
    return any(
        isinstance(item.request, Measure)
        and not item.request.reuse_only
        and item.request.plan.purpose != "baseline"
        for item in host.run.open
    )


def _interpreting(host: Host) -> bool:
    return any(
        isinstance(item.request, Operation)
        and isinstance(item.request.request, InterpretEvidence)
        and item.path != INPUT_PATH
        for item in host.run.open
    )


def _settling(host: Host) -> bool:
    return bool(_waits(host, kind=Withdraw, label="settle"))


def _planning(host: Host) -> bool:
    return bool(_waits(host, kind=RequestTurn, label=PLANNER_LABEL)) and bool(
        host.record and host.record.finished
    )


POINTS: dict[str, Callable[[Host], bool]] = {
    "input-measuring": _input_in_flight,
    "input-decoding": _input_decoding,
    "implementing": _implementing,
    "reviewing": _reviewing,
    "candidate-measuring": _candidate_measuring,
    "interpreting": _interpreting,
    "settling": _settling,
    "planning-after-a-round": _planning,
}


@pytest.mark.parametrize("point", list(POINTS))
def test_a_code_change_keeps_the_input_the_candidates_and_the_history(point: str) -> None:
    """Changed at ``point``, the run keeps all it had: answered in the order things were asked."""
    host = _begin(config(max_rounds=3, max_in_flight=2, judge_every=1))
    seen = Seen()
    _play(host, seen, [0], until=POINTS[point])
    host.run.swap(CHANGED)
    _play(host, seen, [0])
    _check_kept_work(host, seen)


def test_a_candidate_measured_before_the_change_is_not_measured_again() -> None:
    host = _begin(config(max_rounds=1, max_in_flight=1))
    seen = Seen()
    _play(host, seen, [0], until=_interpreting)
    host.run.swap(CHANGED)
    _play(host, seen, [0])
    measured = Counter(
        item.plan.candidate for item in _decisions(host, Measure) if item.plan.purpose != "baseline"
    )
    assert measured, host.run.timeline()
    assert all(count == 1 for count in measured.values()), measured
    _check_kept_work(host, seen)


def test_a_reviewed_candidate_is_not_implemented_again_after_a_change() -> None:
    host = _begin(config(max_rounds=1, max_in_flight=1, judge_every=1))
    seen = Seen()
    _play(host, seen, [0], until=_reviewing)
    implemented = len(_decisions(host, RequestTurn))
    host.run.swap(CHANGED)
    _play(host, seen, [0], until=_reviewing)
    turns = _decisions(host, RequestTurn)[implemented:]
    assert [t.decision_id.root for t in turns if "implementer" in t.decision_id.root] == []
    _play(host, seen, [0])
    _check_kept_work(host, seen)


# -- the property: the code changes after generated steps, under a generated answer order ---------


@settings(max_examples=examples(pr=25, nightly=200), deadline=None, derandomize=True, database=None)
@given(
    changes=st.sets(st.integers(0, 70), min_size=1, max_size=3),
    picks=st.lists(st.integers(0, 7), min_size=1, max_size=12),
    width=st.tuples(st.integers(1, 3), st.integers(1, 3), st.integers(1, 2)),
)
def test_code_changes_at_any_steps_keep_the_runs_work(
    changes: set[int], picks: list[int], width: tuple[int, int, int]
) -> None:
    rounds, in_flight, slots = width
    cfg = config(max_rounds=rounds, max_in_flight=in_flight, evaluation_slots=slots, judge_every=1)
    host = _begin(cfg, RestartPolicy(steps=frozenset(changes), changes=(CHANGED, dynamic)))
    seen = Seen()
    try:
        _play(host, seen, picks)
        _check_kept_work(host, seen)
    finally:
        note(host.run.timeline())


@settings(max_examples=examples(pr=15, nightly=100), deadline=None, derandomize=True, database=None)
@given(
    changes=st.sets(st.integers(0, 90), min_size=1, max_size=3),
    picks=st.lists(st.integers(0, 7), min_size=1, max_size=12),
    throughputs=st.dictionaries(
        st.sampled_from([f"c{n}" for n in range(1, 12)]), st.floats(10, 200), max_size=11
    ),
)
def test_code_changes_during_merges_keep_main_and_each_merged_commit(
    changes: set[int], picks: list[int], throughputs: dict[str, float]
) -> None:
    """A run that merges, changed at generated steps, keeps main, its merges and their commits.

    Main resumes where it was and moves only to the commit of a merge that merged; a merger's
    commit is measured at most once, and is measured or its code kept, like a candidate.
    """
    cfg = config(max_rounds=3, max_in_flight=2, judge_every=1)
    policy = RestartPolicy(steps=frozenset(changes), changes=(CHANGED, dynamic))
    host = begin(cfg, restarting=policy, merging=True, throughputs=throughputs)
    seen = Seen(main=MainWatch(throughputs=dict(throughputs)))
    try:
        _play(host, seen, picks)
        _check_kept_work(host, seen)
        record = host.record
        assert record is not None
        commits = [item.revision for item in record.merges if item.revision is not None]
        measured = Counter(
            item.plan.candidate
            for item in _decisions(host, Measure)
            if item.plan.purpose != "baseline"
        )
        assert all(measured[commit] <= 1 for commit in commits), measured
        moved = [item for item in record.merges if item.outcome is MergeOutcome.MERGED]
        assert all(item.revision and measured[item.revision] == 1 for item in moved), record.merges
        event(f"merges: {len(record.merges)}, merged: {len(moved)}")
    finally:
        note(host.run.timeline())


# -- explicit objective policy migration -------------------------------------------------------


def _objective_migration_fixture(
    base_values: tuple[float, ...],
) -> tuple[RuntimeRecord[FlowState], FakeEvidenceLedger]:
    """An in-flight candidate with trusted receipts and a checkpoint, not a settlement."""

    host = begin(config(max_rounds=3), restarting=False)
    state = host.run.state
    root_flow = next(flow for flow in state.live.flows if flow.path == "")
    root = DynamicResume.model_validate_json(root_flow.snapshot_json)
    assert FACTS.task is not None
    terms = FACTS.task.model_copy(
        update={
            "objectives": (
                TaskObjective(name="throughput", direction="max"),
                TaskObjective(name="latency", direction="min"),
            )
        }
    )
    facts = FACTS.model_copy(update={"task": terms, "baseline": BASE})
    start = workstream_start()
    baseline = Measured(
        reading=InputReading(
            accuracy_passed=True,
            benchmark_passed=True,
            metrics=(
                MetricRow(name="throughput", value=100.0, direction="max"),
                MetricRow(name="latency", value=100.0, direction="min"),
            ),
        )
    )
    root = root.model_copy(
        update={
            "record": root.record.model_copy(update={"baseline": baseline, "running": (start,)})
        }
    )
    flows = tuple(
        flow.model_copy(update={"snapshot_json": root.model_dump_json()})
        if flow.path == ""
        else flow
        for flow in state.live.flows
    )
    checkpoint = CheckpointKnown(
        invocation=InvocationRef(
            session_id=SessionId(root="session"),
            invocation_id=InvocationId(root="invocation"),
            generation=0,
        ),
        checkpoint=Retained(revision=CANDIDATE),
    )
    step = state.journal[0].model_copy(update={"event": checkpoint})
    state = state.model_copy(
        update={
            "facts": facts,
            "live": state.live.model_copy(update={"flows": flows}),
            "journal": (*state.journal, step),
        }
    )
    ledger = FakeEvidenceLedger()
    refs = []
    digest = ContentDigest(value="a" * 64)
    rows: list[tuple[RevisionRef, StageKind, float, Literal["baseline", "local-validation"]]] = [
        (BASE, StageKind.BENCHMARK, value, "baseline") for value in base_values
    ]
    rows += [
        (CANDIDATE, StageKind.ACCURACY, 100.0, "local-validation"),
        (CANDIDATE, StageKind.BENCHMARK, 99.0, "local-validation"),
    ]
    for index, (candidate, stage, value, purpose) in enumerate(rows):
        identifier = hashlib.sha256(str(index).encode()).hexdigest()
        ref = evidence_ref(
            identifier,
            EvidenceKind.CORRECTNESS if stage is StageKind.ACCURACY else EvidenceKind.BENCHMARK,
            candidate,
        ).model_copy(update={"purpose": purpose, "scope": Scope(owner=start.attempt, generation=0)})
        record = TrustedEvidence(
            evidence_id=identifier,
            evaluation_id=f"evaluation-{index}",
            stage_name=stage.value,
            kind=stage,
            fingerprints=EvidenceFingerprints(
                candidate=digest, evaluator=digest, workload=digest, environment=digest
            ),
            trusted_inputs=digest,
            outcome=EvidenceOutcome.PASSED,
            accepted_round=0,
            metrics=(
                EvidenceMetric(name="throughput", value=value, direction="max"),
                EvidenceMetric(
                    name="latency", value=60.0 if candidate == CANDIDATE else 100.0, direction="min"
                ),
            ),
        )
        ledger.record(ref.source_request, record, purpose)
        refs.append(ref)
    core = initial_state()
    core = core.model_copy(
        update={
            "run": core.run.model_copy(
                update={"facts": facts, "declaration": host.run.driver.declaration}
            ),
            "evaluation": core.evaluation.model_copy(update={"evidence": tuple(refs)}),
        }
    )
    envelope = RunEnvelope[FlowState](
        schema_version=ENVELOPE_SCHEMA_VERSION,
        fence=HostFence(host_id=HostId(root="writer"), epoch=1),
        strategy_id=host.run.driver.declaration.strategy_id,
        state_schema=host.run.driver.declaration.state_schema,
        core=core,
        strategy=state,
        event_cursor=EventCursor(sequence=0),
    )
    return RuntimeRecord[FlowState].fresh(envelope), ledger


@pytest.mark.regression
@example(values=(100.0,), floor=0.05)
@example(values=(100.0, 110.0), floor=0.05)
@given(
    values=st.lists(st.integers(100, 110).map(float), min_size=1, max_size=4).map(tuple),
    floor=st.sampled_from((0.01, 0.05)),
)
def test_objective_migration_preserves_work_and_reprojects_verified_checkpoints(
    values: tuple[float, ...], floor: float
) -> None:

    before, ledger = _objective_migration_fixture(values)
    migrated = migrate_objective_policy(
        before,
        recorded_objective_readings(before, ledger),
        floor=floor,
        calibration={"throughput": (100.0, 100.0), "latency": (100.0, 100.0)},
        provenance="declared calibration",
    )
    old = DynamicResume.model_validate_json(
        next(flow.snapshot_json for flow in before.envelope.strategy.live.flows if flow.path == "")
    )
    fresh = DynamicResume.model_validate_json(
        next(
            flow.snapshot_json for flow in migrated.envelope.strategy.live.flows if flow.path == ""
        )
    )
    assert migrated.envelope.core.run.receipts == before.envelope.core.run.receipts
    assert migrated.envelope.core.evaluation == before.envelope.core.evaluation
    assert migrated.envelope.strategy.journal == before.envelope.strategy.journal
    assert fresh.record.evaluations == old.record.evaluations
    assert fresh.record.hypotheses == old.record.hypotheses
    terms = migrated.envelope.core.run.facts.task
    assert terms is not None
    policy = terms.objectives[0].noise
    assert policy is not None
    observed = values if len(values) > 1 else (100.0, 100.0)
    mean = sum(observed) / len(observed)
    expected = max(
        floor, sqrt(sum((value - mean) ** 2 for value in observed) / (len(observed) - 1)) / mean
    )
    assert policy.relative_noise == pytest.approx(expected)
    assert tuple(sample.value for sample in policy.baseline_values) == observed
    run = RunRecord.resumed(
        fresh.config,
        migrated.envelope.core.run.facts.baseline,
        terms,
        fresh.record,
        CoreHolds(
            migrated.envelope.core.run.limits.max_attempts,
            migrated.envelope.core.settlement.settlements,
        ),
    )
    parent = run.offered()[0].snapshot
    assert parent.benchmark is not None
    assert (
        space_of(terms).compare_rows(parent.benchmark.metrics, input_metrics(run.baseline))
        is ComparisonVerdict.BETTER
    )
    assert run.world().merge_options.mergeable(parent.hypothesis_id)
    assert any(item.revision == parent.revision for item in run.implemented())
    revised = migrate_objective_policy(
        migrated,
        recorded_objective_readings(migrated, ledger),
        floor=floor,
        calibration={},
        provenance="revised from new observations",
    )
    revised_terms = revised.envelope.core.run.facts.task
    assert revised_terms is not None
    revised_noise = revised_terms.objectives[0].noise
    assert revised_noise is not None
    assert revised_noise.revision == 2
