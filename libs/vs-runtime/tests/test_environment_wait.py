"""A host that refuses a measurement delays it; it never decides it.

Regression: a task's accuracy gate refused because the host's free disk fluctuated with swap
(``Insufficient free disk``). The backend measured the candidate again at once, a bounded
number of times, and then reported the last refusal as the measurement's result; the search
settled the candidate as failed and lost it, the most valuable one of its run. A host's
refusal is no result and no try: the backend holds the measurement, reported as queued with
the refusal as its reason, runs it again after a backoff, and ends it only with the
candidate's own result (or a cancel), however often and whenever the host refuses.

The properties are metamorphic: putting refusals anywhere into the runs of a measurement
changes none of its outcome, whatever the clock does between polls and wherever the host
restarts.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from vs_core.api import MAX_SUBMISSIONS_PER_MEASUREMENT
from vs_evaluation.api import (
    AvailabilitySnapshot,
    AvailabilityState,
    ContentDigest,
    CostClass,
    EvaluationRequest,
    EvaluationState,
    EvaluationStep,
    EvaluationStepResult,
    EvidenceFailureKind,
    EvidenceFingerprints,
    EvidenceKind,
    EvidenceOutcome,
    ExecutorObservation,
    ExecutorPoll,
    PollPhase,
    ResourceRequirements,
    ReuseStatus,
    SemanticEvaluationStage,
    StageState,
    TrustedEvidence,
)
from vs_evaluation.api.testing import InMemoryEvaluationNamespace
from vs_evaluator_protocol.api import ENVIRONMENT_REFUSED_EXIT_STATUS
from vs_runtime.api import (
    ENVIRONMENT_WAIT_PREFIX,
    AccuracyEvaluation,
    EvaluationRetries,
    PollingEvaluationExecutor,
    RetryingEvaluationExecutor,
    environment_refusal,
)
from vs_runtime.api.infrastructure import RecordState, classify, signal_of
from vs_runtime.api.testing import FakeEvaluation, FakeWorkspace, FakeWorkspaces
from vs_sim.api.testing import ManualClock, yield_until

_DIGEST = ContentDigest(value="a" * 64)
_DISK = (
    "Framework accuracy gate failed.\n[ 93%] Building OBJCXX object runtime/native.mm.o\n"
    "Accuracy failed: RuntimeError: ValueError: Insufficient free disk: 22083891200 bytes "
    "available; need 24883658752 bytes (one pack plus 2 GiB margin). Do not repack or copy "
    "weights."
)
_DISK_REASON = _DISK.splitlines()[-1]
_HANDLE = "h"
_GUARD = 5_000
"""Polls after which a measurement that never ended is a hang, not a slow host."""
_BACKOFF = 30.0
_CAP = 300.0


class Run(StrEnum):
    """How one run of a measurement ends."""

    REFUSED = "refused"
    INFRASTRUCTURE = "infrastructure"
    AMBIGUOUS = "ambiguous"
    WORKLOAD = "workload"
    SUCCEEDED = "succeeded"


_COUNTED = (Run.INFRASTRUCTURE, Run.AMBIGUOUS)
_ENDING = (Run.WORKLOAD, Run.SUCCEEDED)


_KINDS = {
    Run.INFRASTRUCTURE: EvidenceFailureKind.INFRASTRUCTURE,
    Run.AMBIGUOUS: EvidenceFailureKind.AMBIGUOUS,
    Run.WORKLOAD: EvidenceFailureKind.WORKLOAD,
}


def _retries(infrastructure: int, ambiguous: int) -> EvaluationRetries:
    return EvaluationRetries(
        infrastructure_attempts=infrastructure,
        ambiguous_attempts=min(ambiguous, infrastructure),
        environment_backoff=_BACKOFF,
        environment_backoff_cap=_CAP,
    )


def _terminal(run: Run) -> ExecutorObservation:
    if run is Run.SUCCEEDED:
        return ExecutorObservation(state=EvaluationState.SUCCEEDED)
    kind = EvidenceFailureKind.ENVIRONMENT_REFUSED if run is Run.REFUSED else _KINDS[run]
    evidence = TrustedEvidence(
        evidence_id=hashlib.sha256(run.value.encode()).hexdigest(),
        evaluation_id="evaluation",
        stage_name="accuracy",
        kind=EvidenceKind.ACCURACY,
        fingerprints=EvidenceFingerprints(
            candidate=_DIGEST, evaluator=_DIGEST, workload=_DIGEST, environment=_DIGEST
        ),
        trusted_inputs=_DIGEST,
        outcome=EvidenceOutcome.FAILED,
        failure_kind=kind,
        accepted_round=0,
    )
    text = _DISK if run is Run.REFUSED else "accuracy failed"
    return ExecutorObservation(
        state=EvaluationState.FAILED,
        failure=text,
        stage_results=(
            EvaluationStepResult(
                name="accuracy",
                state=StageState.FAILED,
                failure=text,
                result=evidence.model_dump(mode="json"),
            ),
        ),
    )


@dataclass
class ScriptedExecutor:
    """A Fake executor whose n-th submitted run ends as the script says (the last repeats)."""

    script: tuple[Run, ...]
    clock: ManualClock
    started: list[tuple[str, float]] = field(default_factory=list)
    cancelled: list[str] = field(default_factory=list)
    _ends: dict[str, Run] = field(default_factory=dict)

    @property
    def runs(self) -> tuple[Run, ...]:
        """The ends of the runs submitted so far, in order."""
        return tuple(self._ends[handle] for handle, _ in self.started)

    async def availability(self, requirements: ResourceRequirements) -> AvailabilitySnapshot:
        del requirements
        return AvailabilitySnapshot(
            state=AvailabilityState.IMMEDIATE,
            in_flight=0,
            queue_depth=0,
            reuse_status=ReuseStatus.NONE,
            cost_class=CostClass.FREE,
            observed_at=0.0,
            fresh_for_s=1.0,
        )

    async def submit(self, request: EvaluationRequest, *, handle_id: str) -> None:
        del request
        if handle_id not in self._ends:
            self.started.append((handle_id, self.clock.now()))
            self._ends[handle_id] = self.script[min(len(self._ends), len(self.script) - 1)]

    async def poll(self, handle_id: str) -> ExecutorPoll:
        run = self._ends.get(handle_id)
        if run is None:
            return ExecutorPoll(phase=PollPhase.UNSUBMITTED)
        return ExecutorPoll(phase=PollPhase.ENDED, terminal=_terminal(run))

    async def inspect_only(self, handle_id: str) -> ExecutorObservation | None:
        return await self.inspect(handle_id)

    async def inspect(self, handle_id: str) -> ExecutorObservation | None:
        run = self._ends.get(handle_id)
        return None if run is None else _terminal(run)

    async def wait_for_change(self, handle_id: str, timeout_s: float) -> None:
        del handle_id, timeout_s

    async def cancel(self, handle_id: str) -> None:
        self.cancelled.append(handle_id)

    async def close(self) -> None:
        return None


def _request() -> EvaluationRequest:
    return EvaluationRequest(key="measure", stages=(EvaluationStep(name="accuracy", payload={}),))


@dataclass
class Measured:
    """What a driven measurement showed: every poll, and how the inner executor was used."""

    polls: list[tuple[float, ExecutorPoll]]
    inner: ScriptedExecutor

    @property
    def terminal(self) -> ExecutorObservation:
        last = self.polls[-1][1].terminal
        assert last is not None, "the measurement never ended"
        return last

    @property
    def counted_runs(self) -> tuple[Run, ...]:
        """The runs submitted that were no refusal, in order."""
        return tuple(run for run in self.inner.runs if run is not Run.REFUSED)


async def _drive(
    script: tuple[Run, ...],
    retries: EvaluationRetries,
    steps: tuple[float, ...],
    *,
    restarts: frozenset[int] = frozenset(),
    cancel_at: int | None = None,
) -> Measured:
    """Poll one measurement to its end, moving the clock by ``steps`` (cycled) between polls.

    The host restarts (a new wrapper over the same namespace, executor and clock) before the
    polls named in ``restarts``; the measurement is cancelled before poll ``cancel_at``.
    """
    clock = ManualClock()
    inner = ScriptedExecutor(script, clock)
    namespace = InMemoryEvaluationNamespace()

    def host() -> RetryingEvaluationExecutor:
        return RetryingEvaluationExecutor(inner, retries, namespace, clock=clock)

    executor = host()
    await executor.submit(_request(), handle_id=_HANDLE)
    polls: list[tuple[float, ExecutorPoll]] = []
    for index in range(_GUARD):
        if index in restarts:
            executor = host()
        if index == cancel_at:
            await executor.cancel(_HANDLE)
        polled = await executor.poll(_HANDLE)
        polls.append((clock.now(), polled))
        if polled.terminal is not None:
            return Measured(polls, inner)
        clock.advance(steps[index % len(steps)])
    message = "the measurement never ended"
    raise AssertionError(message)


def _measure(
    script: tuple[Run, ...],
    retries: EvaluationRetries,
    *,
    steps: tuple[float, ...] = (0.0, 1.0, 7.0, _CAP),
    restarts: frozenset[int] = frozenset(),
    cancel_at: int | None = None,
) -> Measured:
    return asyncio.run(_drive(script, retries, steps, restarts=restarts, cancel_at=cancel_at))


_BOUNDS = st.integers(min_value=1, max_value=MAX_SUBMISSIONS_PER_MEASUREMENT)
_RETRIES = st.tuples(_BOUNDS, _BOUNDS).map(lambda pair: _retries(max(pair), min(pair)))
_STEPS = st.lists(st.sampled_from((0.0, 0.5, 1.0, 7.0, 29.0, 61.0, 1_000.0)), max_size=5).map(
    lambda steps: (*steps, _CAP)
)
_COUNTED_RUNS = st.lists(st.sampled_from((*_COUNTED, *_ENDING)), min_size=1, max_size=6).map(tuple)
_MAX_REFUSALS = 12


@st.composite
def _with_refusals(draw: st.DrawFn) -> tuple[tuple[Run, ...], tuple[Run, ...]]:
    """A script of counted runs, and the same script with refusals put anywhere into it."""
    counted = draw(_COUNTED_RUNS)
    refused = list(counted)
    for _ in range(draw(st.integers(min_value=1, max_value=_MAX_REFUSALS))):
        # never after the last run: the script's last run repeats, and a host that refused
        # forever would be a different measurement
        refused.insert(draw(st.integers(min_value=0, max_value=len(refused) - 1)), Run.REFUSED)
    return counted, tuple(refused)


@pytest.mark.regression
@given(scripts=_with_refusals(), retries=_RETRIES, steps=_STEPS)
@example(
    scripts=((Run.SUCCEEDED,), (Run.REFUSED, Run.REFUSED, Run.REFUSED, Run.SUCCEEDED)),
    retries=_retries(1, 1),
    steps=(0.0, _CAP),
)
def test_refusals_put_anywhere_into_a_measurement_change_none_of_its_outcome(
    scripts: tuple[tuple[Run, ...], tuple[Run, ...]],
    retries: EvaluationRetries,
    steps: tuple[float, ...],
) -> None:
    counted, refused = scripts

    plain = _measure(counted, retries, steps=steps)
    interleaved = _measure(refused, retries, steps=steps)

    # the host's refusals cost the measurement no try and no outcome
    assert interleaved.terminal == plain.terminal
    assert interleaved.counted_runs == plain.counted_runs
    # and none of them was ever reported as the measurement's end
    assert all(
        polled.terminal is None or environment_refusal(polled.terminal) is None
        for _, polled in interleaved.polls
    )


@given(scripts=_with_refusals(), retries=_RETRIES, steps=_STEPS)
def test_a_measurement_its_host_refuses_is_queued_with_the_refusal_as_its_reason(
    scripts: tuple[tuple[Run, ...], tuple[Run, ...]],
    retries: EvaluationRetries,
    steps: tuple[float, ...],
) -> None:
    _, refused = scripts

    measured = _measure(refused, retries, steps=steps)

    waiting = [polled for _, polled in measured.polls if polled.pending_reason is not None]
    assert bool(waiting) == (Run.REFUSED in measured.inner.runs)
    assert all(polled.phase is PollPhase.QUEUED for polled in waiting)
    assert all(
        polled.pending_reason == ENVIRONMENT_WAIT_PREFIX + _DISK_REASON for polled in waiting
    )


@given(scripts=_with_refusals(), retries=_RETRIES, steps=_STEPS)
def test_a_refused_measurement_runs_again_only_after_its_backoff(
    scripts: tuple[tuple[Run, ...], tuple[Run, ...]],
    retries: EvaluationRetries,
    steps: tuple[float, ...],
) -> None:
    _, refused = scripts

    measured = _measure(refused, retries, steps=steps)

    refusals = 0
    for (_, earlier), (_, later), run in zip(
        measured.inner.started, measured.inner.started[1:], measured.inner.runs, strict=False
    ):
        if run is Run.REFUSED:
            refusals += 1
            assert later - earlier >= retries.environment_delay(refusals)


@given(
    scripts=_with_refusals(),
    retries=_RETRIES,
    steps=_STEPS,
    restarts=st.sets(st.integers(min_value=0, max_value=40), max_size=6),
)
def test_a_host_that_restarts_anywhere_in_the_wait_measures_the_same(
    scripts: tuple[tuple[Run, ...], tuple[Run, ...]],
    retries: EvaluationRetries,
    steps: tuple[float, ...],
    restarts: set[int],
) -> None:
    _, refused = scripts

    steady = _measure(refused, retries, steps=steps)
    restarted = _measure(refused, retries, steps=steps, restarts=frozenset(restarts))

    assert restarted.terminal == steady.terminal
    assert restarted.inner.runs == steady.inner.runs


@given(
    refusals=st.integers(min_value=1, max_value=_MAX_REFUSALS),
    retries=_RETRIES,
    at=st.integers(min_value=1, max_value=20),
)
def test_a_measurement_cancelled_while_its_host_refuses_ends_cancelled_and_runs_no_more(
    refusals: int, retries: EvaluationRetries, at: int
) -> None:
    script = (*(Run.REFUSED,) * refusals, Run.SUCCEEDED)

    # the clock stands still, so the host's first refusal holds the measurement until the cancel
    measured = _measure(script, retries, steps=(0.0,), cancel_at=at)

    assert measured.terminal.state is EvaluationState.CANCELED
    assert len(measured.polls) == at + 1
    assert measured.inner.runs == (Run.REFUSED,)


@given(
    retries=_RETRIES,
    refusals=st.integers(min_value=1, max_value=10_000_000),
)
def test_the_backoff_grows_from_its_first_wait_to_its_cap_and_stays_finite(
    retries: EvaluationRetries, refusals: int
) -> None:
    delay = retries.environment_delay(refusals)

    assert retries.environment_delay(1) == _BACKOFF
    assert _BACKOFF <= delay <= _CAP
    assert delay <= retries.environment_delay(refusals + 1)


# -- the same through the product's executors --------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.regression
@given(refusals=st.integers(min_value=1, max_value=6))
@example(refusals=2)
async def test_a_gate_whose_host_refuses_the_accuracy_check_is_measured_when_the_host_recovers(
    refusals: int,
) -> None:
    """The gate's exit status 75 reaches the backend as it is read: the candidate is measured."""
    refused = classify(
        signal_of(ENVIRONMENT_REFUSED_EXIT_STATUS, trusted_evaluator=True), RecordState.EXIT_STATUS
    )
    evaluation = FakeEvaluation()
    evaluation.script_accuracy(
        *(
            AccuracyEvaluation(
                executed=True,
                feedback=_DISK,
                operator_feedback=_DISK,
                failure_kind=refused,
            )
            for _ in range(refusals)
        ),
        AccuracyEvaluation(executed=True),
    )
    workspaces = FakeWorkspaces(
        FakeWorkspace(path=Path("/workspace")), supports_parallel_candidates=True
    )
    clock = ManualClock()
    # one try only: any refusal counted as a failed try would end the measurement
    executor = RetryingEvaluationExecutor(
        PollingEvaluationExecutor(evaluation, workspaces, clock=clock),
        _retries(1, 1),
        InMemoryEvaluationNamespace(),
        clock=clock,
    )
    snapshot = await workspaces.root.snapshot("candidate")
    digest = ContentDigest.sha256(b"same")
    stage = SemanticEvaluationStage(
        snapshot=snapshot,
        kind=EvidenceKind.ACCURACY,
        fingerprints=EvidenceFingerprints(
            candidate=digest, evaluator=digest, workload=digest, environment=digest
        ),
    )
    request = EvaluationRequest(
        key="measure",
        stages=(EvaluationStep(name="accuracy", payload=stage.model_dump(mode="json")),),
    )
    await executor.submit(request, handle_id=_HANDLE)

    async def settled() -> ExecutorPoll:
        polled = await executor.poll(_HANDLE)
        if polled.terminal is None and polled.pending_reason is not None:
            clock.advance(_CAP)
        return polled

    ended = await yield_until(
        settled, lambda polled: polled.phase is PollPhase.ENDED, "the measurement", turns=5_000
    )

    assert ended.terminal is not None
    assert ended.terminal.state is EvaluationState.SUCCEEDED
    assert len(evaluation.accuracy_calls) == refusals + 1
