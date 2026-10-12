"""Every prompt the strategy can request renders from the core policy's templates.

The strategy sends a typed `PromptContext`; the render owner passes its fields (plus the
run-level `objective`) to the template under strict undefined variables. A template that
reads a name no context supplies fails the render, which the planner sees as "prompt
render failed" and ends the run, so each template is exercised over its context space.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from hypothesis import given
from hypothesis import strategies as st
from tests.vibesys.domain_sections import EMPTY_DOMAIN_SECTIONS

import vibesys.orchestration.dynamic.core_policy.prompts as templates
from vibesys.candidates.api import InputFailure, InputFailureKind
from vibesys.orchestration.dynamic.core_policy.api import (
    planner_reply_example,
    run_prompt_variables,
)
from vibesys.orchestration.dynamic.models import ImplementPortfolioPlan, PortfolioPlan
from vibesys.orchestration.dynamic.search.api import (
    Blocker,
    BlockerKind,
    EvidenceCitation,
    Failure,
    FailureKind,
    HistoryRow,
    ImplementPrompt,
    MetricRow,
    PlannerCorrection,
    PlannerPrompt,
    PlannerReask,
    PlanProblem,
    PlanViolation,
    ProblemKind,
    ProfilePrompt,
    PromptContext,
    PromptTemplate,
    ReviewPrompt,
    ViolationCode,
)
from vibesys.prompts import (
    CORE_PROTOCOL_DIR,
    CoreProtocolTemplate,
    ReplyCorrectionPrompt,
    ResumePrompt,
    WaitUnrecordedPrompt,
)
from vs_core.api import (
    InvocationId,
    InvocationRef,
    ReplyError,
    ReplyErrorCode,
    RevisionId,
    RevisionRef,
    SessionId,
)
from vs_prompts.api import TemplateRenderer

if TYPE_CHECKING:
    from pydantic import BaseModel

_ROOT = Path(templates.__file__).parent
_OBJECTIVE = "Raise queue throughput."
# Immutable once built; its compiled-template cache is shared by every example.
_RENDERER = TemplateRenderer(_ROOT, fallback_roots=(CORE_PROTOCOL_DIR,))


def _revision(name: str) -> RevisionRef:
    return RevisionRef(revision_id=RevisionId(root=name), digest=f"git-commit:{name}")


_REVISIONS = st.sampled_from(("a1", "b2", "c3")).map(_revision)
_TEXT = st.text(alphabet="abcdefg hij", min_size=1, max_size=12)
_WORD = st.text(alphabet="abcdefghij", min_size=1, max_size=8)
_CITATIONS = st.lists(
    st.builds(EvidenceCitation, location=_TEXT, revision=st.none() | _TEXT, purpose=_TEXT),
    max_size=2,
).map(tuple)
_METRICS = st.lists(
    st.builds(
        MetricRow,
        name=_TEXT,
        value=st.floats(min_value=0, max_value=1e6),
        direction=st.sampled_from(("max", "min")),
        unit=st.none() | _TEXT,
    ),
    max_size=2,
).map(tuple)
_TURNS = st.builds(
    InvocationRef,
    session_id=st.builds(SessionId, root=_WORD),
    invocation_id=st.builds(InvocationId, root=_WORD),
    generation=st.integers(0, 2),
)
_ERRORS = st.lists(
    st.builds(
        ReplyError,
        path=st.lists(_TEXT | st.integers(0, 3), max_size=3).map(tuple),
        code=st.sampled_from(ReplyErrorCode),
    ),
    min_size=1,
    max_size=2,
).map(tuple)
_BLOCKERS = st.builds(
    Blocker,
    kind=st.sampled_from(BlockerKind),
    summary=_TEXT,
    next_step=st.none().map(lambda _: "") | _TEXT,
)
_VIOLATIONS = st.builds(
    PlanViolation,
    path=st.lists(_TEXT | st.integers(0, 3), max_size=3).map(tuple),
    code=st.sampled_from(ViolationCode),
    subject=_TEXT,
    revision=_TEXT,
    requested=st.integers(1, 5),
    capacity=st.integers(0, 3),
)
_PROBLEMS = st.one_of(
    st.builds(PlanProblem, kind=st.just(ProblemKind.UNPARSEABLE), errors=_ERRORS),
    st.builds(
        PlanProblem,
        kind=st.just(ProblemKind.VIOLATIONS),
        violations=st.lists(_VIOLATIONS, min_size=1, max_size=3).map(tuple),
    ),
    st.builds(
        PlanProblem,
        kind=st.just(ProblemKind.UNDERFILLED),
        scheduled=st.integers(0, 2),
        wanted=st.integers(1, 3),
    ),
    st.builds(PlanProblem, kind=st.just(ProblemKind.NO_REPLY)),
)
_FAILURES = st.one_of(
    st.builds(Failure, kind=st.just(FailureKind.STOPPED)),
    st.builds(Failure, kind=st.just(FailureKind.OUT_OF_BUDGET)),
    st.builds(Failure, kind=st.just(FailureKind.REPEATED_TURN), blocker=_BLOCKERS),
    st.builds(
        Failure,
        kind=st.just(FailureKind.TURN_LIMIT),
        turns=st.integers(1, 5),
        limit=st.integers(1, 5),
        blocker=_BLOCKERS,
    ),
)
_HISTORY = st.builds(
    HistoryRow,
    hypothesis_id=_TEXT,
    sequence=st.integers(1, 9),
    title=_TEXT,
    status=_TEXT,
    strategy=_TEXT,
    failure=st.none() | _FAILURES,
    failure_tail=_TEXT | st.just(""),
)
_PLANNER = st.builds(
    PlannerPrompt,
    history=st.lists(_HISTORY, max_size=2).map(tuple),
    capacity=st.integers(0, 3),
    in_flight=st.integers(0, 3),
    remaining=st.integers(0, 9),
    base_revision=_REVISIONS,
    base_accuracy_passed=st.none() | st.booleans(),
    offer_snapshot=_TEXT,
    older_ids=st.lists(_TEXT, max_size=2).map(tuple),
    baseline=_METRICS,
    input_failure=st.none()
    | st.builds(InputFailure, kind=st.just(InputFailureKind.BENCHMARK_FAILED), output=_TEXT),
    profiling=st.booleans(),
)
_CONTEXTS: dict[PromptTemplate | CoreProtocolTemplate, st.SearchStrategy[PromptContext]] = {
    PromptTemplate.PORTFOLIO: _PLANNER,
    PromptTemplate.PORTFOLIO_CORRECTION: st.builds(
        PlannerCorrection,
        of=_TURNS,
        planner=_PLANNER,
        problem=st.none() | _PROBLEMS,
    ),
    PromptTemplate.PORTFOLIO_REASK: st.builds(PlannerReask, planner=_PLANNER, problem=_PROBLEMS),
    PromptTemplate.IMPLEMENT: st.builds(
        ImplementPrompt,
        hypothesis_id=_TEXT,
        hypothesis=_TEXT,
        task=_TEXT,
        pass_criteria=_TEXT,
        parent_revision=_REVISIONS,
        evidence=_CITATIONS,
        worktree_revision=st.none() | _REVISIONS,
        prior_revision=st.none() | _REVISIONS,
        rejection=st.none() | _TEXT,
        last_blocker=st.none() | _BLOCKERS,
        turns_without_candidate=st.integers(0, 3),
    ),
    PromptTemplate.REVIEW: st.builds(
        ReviewPrompt,
        hypothesis_id=_TEXT,
        hypothesis=_TEXT,
        pass_criteria=_TEXT,
        candidate=_REVISIONS,
        summary=_TEXT,
        evidence=_CITATIONS,
    ),
    PromptTemplate.PROFILE_REQUEST: st.builds(
        ProfilePrompt, question=_TEXT, required_fields=st.just(()), target=_REVISIONS
    ),
    CoreProtocolTemplate.RESUME: st.builds(
        ResumePrompt,
        role=st.sampled_from(("implementer", "judge", "profiler")),
        retained_revision=st.none() | _REVISIONS,
        timed_out=st.booleans(),
    ),
    CoreProtocolTemplate.REPLY_CORRECTION: st.builds(
        ReplyCorrectionPrompt,
        role=st.sampled_from(("planner", "implementer", "judge", "profiler")),
        errors=_ERRORS,
    ),
    CoreProtocolTemplate.WAIT_UNRECORDED: st.builds(WaitUnrecordedPrompt),
}


def _variables(context: BaseModel, *, agent_evaluation: bool = False) -> dict[str, object]:
    variables = {name: getattr(context, name) for name in type(context).model_fields}
    variables.pop("template")
    return {
        **run_prompt_variables(
            domain_sections=EMPTY_DOMAIN_SECTIONS,
            objective=_OBJECTIVE,
            environment_notes="",
            agent_evaluation=agent_evaluation,
        ),
        **variables,
    }


def test_every_requestable_template_has_a_context_generator() -> None:
    assert set(_CONTEXTS) == {*PromptTemplate, *CoreProtocolTemplate}


@pytest.mark.parametrize(
    "template", [*PromptTemplate, *CoreProtocolTemplate], ids=lambda item: item.value
)
@given(data=st.data())
def test_a_template_renders_for_every_context_of_its_kind(
    template: PromptTemplate | CoreProtocolTemplate, data: st.DataObject
) -> None:
    context = data.draw(_CONTEXTS[template])

    text = _RENDERER.render_template(f"{template.value}.j2", **_variables(context))

    assert text.strip()
    if template in (PromptTemplate.PORTFOLIO, PromptTemplate.IMPLEMENT, PromptTemplate.REVIEW):
        assert _OBJECTIVE in text


@pytest.mark.parametrize("template", [PromptTemplate.IMPLEMENT, PromptTemplate.REVIEW])
@given(data=st.data(), offered=st.booleans())
def test_the_evaluation_tool_is_described_exactly_when_it_is_offered(
    template: PromptTemplate, data: st.DataObject, *, offered: bool
) -> None:
    context = data.draw(_CONTEXTS[template])

    text = _RENDERER.render_template(
        f"{template.value}.j2", **_variables(context, agent_evaluation=offered)
    )

    assert ("submit_evaluation" in text) is offered
    assert ("validate_evaluation_wait" in text) is offered


def _planner_texts(context: PlannerPrompt) -> list[str]:
    invocation = InvocationRef(
        session_id=SessionId(root="s"), invocation_id=InvocationId(root="i"), generation=0
    )
    correction = PlannerCorrection(of=invocation, planner=context, problem=None)
    reask = PlannerReask(planner=context, problem=PlanProblem(kind=ProblemKind.NO_REPLY))
    return [
        _RENDERER.render_template("portfolio.j2", **_variables(context)),
        _RENDERER.render_template("portfolio_correction.j2", **_variables(correction)),
        _RENDERER.render_template("portfolio_reask.j2", **_variables(reask)),
    ]


@given(context=_PLANNER)
def test_the_planner_prompt_shows_a_reply_its_own_schema_accepts(context: PlannerPrompt) -> None:
    # live-1: Haiku put the plan inside a `findings` field, then left a workstream without
    # its fields; each failed schema check cost a 30 to 40 s planner turn.
    example = planner_reply_example()
    for text in _planner_texts(context):
        assert example in text
    for reply_type in (PortfolioPlan, ImplementPortfolioPlan):
        reply_type.model_validate_json(example)


@given(context=_PLANNER)
def test_a_workstream_of_the_reply_is_never_offered_a_sibling_as_parent(
    context: PlannerPrompt,
) -> None:
    # live-1: the planner named its own h1, not yet built, as the parent of h2.
    for text in _planner_texts(context):
        assert "never another workstream of this reply" in text
        assert ("leave `parent_hypothesis_id` null" in text) is True  # no buildable rows here


@given(data=st.data(), offered=st.booleans())
def test_the_implementer_is_told_to_submit_early_and_not_to_repeat_a_passed_check(
    data: st.DataObject, *, offered: bool
) -> None:
    # live-1: implementer turns ran the local check 11 to 14 times and took 12 to 13 minutes.
    context = data.draw(_CONTEXTS[PromptTemplate.IMPLEMENT])

    text = _RENDERER.render_template(
        "implement.j2", **_variables(context, agent_evaluation=offered)
    )

    assert "Once the candidate builds and one local check passes, stop checking" in text
    assert "Never\nrerun a local check on files that already passed it" in text
    assert ("submit it for trusted evaluation" in text) is offered
    assert ("nominate it" in text.split("stop checking and")[1][:60]) is not offered


@given(data=st.data(), offered=st.booleans())
def test_the_judge_reads_evidence_and_the_diff_before_running_anything(
    data: st.DataObject, *, offered: bool
) -> None:
    # live-1: judges ran the local check 10 to 19 times in turns of 96 to 166 s.
    context = data.draw(_CONTEXTS[PromptTemplate.REVIEW])

    text = _RENDERER.render_template("review.j2", **_variables(context, agent_evaluation=offered))

    assert "Start from the referenced evidence and the candidate's diff" in text
    assert "do not run local checks, benchmarks" in text


def test_strategy_prompts_name_no_domain_command() -> None:
    # Domain commands live in the bundle's objective; the strategy's prompts stay generic.
    for template in sorted(_ROOT.glob("*.j2")):
        assert "cpu_check" not in template.read_text(), template.name


@given(data=st.data(), summary=_TEXT, step=st.none() | _TEXT)
def test_a_retried_implementer_is_shown_its_own_blocker_and_narrowed_step(
    data: st.DataObject, summary: str, step: str | None
) -> None:
    # live-2: three turns in a row ended "not ready for trusted evaluation" on one workstream;
    # the retry received only the previous summary and no narrower step.
    context = data.draw(_CONTEXTS[PromptTemplate.IMPLEMENT]).model_copy(
        update={
            "last_blocker": Blocker(kind=BlockerKind.FAILED, summary=summary, next_step=step or ""),
            "turns_without_candidate": 1,
        }
    )

    text = _RENDERER.render_template("implement.j2", **_variables(context))

    assert "What ended that turn:" in text
    assert summary in text
    assert (step is not None and "The narrower step you named for this turn:" in text) or (
        step is None and "The narrower step you named" not in text
    )
    if step is not None:
        assert step in text


@given(data=st.data())
def test_a_first_attempt_is_not_shown_a_blocker(data: st.DataObject) -> None:
    context = data.draw(_CONTEXTS[PromptTemplate.IMPLEMENT]).model_copy(
        update={"last_blocker": None, "turns_without_candidate": 0}
    )

    text = _RENDERER.render_template("implement.j2", **_variables(context))

    assert "What ended that turn:" not in text


@given(data=st.data())
def test_planner_and_implementer_share_one_first_measurable_step(data: st.DataObject) -> None:
    # live-2: the planner chose architecture-scale hypotheses that no single turn could finish.
    planner = data.draw(_PLANNER)
    implement = data.draw(_CONTEXTS[PromptTemplate.IMPLEMENT])

    assert "first measurable step" in _RENDERER.render_template(
        "portfolio.j2", **_variables(planner)
    )
    assert "first measurable step" in _RENDERER.render_template(
        "implement.j2", **_variables(implement)
    )
