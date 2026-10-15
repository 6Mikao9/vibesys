"""Agents keep working while candidates wait for a trusted evaluation, bound only when a slot frees.

A workstream whose candidate is reviewed submits it to the run's evaluation queue and parks: its
agent slot goes to new work at once, whether or not an evaluation slot is free. Which candidate an
evaluation slot measures is decided only when the slot frees: the newest waiting one. A newer
candidate of the same hypothesis (built on the waiting one, so it holds its code) drops the older
one unmeasured, with the reason recorded and shown to the orchestrator. A workstream that waits
longer than the run allows for the input's measurement is not left hanging silently: the
orchestrator is told on its next turn. A plan step accepted on its own checks never waits for the
input at all.
"""

from __future__ import annotations

from tests.vibesys.orchestration.dynamic._host import (
    PLANNER_LABEL,
    Host,
    begin,
    config,
    plan_config,
)
from tests.vibesys.orchestration.dynamic._replies import (
    implement,
    implemented,
    plan_reply,
    reviewed,
    step,
)
from tests.vibesys.orchestration.dynamic.core_policy._contexts import render
from tests.vibesys.search_faults import assert_flow_closed
from tests.vibesys.vibesys_flows import across_restarts

from vibesys.orchestration.dynamic.models import ImplementerResult, ReviewResult
from vibesys.orchestration.dynamic.search.api import (
    DropReason,
    FailureKind,
    PlannerCorrection,
    PlannerPrompt,
    PromptTemplate,
    RunSnapshot,
)
from vs_core.api import (
    Finish,
    Measure,
    RequestTurn,
    Retained,
    RevisionRef,
    Settle,
    StartAttempt,
    Wait,
    Withdraw,
)

MODES = across_restarts
QUEUED = {"max_in_flight": 1, "evaluation_slots": 1, "max_backlog": 3}
"""One agent slot and one evaluation slot; up to three workstreams parked on their measurement."""


def _line_of(decision: str) -> str:
    """The workstream a decision belongs to: ``000002.h2/measure.1`` is of ``000002.h2``."""
    return decision.split("/", 1)[0]


def _issued[R](host: Host, kind: type[R]) -> list[R]:
    """Every request of ``kind`` the run issued, in order."""
    return [
        item.request
        for item in host.run.decisions
        if isinstance(item, Wait) and isinstance(item.request, kind)
    ]


def _measured(host: Host) -> list[str]:
    """The workstreams whose candidate the run measured, in the order it asked."""
    return [
        _line_of(request.decision_id.root)
        for request in _issued(host, Measure)
        if request.plan.purpose != "baseline"
    ]


def _measuring(host: Host) -> list[str]:
    """The workstreams whose candidate is being measured now."""
    return [
        _line_of(item.decision.root)
        for item in host.open(Measure)
        if isinstance(item.request, Measure) and item.request.plan.purpose != "baseline"
    ]


def _answer_but_measurements(host: Host) -> None:
    """Answer every open wait but agent turns and trusted measurements (workspaces, checks)."""
    while waits := [
        item for item in host.run.open if not isinstance(item.request, RequestTurn | Measure)
    ]:
        host.answer(waits[0])


def _work(host: Host, line: str, commit: str) -> None:
    """Implement the open turn of workstream ``line`` as ``commit`` and pass the judge's review."""
    _answer_but_measurements(host)
    implementer = next(
        item
        for item in host.open(RequestTurn, label="implementer")
        if _line_of(item.decision.root) == line
    )
    host.run.reply(
        implementer,
        ImplementerResult.model_validate_json(implemented()),
        checkpoint=Retained(revision=RevisionRef.of_git_commit(commit)),
    )
    _answer_but_measurements(host)
    judge = next(
        item
        for item in host.open(RequestTurn, label="judge")
        if _line_of(item.decision.root) == line
    )
    host.run.reply(judge, ReviewResult.model_validate_json(reviewed()))
    _answer_but_measurements(host)


def _end_measurement(host: Host, line: str) -> None:
    """Answer the trusted measurement of ``line``, and what decoding its evidence asks."""
    (measuring,) = [item for item in host.open(Measure) if _line_of(item.decision.root) == line]
    host.answer(measuring)
    _answer_but_measurements(host)


def _plan(host: Host, *entries: dict[str, object]) -> PlannerPrompt:
    """Answer the open planner turn with ``entries``; the prompt it was asked with."""
    turn = host.run.expect_turn(role=PLANNER_LABEL)
    assert isinstance(turn.context, PlannerPrompt)
    host.run.reply_raw(turn, plan_reply(*entries))
    return turn.context


def _record(host: Host) -> RunSnapshot:
    record = host.record
    assert record is not None
    return record


@MODES
def test_agents_keep_working_and_a_freed_slot_measures_the_newest_candidate(
    *, restarting: bool
) -> None:
    host = begin(config(max_rounds=3, **QUEUED), restarting=restarting)
    _plan(host, implement("h1"))
    _work(host, "000001.h1", "c1")
    assert _measuring(host) == ["000001.h1"]

    # h1 holds the only evaluation slot, yet its agent slot is free: h2 and h3 are implemented.
    _plan(host, implement("h2"))
    _work(host, "000002.h2", "c2")
    told = _plan(host, implement("h3"))
    _work(host, "000003.h3", "c3")
    assert _measuring(host) == ["000001.h1"]
    assert not _record(host).finished

    # The orchestrator saw the queue: h1 measured, h2 waiting.
    assert told.evaluations is not None
    assert [item.work_id for item in told.evaluations.measuring] == ["h1"]
    assert [item.work_id for item in told.evaluations.waiting] == ["h2"]
    assert "waiting: `h2`" in render(PromptTemplate.PORTFOLIO, told)

    # When h1's measurement ends, its slot is bound to the newest candidate waiting, h3's.
    _end_measurement(host, "000001.h1")
    assert _measuring(host) == ["000003.h3"]
    host.drain()
    assert _measured(host) == ["000001.h1", "000003.h3", "000002.h2"]
    assert isinstance(host.run.finished, Finish)
    assert_flow_closed(host)


@MODES
def test_a_newer_candidate_of_the_same_hypothesis_drops_the_waiting_one_unmeasured(
    *, restarting: bool
) -> None:
    host = begin(config(max_rounds=3, **QUEUED), restarting=restarting)
    _plan(host, implement("h1"))
    _work(host, "000001.h1", "c1")
    _plan(host, implement("h2"))
    _work(host, "000002.h2", "c2")

    # h2 waits for the slot h1 holds; the orchestrator continues it, built on its waiting code.
    told = _plan(host, implement("h2", continue_hypothesis=True, task="next step"))
    (row,) = [item for item in told.history if getattr(item, "hypothesis_id", "") == "h2"]
    assert row.status == "submitted"
    _answer_but_measurements(host)
    (started,) = [
        request
        for request in _issued(host, StartAttempt)
        if _line_of(request.decision_id.root) == "000003.h2"
    ]
    assert started.workspace.base == RevisionRef.of_git_commit("c2")
    _work(host, "000003.h2", "c3")

    # c3 holds c2's code: c2 is dropped, never measured, and kept as work in progress.
    (dropped,) = [item for item in _record(host).finished if item.sequence == 2]
    assert dropped.failure is not None
    assert dropped.failure.kind is FailureKind.NOT_MEASURED
    assert dropped.failure.dropped is DropReason.SUPERSEDED
    assert dropped.failure.superseded_by == "h2"
    (settled,) = [
        request.disposition
        for request in _issued(host, Withdraw)
        if _line_of(request.decision_id.root) == "000002.h2"
    ]
    assert isinstance(settled, Settle)
    assert settled.retention == "wip"
    assert settled.candidate == RevisionRef.of_git_commit("c2")
    assert not settled.eligible
    queue = _record(host).evaluations
    assert queue is not None
    assert [entry.submission.work_id for entry, _ in queue.dropped] == ["h2"]

    _end_measurement(host, "000001.h1")
    host.drain()
    assert _measured(host) == ["000001.h1", "000003.h2"]
    assert isinstance(host.run.finished, Finish)
    assert_flow_closed(host)


@MODES
def test_a_wait_for_the_input_past_the_limit_reaches_the_orchestrators_next_turn(
    *, restarting: bool
) -> None:
    limit = 600.0
    cfg = config(
        max_rounds=2,
        max_in_flight=1,
        evaluation_slots=2,
        max_backlog=1,
        input_wait_seconds=limit,
    )
    host = begin(cfg, restarting=restarting, input_first=False)
    _plan(host, implement("h1"))
    _work(host, "000001.h1", "c1")

    # h1 is measured beside the input, then waits for the input to settle; the planner is asked.
    _end_measurement(host, "000001.h1")
    assert host.input_waits()
    first = host.run.expect_turn(role=PLANNER_LABEL)
    assert isinstance(first.context, PlannerPrompt)
    assert first.context.input_overdue is None

    # The limit passes: the run is not stuck silently, the orchestrator's next turn says so.
    host.run.advance_to(min(host.run.sleeping_until))
    record = _record(host)
    assert record.input_overdue is not None
    assert record.input_overdue.limit == limit
    host.run.reply_raw(first, "not json")
    again = host.run.expect_turn(role=PLANNER_LABEL).context
    assert isinstance(again, PlannerCorrection)
    assert again.input_overdue is not None
    assert again.input_overdue.limit_seconds == int(limit)
    assert again.input_overdue.waited_seconds is not None
    assert again.input_overdue.waited_seconds >= int(limit)
    assert "measurement is overdue" in render(PromptTemplate.PORTFOLIO_CORRECTION, again)

    # The orchestrator decides; once the input lands, the run ends as usual.
    host.run.reply_raw(host.run.expect_turn(role=PLANNER_LABEL), plan_reply(implement("h2")))
    host.measure_input()
    host.drain()
    assert isinstance(host.run.finished, Finish)
    assert_flow_closed(host)


@MODES
def test_a_step_accepted_on_its_own_checks_settles_before_the_input_lands(
    *, restarting: bool
) -> None:
    host = begin(plan_config(step("a")), restarting=restarting, input_first=False)
    _work(host, "000001.a", "a-rev")

    # The step is never measured, so nothing in it waits for the input's measurement.
    assert host.input_waits()
    (settled,) = _record(host).finished
    assert settled.plan.work_id == "a"
    assert settled.failure is None
    assert not _measured(host)

    host.measure_input()
    host.drain()
    assert isinstance(host.run.finished, Finish)
    assert_flow_closed(host)
