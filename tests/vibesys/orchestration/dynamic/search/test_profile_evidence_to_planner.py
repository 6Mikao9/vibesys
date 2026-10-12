"""A finished profile reaches the planner as typed evidence, and only a real failure reads as one.

Live defect: four rocprofv3 captures passed (every required field measured, exit 0), yet each
later planner prompt said "Why it failed: profile produced no trusted evidence" and showed only
the head of the capture log, so the planner profiled again. These properties render the planner
prompt from generated profile results (passed, failed, finished with no reading, running).
"""

from __future__ import annotations

import string
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st
from tests.vibesys.domain_sections import EMPTY_DOMAIN_SECTIONS
from tests.vibesys.orchestration.dynamic.search._views import evidence_ref, revision

import vibesys.orchestration.dynamic.core_policy.prompts as templates
from vibesys.orchestration.dynamic.core_policy.api import run_prompt_variables

# test-isolation: PlanWorld is the search's internal projection of its state into the planner's
# typed context; no public entry accepts a hand-built finished workstream, and the production
# shell cannot script a profile that finished with no reading or one with arbitrary statistics.
from vibesys.orchestration.dynamic.search._context import PlanWorld
from vibesys.orchestration.dynamic.search._values import (
    Failure,
    FailureKind,
    FinishedWork,
    Proof,
    WorkKind,
    WorkPlan,
)
from vibesys.orchestration.dynamic.search._workstream import Profiling, Workstream
from vibesys.orchestration.dynamic.search.api import (
    AcceptedReading,
    DynamicConfig,
    MetricRow,
    PlannerPrompt,
    ProfileHistoryRow,
    ProfileHistoryStatus,
)
from vibesys.prompts import CORE_PROTOCOL_DIR
from vibesys.candidates.api import NotConfigured
from vs_core.api import (
    ArtifactId,
    ArtifactRef,
    AttemptId,
    EvidenceKind,
    ObservationStatus,
)
from vs_prompts.api import TemplateRenderer

FAILURE_TEXT = "the trusted check produced no usable evidence"
_WORDS = string.ascii_letters + string.digits + " "
_CONFIG = DynamicConfig(
    recipe=ArtifactRef(artifact_id=ArtifactId(root="recipe"), digest="recipe"),
    max_rounds=3,
    profiling=True,
)
_RENDERER = TemplateRenderer(Path(templates.__file__).parent, fallback_roots=(CORE_PROTOCOL_DIR,))


def _text(prompt: PlannerPrompt) -> str:
    variables = {name: getattr(prompt, name) for name in type(prompt).model_fields}
    variables.pop("template")
    return _RENDERER.render_template(
        "portfolio.j2",
        **variables,
        **run_prompt_variables(
            domain_sections=EMPTY_DOMAIN_SECTIONS,
            objective="Raise throughput.",
            environment_notes="",
            agent_evaluation=False,
        ),
    )


def _metrics() -> st.SearchStrategy[tuple[MetricRow, ...]]:
    row = st.builds(
        MetricRow,
        name=st.text(string.ascii_lowercase, min_size=3, max_size=10),
        value=st.floats(min_value=0, max_value=1000, allow_nan=False, allow_infinity=False),
        direction=st.sampled_from(["max", "min"]),
        unit=st.sampled_from(["fraction", "ms"]),
    )
    return st.lists(row, max_size=6, unique_by=lambda item: item.name).map(tuple)


def _plan(required_fields: tuple[str, ...]) -> WorkPlan:
    return WorkPlan(
        kind=WorkKind.PROFILE,
        work_id="prof-1",
        question="Where does the time go?",
        decision_impact="Pick the next kernel to optimize.",
        required_fields=required_fields,
    )


def _world(*, finished: tuple[FinishedWork, ...], running: tuple[Workstream, ...]) -> PlanWorld:
    return PlanWorld(
        config=_CONFIG,
        base=revision("parent"),
        baseline=NotConfigured(),
        hypotheses=(),
        running=running,
        finished=finished,
        refunded=0,
        offered=(),
    )


def _finished(
    *,
    reading: AcceptedReading | None,
    failure: Failure | None = None,
    required_fields: tuple[str, ...] = (),
    artifacts: tuple[ArtifactRef, ...] = (),
) -> PlannerPrompt:
    ref = evidence_ref("profile-evidence", EvidenceKind.PROFILING, revision("parent")).model_copy(
        update={"purpose": "profile", "artifacts": artifacts}
    )
    work = FinishedWork(
        sequence=1,
        plan=_plan(required_fields),
        parent=revision("parent"),
        failure=failure,
        readings=() if reading is None else (reading,),
        proof=None if reading is None else Proof(refs=(ref,)),
    )
    return _world(finished=(work,), running=()).prompt()


def _running() -> PlannerPrompt:
    work = Workstream(
        sequence=1,
        plan=_plan(()),
        attempt=AttemptId(root="attempt-1"),
        parent=revision("parent"),
        phase=Profiling(),
    )
    return _world(finished=(), running=(work,)).prompt()


def _reading(*, passed: bool, metrics: tuple[MetricRow, ...], feedback: str) -> AcceptedReading:
    ref = evidence_ref("profile-evidence", EvidenceKind.PROFILING, revision("parent"))
    return AcceptedReading(
        source_request=ref.source_request,
        evidence_id=ref.evidence_id,
        kind=EvidenceKind.PROFILING,
        passed=passed,
        stage="profile",
        metrics=metrics,
        feedback=feedback,
    )


def _row(prompt: PlannerPrompt) -> ProfileHistoryRow:
    (row,) = [item for item in prompt.history if getattr(item, "kind", None) == "profile"]
    assert isinstance(row, ProfileHistoryRow)
    return row


@given(
    metrics=_metrics(),
    output=st.text(_WORDS, max_size=6000),
    fields=st.lists(st.sampled_from(["hip_api_timing"]), max_size=1, unique=True).map(tuple),
    artifact_names=st.lists(st.text(string.ascii_lowercase, min_size=3, max_size=8), max_size=3),
)
def test_a_passed_profile_is_observed_evidence_whatever_its_output_length(
    metrics: tuple[MetricRow, ...],
    output: str,
    fields: tuple[str, ...],
    artifact_names: list[str],
) -> None:
    artifacts = tuple(
        ArtifactRef(artifact_id=ArtifactId(root=f"art-{name}"), digest="d")
        for name in artifact_names
    )
    prompt = _finished(
        reading=_reading(passed=True, metrics=metrics, feedback=output),
        required_fields=fields,
        artifacts=artifacts,
    )
    row = _row(prompt)
    rendered = _text(prompt)

    assert row.status is ProfileHistoryStatus.OBSERVED
    assert row.failure is None
    assert FAILURE_TEXT not in rendered
    assert "Why it failed" not in rendered
    # The verdict, the typed statistics, the fields and the artifacts are never cut.
    assert "Verdict: the trusted profile succeeded" in rendered
    for field in fields:
        assert field in rendered
    for item in metrics:
        assert f"{item.name} = {item.value}" in rendered
    for item in artifacts:
        assert item.artifact_id.root in rendered
    # Only the raw output is bounded, and the cut keeps its end, where the tables are.
    if output:
        assert output[-40:].strip() in rendered
    assert len(row.diagnosis) < 1300


@given(
    feedback=st.text(_WORDS, min_size=1, max_size=3000),
    metrics=_metrics(),
)
def test_a_failed_profile_states_its_failure_and_no_success_evidence(
    feedback: str, metrics: tuple[MetricRow, ...]
) -> None:
    failure = Failure(kind=FailureKind.NO_TRUSTED_EVIDENCE, status=ObservationStatus.FAILED)
    prompt = _finished(
        reading=_reading(passed=False, metrics=metrics, feedback=feedback), failure=failure
    )
    row = _row(prompt)
    rendered = _text(prompt)

    assert row.status is ProfileHistoryStatus.FAILED
    assert "Why it failed" in rendered
    assert feedback[-40:].strip() in rendered
    assert "Verdict: the trusted profile succeeded" not in rendered
    assert row.metrics == ()


def test_a_finished_profile_with_no_reading_states_the_typed_failure() -> None:
    failure = Failure(kind=FailureKind.NO_TRUSTED_EVIDENCE, status=ObservationStatus.SUCCEEDED)
    prompt = _finished(reading=None, failure=failure)
    assert _row(prompt).status is ProfileHistoryStatus.FAILED
    assert FAILURE_TEXT in _text(prompt)


@given(output=st.text(_WORDS, min_size=1, max_size=200))
def test_a_profile_that_ended_in_failure_reports_what_its_evidence_printed(output: str) -> None:
    failure = Failure(
        kind=FailureKind.NO_TRUSTED_EVIDENCE, status=ObservationStatus.FAILED, output=output
    )
    prompt = _finished(reading=None, failure=failure)
    assert output.strip()[-40:] in _text(prompt)


def test_a_running_profile_carries_neither_failure_nor_success_evidence() -> None:
    prompt = _running()
    rendered = _text(prompt)
    assert _row(prompt).status is ProfileHistoryStatus.RUNNING
    assert FAILURE_TEXT not in rendered
    assert "Why it failed" not in rendered
    assert "Verdict: the trusted profile succeeded" not in rendered
