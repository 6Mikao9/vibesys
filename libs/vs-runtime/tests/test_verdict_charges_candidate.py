"""Only a verdict about the candidate charges the candidate; the environment never does.

Regression: a trusted stage the clock or the machinery stopped (an accuracy command or an
evaluator that hit its time limit, a build the run environment could not confine, a task
that declares a benchmark contract and no command) was read as the candidate failing the
stage, which cost the attempt and its eligibility. A stage judges the candidate only when
the evaluator ran the candidate's measurement and judged it: it wrote its own result, or the
stage has no result file and its exit status is the judgement. Everything else is the
machinery's or ambiguous, and both are measured again and charge nobody.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from vs_core.api import MeasurementFailure
from vs_evaluator_protocol.api import ENVIRONMENT_REFUSED_EXIT_STATUS
from vs_runtime.api import BenchmarkFailureKind
from vs_runtime.api.infrastructure import (
    ProtocolBenchmarkContract,
    RecordState,
    TrustedBuild,
    TrustedEvaluationExecutor,
    TrustedEvaluationPlan,
    create_trusted_evaluation_executor,
    job_failure,
)
from vs_sandbox.api import CommandResult
from vs_sandbox.api.testing import FakeCommandRunner
from vs_sim.api import DISCARD
from vs_sim.api.testing import InlineBlockingRunner, SimThreads

if TYPE_CHECKING:
    from vs_project.api import GitTracker

_MARKER = "__VIBESYS_FRAMEWORK_BENCHMARK_JSON__"
_END_MARKER = "__VIBESYS_FRAMEWORK_BENCHMARK_JSON_END__"
_TIMEOUT = 124
_SIGKILL = 137
# A status that stops a stage without judging it; 75 is the evaluator's own declaration.
_STOPPED = (_TIMEOUT, _SIGKILL)
_JUDGING = st.integers(min_value=1, max_value=255).filter(
    lambda code: code not in (*_STOPPED, ENVIRONMENT_REFUSED_EXIT_STATUS)
)
_EXITS = st.none() | st.integers(min_value=-9, max_value=255)
_FAULT = BenchmarkFailureKind.INFRASTRUCTURE


class _Git:
    def trusted_input_changes(self) -> list[str]:
        return []


def _executor(plan: TrustedEvaluationPlan, sandbox: FakeCommandRunner) -> TrustedEvaluationExecutor:
    return create_trusted_evaluation_executor(
        plan,
        workspace=Path("/workspace"),
        sandbox=sandbox,
        git=cast("GitTracker", _Git()),
        threads=SimThreads(),
        blocking=InlineBlockingRunner(),
    )


def _result(exit_code: int | None, *, cancelled: bool = False) -> CommandResult:
    return CommandResult(output="out", exit_code=exit_code, cancelled=cancelled)


def _accuracy(exit_code: int | None, *, cancelled: bool = False) -> BenchmarkFailureKind:
    sandbox = FakeCommandRunner(default_result=_result(exit_code, cancelled=cancelled))
    plan = TrustedEvaluationPlan(accuracy_command="check")
    result = asyncio.run(_executor(plan, sandbox).accuracy(output=DISCARD))
    assert result.failure_kind is not None
    return result.failure_kind


def _build(exit_code: int | None) -> BenchmarkFailureKind:
    plan = TrustedEvaluationPlan(build=TrustedBuild(command="make", timeout_seconds=60))
    confinement = FakeCommandRunner(default_result=_result(exit_code))
    result = asyncio.run(
        _executor(plan, FakeCommandRunner()).build(confinement=confinement, output=DISCARD)
    )
    assert result.failure_kind is not None
    return result.failure_kind


def _benchmark(exit_code: int | None, record: RecordState) -> BenchmarkFailureKind:
    written = {
        RecordState.ABSENT: "",
        RecordState.DECLARED: '{"kind":"hello","protocol":2,"metrics":{"tps":{}}}',
        RecordState.OUTCOME: '{"kind":"error","message":"slow"}',
    }[record]
    framed = f"out\n{_MARKER}\n{written}\n{_END_MARKER}"
    sandbox = FakeCommandRunner(default_result=CommandResult(output=framed, exit_code=exit_code))
    plan = TrustedEvaluationPlan(
        benchmark_command="bench", benchmark_contract=ProtocolBenchmarkContract()
    )
    result = asyncio.run(_executor(plan, sandbox).benchmark(output=DISCARD))
    assert result.failure_kind is not None
    return result.failure_kind


@pytest.mark.regression
@given(exit_code=_EXITS)
@example(exit_code=_TIMEOUT)
@example(exit_code=_SIGKILL)
def test_an_accuracy_command_charges_the_candidate_only_with_its_own_exit_status(
    exit_code: int | None,
) -> None:
    if exit_code in (0, None) or exit_code < 0:
        return  # a pass has no failure; a stage with no status is `test_a_stage_that_...`
    kind = _accuracy(exit_code)
    judged = exit_code in range(1, 256) and exit_code not in (*_STOPPED, 75)
    assert (kind is BenchmarkFailureKind.WORKLOAD) == judged


@pytest.mark.regression
@given(exit_code=_EXITS)
@example(exit_code=_TIMEOUT)
def test_a_candidate_build_charges_the_candidate_only_with_its_own_exit_status(
    exit_code: int | None,
) -> None:
    if exit_code in (0, None) or exit_code < 0:
        return
    kind = _build(exit_code)
    assert (kind is BenchmarkFailureKind.WORKLOAD) == (exit_code not in _STOPPED)


@pytest.mark.regression
@given(
    exit_code=st.integers(min_value=0, max_value=255),
    record=st.sampled_from((RecordState.ABSENT, RecordState.DECLARED)),
)
@example(exit_code=_TIMEOUT, record=RecordState.DECLARED)
def test_an_evaluator_that_declared_and_was_stopped_judged_nothing(
    exit_code: int, record: RecordState
) -> None:
    kind = _benchmark(exit_code, record)
    if exit_code in _STOPPED:
        assert kind is BenchmarkFailureKind.AMBIGUOUS
    if record is RecordState.ABSENT:
        assert kind is not BenchmarkFailureKind.WORKLOAD


@given(exit_code=st.integers(min_value=0, max_value=255).filter(lambda c: c != 75))
def test_an_evaluator_that_wrote_its_verdict_is_charged_whatever_stopped_it(
    exit_code: int,
) -> None:
    assert _benchmark(exit_code, RecordState.OUTCOME) is BenchmarkFailureKind.WORKLOAD


@pytest.mark.regression
@example(cancelled=False)
@given(cancelled=st.booleans())
def test_a_stage_that_reported_no_exit_status_is_the_machinerys(*, cancelled: bool) -> None:
    assert _accuracy(None, cancelled=cancelled) is _FAULT
    assert _build(-1) is _FAULT
    for record in RecordState.ABSENT, RecordState.DECLARED, RecordState.OUTCOME:
        assert _benchmark(-1, record) is _FAULT


@pytest.mark.regression
def test_a_setup_the_run_cannot_make_is_never_the_candidates() -> None:
    plan = TrustedEvaluationPlan(build=TrustedBuild(command="make", timeout_seconds=60))
    unconfined = asyncio.run(
        _executor(plan, FakeCommandRunner()).build(confinement=None, output=DISCARD)
    )
    assert unconfined.failure_kind is _FAULT
    no_command = TrustedEvaluationPlan(benchmark_contract=ProtocolBenchmarkContract())
    benchmark = asyncio.run(_executor(no_command, FakeCommandRunner()).benchmark(output=DISCARD))
    assert benchmark.failure_kind is _FAULT


@given(failure=st.sampled_from(MeasurementFailure))
def test_only_a_workload_failure_charges_the_candidate(failure: MeasurementFailure) -> None:
    assert failure.charges_candidate == (failure is MeasurementFailure.WORKLOAD)


@given(
    kinds=st.lists(st.none() | st.sampled_from(BenchmarkFailureKind), max_size=6),
)
def test_a_job_charges_the_candidate_only_when_every_failed_stage_judged_it(
    kinds: list[BenchmarkFailureKind | None],
) -> None:
    """The class of a failed job is its stages' worst: one stage the machinery, the clock or an
    unclassified cause stopped means no verdict about the candidate."""
    judged = bool(kinds) and all(kind is BenchmarkFailureKind.WORKLOAD for kind in kinds)

    assert job_failure(kinds).charges_candidate == judged
