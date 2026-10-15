"""A run that follows the task's plan implements its steps in order, without exploring.

With ``mode = "follow_plan"`` the steps of the task's ``[plan]`` table are the work: each ready
step (every step it depends on is done) starts as a workstream in declared order, built on the
latest revision its dependencies settled, with no planner turn. A step with ``evaluate = "none"``
is accepted on the judge's review alone: it is never measured and never the winner, and it is
kept as work in progress for the steps that build on it. A ``trusted`` step is measured as any
candidate is, and may win. A plan that declares no steps is outlined by one turn first. The
planner is asked only to repair a failed step (and may then schedule nothing else) and, once
every step is done, to explore.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tests.vibesys.orchestration.dynamic._host import (
    FACTS,
    OUTLINER_LABEL,
    PLANNER_LABEL,
    Host,
    begin,
    plan_config,
)
from tests.vibesys.orchestration.dynamic._replies import (
    implement,
    implemented,
    outline_reply,
    plan_reply,
    reviewed,
    step,
)
from tests.vibesys.search_faults import assert_flow_closed
from tests.vibesys.vibesys_flows import across_restarts

from vibesys.orchestration.dynamic.models import ImplementerResult, ReviewResult
from vibesys.orchestration.dynamic.search.api import (
    OutlinePrompt,
    PlannerCorrection,
    StepState,
    ViolationCode,
)
from vs_core.api import (
    Finish,
    RequestTurn,
    Retained,
    RevisionRef,
    Settle,
    StartAttempt,
    Wait,
    Withdraw,
)

if TYPE_CHECKING:
    from vs_core.api.testing import Asked

MODES = across_restarts


def _starts(host: Host) -> list[StartAttempt]:
    """Every workspace the run asked to open, in order."""
    return [
        item.request
        for item in host.run.decisions
        if isinstance(item, Wait) and isinstance(item.request, StartAttempt)
    ]


def _started_ids(host: Host) -> list[str]:
    return [request.item_id.root.rsplit(".", 1)[0] for request in _starts(host)]


def _base_of(host: Host, step_id: str) -> RevisionRef:
    """The revision the latest workstream of ``step_id`` started from."""
    found = [r for r in _starts(host) if r.item_id.root.rsplit(".", 1)[0] == step_id]
    assert found, host.run.timeline()
    return found[-1].workspace.base


def _settlements(host: Host, step_id: str) -> list[Settle]:
    """What each workspace of ``step_id`` asked to settle with."""
    return [
        item.request.disposition
        for item in host.run.decisions
        if isinstance(item, Wait)
        and isinstance(item.request, Withdraw)
        and isinstance(item.request.disposition, Settle)
        and f".{step_id}/" in item.request.decision_id.root
    ]


def _planner_asked(host: Host) -> bool:
    return any(
        isinstance(item, Wait)
        and isinstance(item.request, RequestTurn)
        and f"/{PLANNER_LABEL}." in item.request.decision_id.root
        for item in host.run.decisions
    )


def _states(host: Host) -> dict[str, StepState]:
    record = host.record
    assert record is not None
    return dict(record.step_states)


def _implement(host: Host, asked: Asked, commit: str) -> None:
    host.run.reply(
        asked,
        ImplementerResult.model_validate_json(implemented()),
        checkpoint=Retained(revision=RevisionRef.of_git_commit(commit)),
    )


def _work_on(host: Host, step_id: str, commit: str, *, passed: bool = True) -> None:
    """Open the step's workspace if asked, implement it as ``commit``, and have the judge rule."""
    for asked in host.open(StartAttempt):
        if asked.decision.root.split("/")[0].endswith(f".{step_id}"):
            host.answer(asked)
    implementer = next(
        item
        for item in host.open(RequestTurn, label="implementer")
        if item.decision.root.startswith(asked_path(host, step_id))
    )
    _implement(host, implementer, commit)
    judge = host.run.expect_turn(role="judge")
    host.run.reply(judge, ReviewResult.model_validate_json(reviewed(passed=passed)))


def asked_path(host: Host, step_id: str) -> str:
    """The decision-id prefix of the running workstream of ``step_id``."""
    record = host.record
    assert record is not None
    (running,) = [item for item in record.running if item.plan.work_id == step_id]
    return f"{running.sequence:06d}.{step_id}/"


@MODES
def test_plan_steps_start_in_declared_order_with_no_planner_turn(*, restarting: bool) -> None:
    cfg = plan_config(
        step("a"), step("b", "trusted", depends_on=("a",)), step("c"), max_rounds=3
    )
    host = begin(cfg, restarting=restarting)

    # The first step starts at once: nobody is asked what to do.
    assert not host.open(RequestTurn, label=PLANNER_LABEL)
    assert _started_ids(host) == ["a"]
    host.drain()

    # b is declared before c and ready once a is checked, so it goes before c.
    assert _started_ids(host) == ["a", "b", "c"]
    assert not _planner_asked(host)
    assert _states(host) == {
        "a": StepState.CHECKED,
        "b": StepState.EVALUATED,
        "c": StepState.CHECKED,
    }
    assert isinstance(host.run.finished, Finish)
    assert_flow_closed(host)


@MODES
def test_a_dependent_step_waits_for_its_parent_and_builds_on_it(*, restarting: bool) -> None:
    cfg = plan_config(
        step("a"), step("b", depends_on=("a",)), step("c"), max_in_flight=2, max_rounds=2
    )
    host = begin(cfg, restarting=restarting)
    assert sorted(_started_ids(host)) == ["a", "c"]

    # c finishes first and frees a slot, but b's parent is still running: nothing starts.
    _work_on(host, "c", "c-rev")
    host.drain_until_turn("implementer")
    assert sorted(_started_ids(host)) == ["a", "c"]
    assert _states(host)["b"] is StepState.PENDING
    assert not host.open(RequestTurn, label=PLANNER_LABEL)

    # Once a is checked, b starts from a's revision, not from the input or from c's.
    _work_on(host, "a", "a-rev")
    assert _started_ids(host) == ["a", "c", "b"] or _started_ids(host) == ["c", "a", "b"]
    assert _base_of(host, "a") == FACTS.baseline
    assert _base_of(host, "b") == RevisionRef.of_git_commit("a-rev")
    host.drain()
    assert_flow_closed(host)


@MODES
def test_an_unevaluated_step_is_kept_as_work_in_progress_and_never_wins(
    *, restarting: bool
) -> None:
    host = begin(plan_config(step("a")), restarting=restarting)
    host.drain()

    (settled,) = _settlements(host, "a")
    assert settled.retention == "wip"
    assert settled.candidate is not None
    assert not settled.eligible
    # nothing measured it: the only measurement is the input's
    assert not [
        item
        for item in host.run.decisions
        if isinstance(item, Wait) and "/measure." in item.request.decision_id.root
    ]
    finish = host.run.finished
    assert isinstance(finish, Finish)
    assert finish.result.selection is not None
    assert finish.result.selection.kind == "trusted_baseline"
    assert_flow_closed(host)


@MODES
def test_a_trusted_step_on_an_unevaluated_one_is_measured_and_may_win(*, restarting: bool) -> None:
    cfg = plan_config(step("a"), step("b", "trusted", depends_on=("a",)), max_rounds=2)
    host = begin(cfg, restarting=restarting)
    host.drain()

    (unevaluated,) = _settlements(host, "a")
    (milestone,) = _settlements(host, "b")
    assert unevaluated.retention == "wip"
    assert milestone.retention == "candidate"
    assert milestone.eligible
    assert _base_of(host, "b") == unevaluated.candidate
    record = host.record
    assert record is not None
    assert record.leading == "b"
    finish = host.run.finished
    assert isinstance(finish, Finish)
    assert finish.result.selection is not None
    assert finish.result.selection.kind == "retained_candidate"
    assert_flow_closed(host)


@MODES
def test_a_plan_without_steps_is_outlined_by_one_turn_before_anything_starts(
    *, restarting: bool
) -> None:
    host = begin(plan_config(max_rounds=2), restarting=restarting)

    turn = host.run.expect_turn(role=OUTLINER_LABEL)
    assert isinstance(turn.context, OutlinePrompt)
    assert turn.context.documents == ("reference/README.md",)
    assert not host.open(RequestTurn, label=PLANNER_LABEL)
    assert not _starts(host)

    # A reply whose steps form a cycle is corrected; the corrected outline is followed.
    host.run.reply_raw(turn, outline_reply(step("x", depends_on=("y",)), step("y", depends_on=("x",))))
    again = host.run.expect_turn(role=OUTLINER_LABEL)
    host.run.reply_raw(again, outline_reply(step("x"), step("y", "trusted", depends_on=("x",))))
    assert _started_ids(host) == ["x"]
    host.drain()

    assert _started_ids(host) == ["x", "y"]
    assert not _planner_asked(host)
    assert _states(host) == {"x": StepState.CHECKED, "y": StepState.EVALUATED}
    assert_flow_closed(host)


@MODES
def test_a_failed_step_is_repaired_by_the_planner_which_may_schedule_nothing_else(
    *, restarting: bool
) -> None:
    cfg = plan_config(
        step("a"), step("b", depends_on=("a",)), max_rounds=3, max_unmeasured_turns=1
    )
    host = begin(cfg, restarting=restarting)
    _work_on(host, "a", "a-rejected", passed=False)

    # The step failed; nothing depends-free is left, so the planner is asked to repair it.
    planner = host.run.expect_turn(role=PLANNER_LABEL)
    assert _states(host) == {"a": StepState.FAILED, "b": StepState.PENDING}
    host.run.reply_raw(planner, plan_reply(implement("fresh-idea")))

    # An exploration hypothesis is not a repair: it is corrected, naming the rule.
    correction = host.run.expect_turn(role=PLANNER_LABEL)
    assert isinstance(correction.context, PlannerCorrection)
    problem = correction.context.problem
    assert problem is not None
    assert [item.code for item in problem.violations] == [ViolationCode.OUTSIDE_PLAN]
    host.run.reply_raw(correction, plan_reply(implement("a", continue_hypothesis=True)))

    # The repair runs a again; once it is checked, b builds on the repaired revision.
    _work_on(host, "a", "a-fixed")
    assert _base_of(host, "b") == RevisionRef.of_git_commit("a-fixed")
    host.drain()
    assert _states(host) == {"a": StepState.CHECKED, "b": StepState.CHECKED}
    assert "fresh-idea" not in _started_ids(host)
    assert_flow_closed(host)
