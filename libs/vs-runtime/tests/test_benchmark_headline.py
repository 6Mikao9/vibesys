"""A benchmark record's first metric is the headline the task declared, not the first name.

``_reading`` takes ``metrics[0]``. The in-process executor used to sort every name, so a
multi-metric row headlined ``completed_requests`` when the task's first objective was
``tpot_ms_avg``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from vs_evaluation.api import (
    ContentDigest,
    EvaluationRequest,
    EvaluationState,
    EvaluationStep,
    EvidenceFingerprints,
    EvidenceKind,
    SemanticEvaluationStage,
    TrustedEvidence,
)
from vs_runtime.api import (
    BenchmarkEvaluation,
    MetricDirection,
    PollingEvaluationExecutor,
)
from vs_runtime.api.testing import FakeEvaluation, FakeWorkspace, FakeWorkspaces
from vs_sim.api.testing import ManualClock, VirtualClock, examples, run_virtual, yield_until

_HANDLE = "headline"
_FLINT_NAMES = (
    "completed_requests",
    "decode_tokens_per_second",
    "scored_duration_s",
    "startup_duration_s",
    "timed_out_requests",
    "tpot_ms_avg",
    "ttft_ms_avg",
)
_NAME = st.from_regex(r"[a-z][a-z0-9_]{0,16}", fullmatch=True)


def _request(snapshot: str) -> EvaluationRequest:
    digest = ContentDigest.sha256(b"same")
    fingerprints = EvidenceFingerprints(
        candidate=digest, evaluator=digest, workload=digest, environment=digest
    )
    stage = SemanticEvaluationStage(
        snapshot=snapshot, kind=EvidenceKind.BENCHMARK, fingerprints=fingerprints
    )
    return EvaluationRequest(
        key=_HANDLE,
        stages=(EvaluationStep(name="benchmark", payload=stage.model_dump(mode="json")),),
    )


async def _headline(names: tuple[str, ...], headline: str) -> str:
    evaluation = FakeEvaluation(run_id="run-1")
    evaluation.benchmark_results.append(
        BenchmarkEvaluation(
            executed=True,
            metric_name=headline,
            metric_value=53.53,
            metric_direction=MetricDirection.MINIMIZE,
            row=dict.fromkeys(names, 1.0),
        )
    )
    workspaces = FakeWorkspaces(
        FakeWorkspace(path=Path("/workspace")), supports_parallel_candidates=True
    )
    executor = PollingEvaluationExecutor(evaluation, workspaces, clock=ManualClock())
    snapshot = await workspaces.root.snapshot("candidate")
    await executor.submit(_request(snapshot), handle_id=_HANDLE)
    observed = await yield_until(
        lambda: executor.inspect(_HANDLE),
        lambda seen: seen is not None and seen.state is EvaluationState.SUCCEEDED,
        "the benchmark to finish",
    )
    await executor.close()
    assert observed is not None
    evidence = TrustedEvidence.model_validate(observed.stage_results[0].result)
    assert evidence.metrics
    return evidence.metrics[0].name


@pytest.mark.regression
@example(names=_FLINT_NAMES, headline="tpot_ms_avg")
@settings(max_examples=examples(pr=8, nightly=40))
@given(
    names=st.lists(_NAME, min_size=2, max_size=6, unique=True).map(tuple),
    headline=_NAME,
)
def test_the_declared_headline_leads_the_benchmark_record(
    names: tuple[str, ...], headline: str
) -> None:
    """The reported Flint row headlined ``tpot_ms_avg``, which is not the first name."""
    if headline not in names or headline == sorted(names)[0]:
        headline = next(name for name in names if name != sorted(names)[0])
    assert run_virtual(VirtualClock(), _headline(names, headline)) == headline
