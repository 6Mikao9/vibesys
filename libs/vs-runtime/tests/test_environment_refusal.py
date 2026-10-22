"""A trusted evaluator's report that its host refused to measure is never the candidate's.

Regression: a task's accuracy checker refused to run because the host lacked free disk
(``Insufficient free disk ... Do not repack or copy weights``) and exited nonzero. The
run read that exit as the accuracy command's verdict on the candidate, failed its round,
and lost a real win. The evaluator protocol gives the task a typed way to say "my host
refused" (``ENVIRONMENT_REFUSED_EXIT_STATUS``); every trusted stage reads it as an
infrastructure failure, which the backend and core measure again within their bounds,
while the candidate's own build cannot claim it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from vs_evaluator_protocol.api import ENVIRONMENT_REFUSED_EXIT_STATUS
from vs_runtime.api import BenchmarkFailureKind
from vs_runtime.api.infrastructure import (
    ProtocolBenchmarkContract,
    RecordState,
    TerminalSignal,
    TrustedBuild,
    TrustedEvaluationExecutor,
    TrustedEvaluationPlan,
    classify,
    create_trusted_evaluation_executor,
    job_failure,
    signal_of,
)
from vs_sandbox.api import CommandResult
from vs_sandbox.api.testing import FakeCommandRunner
from vs_sim.api import DISCARD
from vs_sim.api.testing import InlineBlockingRunner, SimThreads

if TYPE_CHECKING:
    from vs_project.api import GitTracker

_BENCHMARK_RECORDS = (RecordState.ABSENT, RecordState.DECLARED, RecordState.OUTCOME)
"""What a benchmark evaluator can leave; the accuracy command and the build leave none."""
_MARKER = "__VIBESYS_FRAMEWORK_BENCHMARK_JSON__"
_END_MARKER = "__VIBESYS_FRAMEWORK_BENCHMARK_JSON_END__"
_DISK = (
    "Accuracy failed: RuntimeError: ValueError: Insufficient free disk: 24012455936 bytes "
    "available; need 24883658752 bytes (one pack plus 2 GiB margin). Do not repack or copy "
    "weights."
)
_REFUSALS = st.sampled_from(
    (
        _DISK,
        "no Metal device is available",
        "GPU lock is held by another evaluation",
        "required toolchain cmake is not installed",
        "",
    )
)


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


def _accuracy(output: str, exit_code: int) -> BenchmarkFailureKind | None:
    sandbox = FakeCommandRunner(default_result=CommandResult(output=output, exit_code=exit_code))
    result = asyncio.run(
        _executor(TrustedEvaluationPlan(accuracy_command="check"), sandbox).accuracy(output=DISCARD)
    )
    assert not result.passed
    return result.failure_kind


def _benchmark(output: str, exit_code: int, *, record: RecordState) -> BenchmarkFailureKind | None:
    records = {
        RecordState.ABSENT: "",
        RecordState.DECLARED: '{"kind":"hello","protocol":2,"metrics":{"tps":{}}}',
        RecordState.OUTCOME: '{"kind":"error","message":"refused"}',
    }[record]
    framed = f"{output}\n{_MARKER}\n{records}\n{_END_MARKER}"
    sandbox = FakeCommandRunner(default_result=CommandResult(output=framed, exit_code=exit_code))
    plan = TrustedEvaluationPlan(
        benchmark_command="bench", benchmark_contract=ProtocolBenchmarkContract()
    )
    result = asyncio.run(_executor(plan, sandbox).benchmark(output=DISCARD))
    assert not result.passed
    return result.failure_kind


@pytest.mark.regression
@given(output=_REFUSALS)
@example(output=_DISK)
def test_an_accuracy_check_whose_host_refused_is_retried_not_charged_to_the_candidate(
    output: str,
) -> None:
    kind = _accuracy(output, ENVIRONMENT_REFUSED_EXIT_STATUS)

    assert kind is BenchmarkFailureKind.INFRASTRUCTURE
    assert job_failure([kind]).retryable


@pytest.mark.regression
@given(output=_REFUSALS, record=st.sampled_from(_BENCHMARK_RECORDS))
@example(output=_DISK, record=RecordState.OUTCOME)
def test_a_benchmark_whose_host_refused_is_retried_whatever_it_wrote(
    output: str, record: RecordState
) -> None:
    assert (
        _benchmark(output, ENVIRONMENT_REFUSED_EXIT_STATUS, record=record)
        is BenchmarkFailureKind.INFRASTRUCTURE
    )


@given(output=_REFUSALS, exit_code=st.integers(min_value=1, max_value=255))
def test_an_accuracy_check_that_failed_for_any_other_reason_is_the_candidates(
    output: str, exit_code: int
) -> None:
    kind = _accuracy(output, exit_code)

    if exit_code == ENVIRONMENT_REFUSED_EXIT_STATUS:
        assert kind is BenchmarkFailureKind.INFRASTRUCTURE
    else:
        # The refusal is the exit status, never the wording: the gate is not loosened.
        assert kind is BenchmarkFailureKind.WORKLOAD


@given(output=_REFUSALS)
def test_a_candidate_build_cannot_declare_its_host_refused(output: str) -> None:
    confinement = FakeCommandRunner(
        default_result=CommandResult(output=output, exit_code=ENVIRONMENT_REFUSED_EXIT_STATUS)
    )
    plan = TrustedEvaluationPlan(
        build=TrustedBuild(command="make", timeout_seconds=60, outputs=("lib.so",)),
        accuracy_command="check",
    )
    result = asyncio.run(
        _executor(plan, FakeCommandRunner()).build(confinement=confinement, output=DISCARD)
    )

    assert result.failure_kind is BenchmarkFailureKind.WORKLOAD


@given(record=st.sampled_from(RecordState), trusted=st.booleans())
def test_the_refusal_status_is_infrastructure_exactly_for_a_trusted_evaluator(
    record: RecordState, *, trusted: bool
) -> None:
    signal = signal_of(ENVIRONMENT_REFUSED_EXIT_STATUS, trusted_evaluator=trusted)

    assert (signal is TerminalSignal.ENVIRONMENT_REFUSED) is trusted
    assert (classify(signal, record) is BenchmarkFailureKind.INFRASTRUCTURE) is trusted
