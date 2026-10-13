"""Story tests for the flow vocabulary, each run live and with a restart after every step.

A story reads as a script: the search opens waits, the test answers them. Every story runs
twice: once on one live search, once where the state is written to JSON and a new search
replays it after each step. Both must tell the same story.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

import vs_core.api as core
from vs_core.api.testing import FakeView

from .flow_support import FACTS, SHORT, Config, Plan, answer_of, ask, finish, start

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from vs_core.api.testing import FlowRun

type Begin = Callable[..., FlowRun[Config]]


@pytest.fixture(params=[False, True], ids=["live", "restart-every-step"])
def begin(request: pytest.FixtureRequest) -> Begin:
    """Starts a flow; the fixture's two settings are a live run and a restart after every step."""
    restarting = bool(request.param)
    return lambda root, view=None: start(root, restarting=restarting, view=view)


def roots(run: FlowRun[Config]) -> list[str]:
    return [a.decision.root for a in run.open]


# -- wait: effects, outcomes as values ----------------------------------------------------------


async def plan_then_finish(ctx: core.Flow, _config: Config) -> None:
    match await ctx.wait(ask("planner"), SHORT, reply=Plan):
        case core.TurnFinished(reply=Plan(n=n)):
            await ctx.finish(finish(f"planned {n}"))
        case core.Failed(reason=reason):
            await ctx.finish(finish(reason.value, outcome="failure"))
        case other:
            raise AssertionError(other)


def test_a_wait_returns_the_typed_reply_and_the_flow_matches_on_it(begin: Begin) -> None:
    run = begin(plan_then_finish)
    asked = run.expect(core.AskAgent, label="planner")
    assert asked.decision.root == "planner.1"
    assert asked.until == SHORT
    run.reply(asked, Plan(n=3))
    assert run.finished is not None
    assert run.finished.result.reason == "planned 3"
    assert run.timeline() == "planner.1:Plan"


def test_a_failure_is_an_outcome_not_an_exception(begin: Begin) -> None:
    run = begin(plan_then_finish)
    run.fail(run.expect(core.AskAgent), core.FailureReason.TIMED_OUT)
    assert run.finished is not None
    assert (run.finished.result.outcome, run.finished.result.reason) == ("failure", "timed-out")


def test_the_runtime_numbers_each_label(begin: Begin) -> None:
    async def twice(ctx: core.Flow, _config: Config) -> None:
        for _ in range(2):
            await ctx.wait(ask("planner"), SHORT, reply=Plan)
        await ctx.finish(finish("two"))

    run = begin(twice)
    run.reply(run.expect(core.AskAgent), Plan(n=1))
    assert roots(run) == ["planner.2"]
    run.reply(run.expect(core.AskAgent), Plan(n=2))
    assert run.finished is not None
    assert run.finished.decision_id.root == "done.1"


def test_a_refused_finish_returns_to_the_flow(begin: Begin) -> None:
    async def insistent(ctx: core.Flow, _config: Config) -> None:
        refused = await ctx.finish(finish("first try"))
        await ctx.finish(finish(f"second try after {refused.reason.value}"))

    run = begin(insistent)
    run.fail(run.expect(core.Finish), core.FailureReason.REFUSED)
    assert run.finished is not None
    assert run.finished.result.reason == "second try after refused"
    assert run.finished.decision_id.root == "done.2"


# -- start, first, sources ------------------------------------------------------------------------


async def race(ctx: core.Flow, _config: Config) -> None:
    slow = ctx.start(ask("slow"), SHORT, reply=Plan)
    quick = ctx.start(ask("quick"), SHORT, reply=Plan)
    gate = ctx.timer(100.0)
    won = await ctx.first(quick, slow)
    other = await ctx.first(slow, quick, gate)
    await ctx.finish(finish(f"{answer_of(won)} then {type(other).__name__}"))


def test_first_gives_the_next_value_of_whichever_source_is_ready(begin: Begin) -> None:
    run = begin(race)
    assert run.waiting()[0].sources == ("quick.1", "slow.1")
    run.reply(run.expect(core.AskAgent, label="slow"), Plan(n=1))
    assert run.waiting()[0].sources == ("slow.1", "quick.1", "timer@100")
    run.reply(run.expect(core.AskAgent, label="quick"), Plan(n=2))
    assert run.finished is not None
    assert run.finished.result.reason == "1 then TurnFinished"  # slow answered first, then quick


def test_when_several_sources_are_ready_the_earliest_argument_wins(begin: Begin) -> None:
    async def tie(ctx: core.Flow, _config: Config) -> None:
        a = ctx.start(ask("a"), SHORT, reply=Plan)
        b = ctx.start(ask("b"), SHORT, reply=Plan)
        gate = ctx.timer(10.0)
        await gate  # both answers arrive while the flow waits for this
        first = answer_of(await ctx.first(b, a))
        second = answer_of(await ctx.first(b, a))
        await ctx.finish(finish(f"{first} {second}"))

    run = begin(tie)
    run.reply(run.expect(core.AskAgent, label="a"), Plan(n=1))
    run.reply(run.expect(core.AskAgent, label="b"), Plan(n=2))
    run.advance_to(10.0)
    assert run.finished is not None
    assert run.finished.result.reason == "2 1"


def test_a_timer_fires_once_when_the_clock_reaches_it(begin: Begin) -> None:
    async def nap(ctx: core.Flow, _config: Config) -> None:
        await ctx.timer(30.0)
        await ctx.finish(finish("woke"))

    run = begin(nap)
    assert run.sleeping_until == (30.0,)
    run.advance_to(10.0)
    assert run.finished is None
    run.advance_to(31.0)
    assert run.finished is not None
    assert run.timeline() == "t=10 t=31"


def test_the_stop_is_a_source_that_yields_the_event_once(begin: Begin) -> None:
    async def watcher(ctx: core.Flow, _config: Config) -> None:
        pending = ctx.start(ask("work"), SHORT, reply=Plan)
        match await ctx.first(pending, ctx.stopped):
            case core.StopRequested(control=control):
                assert ctx.stop_requested
                await ctx.finish(finish(f"stopped by {control.control_id.root}"))
            case other:
                raise AssertionError(other)

    run = begin(watcher)
    run.stop()
    assert run.finished is not None
    assert run.finished.result.reason == "stopped by stop"


def test_other_control_actions_are_ignored(begin: Begin) -> None:
    run = begin(plan_then_finish)
    run.send(
        core.StopRequested(
            control=core.ControlInput(control_id=core.ControlId(root="c"), action="pause")
        )
    )
    assert roots(run) == ["planner.1"]


def test_a_flow_that_waits_for_nothing_that_can_arrive_is_a_deadlock(begin: Begin) -> None:
    async def twice(ctx: core.Flow, _config: Config) -> None:
        pending = ctx.start(ask("a"), SHORT, reply=Plan)
        await pending
        await pending  # its one value is gone

    run = begin(twice)
    with pytest.raises(core.Deadlock) as caught:
        run.reply(run.expect(core.AskAgent), Plan(n=1))
    assert (caught.value.flow, caught.value.sources) == ("", ("a.1",))


def test_a_flow_may_await_only_ctx_operations(begin: Begin) -> None:
    class Foreign:
        def __await__(self):  # noqa: ANN204
            yield 1

    async def rogue(_ctx: core.Flow, _config: Config) -> None:
        await Foreign()

    with pytest.raises(core.ContractError, match="only ctx operations"):
        begin(rogue)


# -- groups and tasks -------------------------------------------------------------------------


async def worker(ctx: core.Flow) -> int:
    return answer_of(await ctx.wait(ask("work"), SHORT, reply=Plan))


async def fan_out(ctx: core.Flow, _config: Config) -> None:
    async with ctx.group(limit=2) as workers:
        assert workers.free == 2
        workers.start("a", worker)
        workers.start("b", worker)
        assert workers.free == 0
        results = [done async for done in workers]
    await ctx.finish(finish(f"results {results}"))


def test_a_group_yields_results_as_tasks_finish_and_task_ids_carry_their_key(begin: Begin) -> None:
    run = begin(fan_out)
    assert roots(run) == ["a/work.1", "b/work.1"]
    first, second = run.open
    run.reply(second, Plan(n=20))
    run.reply(first, Plan(n=1))
    assert run.finished is not None
    assert run.finished.result.reason == "results [20, 1]"


def test_starting_a_task_in_a_full_group_is_a_bug(begin: Begin) -> None:
    async def greedy(ctx: core.Flow, _config: Config) -> None:
        async with ctx.group(limit=1) as one:
            one.start("a", worker)
            one.start("b", worker)

    with pytest.raises(core.ContractError, match="full"):
        begin(greedy)


def test_a_task_is_a_source_and_tasks_nest(begin: Begin) -> None:
    async def middle(ctx: core.Flow) -> int | core.TaskStopped:
        async with ctx.group() as inner:
            return await inner.start("b", worker)

    async def outer(ctx: core.Flow, _config: Config) -> None:
        async with ctx.group() as outers:
            got = await outers.start("a", middle)
        await ctx.finish(finish(f"got {got}"))

    run = begin(outer)
    asked = run.expect(core.AskAgent)
    assert asked.decision.root == "a/b/work.1"
    assert [w.flow for w in run.waiting()] == ["", "a/", "a/b/"]
    run.reply(asked, Plan(n=7))
    assert run.finished is not None
    assert run.finished.result.reason == "got 7"


def test_leaving_a_group_waits_for_every_task(begin: Begin) -> None:
    async def leaves_early(ctx: core.Flow, _config: Config) -> None:
        async with ctx.group() as workers:
            workers.start("a", worker)
            workers.start("b", worker)
        await ctx.finish(finish("all done"))

    run = begin(leaves_early)
    run.reply(run.expect(core.AskAgent, label="work"), Plan(n=1))
    assert run.finished is None
    run.reply(run.expect(core.AskAgent, label="work"), Plan(n=2))
    assert run.finished is not None


# -- stops, resources, shielding ---------------------------------------------------------------


@core.resource
async def attempt(ctx: core.Flow, name: str) -> AsyncIterator[str]:
    await ctx.wait(ask("open"), SHORT, reply=Plan)
    try:
        yield name
    except core.Stopped:
        await ctx.wait(ask("settle-stopped"), SHORT, reply=Plan)
    else:
        await ctx.wait(ask("settle"), SHORT, reply=Plan)


async def workstream(ctx: core.Flow, name: str) -> str:
    async with attempt(ctx, name):
        await ctx.wait(ask("work"), SHORT, reply=Plan)
    return f"{name} done"


async def stop_aware(ctx: core.Flow, _config: Config) -> None:
    seen: list[str] = []
    async with ctx.group() as streams, ctx.group(stops=False) as planning:
        streams.start("a", workstream, "a")
        streams.start("b", workstream, "b")
        planning.start("p", worker)
        while streams.running + planning.running:
            match await ctx.first(planning, streams, ctx.stopped):
                case core.StopRequested():
                    seen.append("stop")
                case core.TaskStopped(key=key):
                    seen.append(f"{key} stopped")
                case int() | str() as done:
                    seen.append(str(done))
    await ctx.finish(finish(" | ".join(seen)))


def test_a_stop_reaches_tasks_of_stopping_groups_and_their_resources_settle(begin: Begin) -> None:
    run = begin(stop_aware)
    for asked in list(run.open):
        if asked.label == "open":
            run.reply(asked, Plan(n=0))
    assert roots(run) == ["p/work.1", "a/work.1", "b/work.1"]
    run.stop()
    # the workstreams' resources settled as stopped; the planner (stops=False) was left alone
    assert roots(run)[-2:] == ["a/settle-stopped.1", "b/settle-stopped.1"]
    run.reply(run.expect(core.AskAgent, label="settle-stopped"), Plan(n=0))
    run.reply(run.expect(core.AskAgent, label="settle-stopped"), Plan(n=0))
    run.reply(run.expect(core.AskAgent, label="work"), Plan(n=5))
    assert run.finished is not None
    assert run.finished.result.reason == "stop | a done | b done | 5"


def test_setup_and_teardown_are_shielded_and_the_stop_comes_at_the_first_await_after(
    begin: Begin,
) -> None:
    run = begin(stop_aware)
    run.stop()  # while both workstreams are still opening
    assert roots(run) == ["a/open.1", "b/open.1", "p/work.1"]
    run.reply(run.expect(core.AskAgent, label="open"), Plan(n=0))
    # a's body asks for work and is stopped at once; its teardown settles it as stopped
    assert roots(run)[-2:] == ["a/work.1", "a/settle-stopped.1"]


def test_a_stop_ends_a_task_that_lets_it_escape_as_task_stopped(begin: Begin) -> None:
    async def parent(ctx: core.Flow, _config: Config) -> None:
        async with ctx.group() as workers:
            workers.start("w", worker)
            done = await ctx.first(workers)
        await ctx.finish(finish(f"{done!r}"))

    run = begin(parent)
    run.stop()
    assert run.finished is not None
    assert run.finished.result.reason == "TaskStopped(key='w')"


def test_a_shielded_region_defers_the_stop(begin: Begin) -> None:
    async def careful(ctx: core.Flow) -> str:
        with ctx.shielded():
            await ctx.wait(ask("critical"), SHORT, reply=Plan)
        await ctx.wait(ask("after"), SHORT, reply=Plan)
        return "unreachable"

    async def parent(ctx: core.Flow, _config: Config) -> None:
        async with ctx.group() as workers:
            workers.start("c", careful)
            done = await ctx.first(workers)
        await ctx.finish(finish(f"{done!r}"))

    run = begin(parent)
    run.stop()
    assert run.finished is None
    run.reply(run.expect(core.AskAgent, label="critical"), Plan(n=0))
    assert run.finished is not None
    assert run.finished.result.reason == "TaskStopped(key='c')"
    assert "c/after.1" in roots(run)  # issued, then abandoned by the stop


def test_a_task_started_in_a_stopping_group_is_stopped_at_its_first_await(begin: Begin) -> None:
    async def parent(ctx: core.Flow, _config: Config) -> None:
        async with ctx.group() as workers:
            await ctx.stopped
            workers.start("late", worker)
            done = await ctx.first(workers)
        await ctx.finish(finish(f"{done!r}"))

    run = begin(parent)
    run.stop()
    assert run.finished is not None
    assert run.finished.result.reason == "TaskStopped(key='late')"
    assert roots(run)[0] == "late/work.1"  # it ran to its first await before it was stopped


def test_a_late_result_of_an_abandoned_wait_is_dropped_and_recorded(begin: Begin) -> None:
    async def parent(ctx: core.Flow, _config: Config) -> None:
        async with ctx.group() as workers:
            workers.start("w", worker)
            await ctx.first(workers)
        await ctx.finish(finish("over"))

    run = begin(parent)
    abandoned = run.expect(core.AskAgent)
    run.stop()
    before = len(run.decisions)
    run.reply(abandoned, Plan(n=9))
    assert len(run.decisions) == before
    assert [(d.event, d.decision) for d in run.state.dropped] == [
        (core.EventKind.TURN_FINISHED, abandoned.decision)
    ]


# -- retry ------------------------------------------------------------------------------------


POLICY = core.RetryPolicy(max_tries=3, backoff=core.Backoff(first=5.0, cap=20.0))


async def retrying(ctx: core.Flow, _config: Config) -> None:
    async def attempt_once(tries: core.Tries[str]) -> int | core.Again[str]:
        n = answer_of(await ctx.wait(ask("try"), SHORT, reply=Plan))
        return n if n > 0 else core.Again(reason=f"zero on try {tries.number}")

    match await ctx.retry(attempt_once, POLICY):
        case int() as value:
            await ctx.finish(finish(f"got {value}"))
        case core.GaveUp(reasons=reasons):
            await ctx.finish(finish(" / ".join(reasons), outcome="failure"))


def test_retry_repeats_an_attempt_with_a_backoff_on_the_run_clock_until_it_gives_a_value(
    begin: Begin,
) -> None:
    run = begin(retrying, FakeView())
    run.reply(run.expect(core.AskAgent, label="try"), Plan(n=0))
    assert run.sleeping_until == (5.0,)  # the first backoff: Backoff.first
    run.advance_to(5.0)
    run.reply(run.expect(core.AskAgent, label="try"), Plan(n=7))
    assert run.finished is not None
    assert run.finished.result.reason == "got 7"


def test_retry_gives_up_after_the_policys_tries_and_reports_every_reason(begin: Begin) -> None:
    run = begin(retrying, FakeView())
    run.reply(run.expect(core.AskAgent, label="try"), Plan(n=0))
    run.advance_to(5.0)
    run.reply(run.expect(core.AskAgent, label="try"), Plan(n=0))
    assert run.sleeping_until == (15.0,)  # doubled, from the clock at that moment
    run.advance_to(15.0)
    run.reply(run.expect(core.AskAgent, label="try"), Plan(n=0))
    assert run.sleeping_until == ()  # no wait after the last try
    assert run.finished is not None
    assert run.finished.result.reason == "zero on try 1 / zero on try 2 / zero on try 3"


# -- reading and publishing -------------------------------------------------------------------


class Progress(core.Value):
    """What the toy flow publishes."""

    replies: int


def test_a_read_is_taken_from_the_live_view_once_and_journaled(begin: Begin) -> None:
    calls: list[float] = []

    def clock(view: core.RunView) -> float:
        calls.append(view.now_at)
        return view.now_at

    async def timed(ctx: core.Flow, _config: Config) -> None:
        await ctx.wait(ask("a"), SHORT, reply=Plan)
        first = ctx.read(clock, float)
        await ctx.wait(ask("b"), SHORT, reply=Plan)
        await ctx.finish(finish(f"read {first} now {ctx.now()}"))

    view = FakeView(now_at=5.0)
    run = begin(timed, view)
    run.reply(run.expect(core.AskAgent, label="a"), Plan(n=1))
    view.now_at = 99.0
    run.reply(run.expect(core.AskAgent, label="b"), Plan(n=2))
    assert run.finished is not None
    assert run.finished.result.reason == "read 5.0 now 99.0"
    assert calls == [5.0]  # replay takes the journaled value, not the view


def test_the_view_cannot_be_read_while_the_run_starts() -> None:
    async def eager(ctx: core.Flow, _config: Config) -> None:
        ctx.now()

    with pytest.raises(core.ContractError, match="view is not available"):
        start(eager)


def test_a_published_record_is_readable_from_the_state(begin: Begin) -> None:
    async def reporting(ctx: core.Flow, _config: Config) -> None:
        ctx.publish(Progress(replies=0))
        await ctx.wait(ask("a"), SHORT, reply=Plan)
        ctx.publish(Progress(replies=1))
        await ctx.wait(ask("b"), SHORT, reply=Plan)

    run = begin(reporting)
    assert run.search.published(run.state, Progress) == Progress(replies=0)
    run.reply(run.expect(core.AskAgent, label="a"), Plan(n=1))
    assert run.search.published(run.state, Progress) == Progress(replies=1)


# -- routing ----------------------------------------------------------------------------------


def _settlement(attempt: core.AttemptRef) -> core.Settlement:
    return core.Settlement(
        settlement_id=core.SettlementId(root="s"),
        attempt=attempt,
        candidate=None,
        assessments=(),
        eligible=False,
        retention="discard",
        outcome="failed",
    )


def test_a_close_answers_the_withdraw_of_its_attempt_though_core_names_the_opening_decision(
    begin: Begin,
) -> None:
    attempt_ref = core.AttemptRef(attempt_id=core.AttemptId(root="at"), generation=0)

    async def closer(ctx: core.Flow, _config: Config) -> None:
        withdraw = core.Withdraw(
            decision_id=core.DecisionId(root="settle"),
            scope=core.THIS_RUN,
            target=attempt_ref,
            disposition=core.Settle(
                assessments=(), eligible=False, retention="discard", outcome="failed"
            ),
        )
        closed = await ctx.wait(withdraw, SHORT)
        await ctx.finish(finish(type(closed).__name__))

    run = begin(closer)
    run.send(
        core.WorkspaceClosed(
            decision=core.DecisionId(root="start-attempt.1"), settlement=_settlement(attempt_ref)
        )
    )
    assert run.finished is not None
    assert run.finished.result.reason == "WorkspaceClosed"
    assert run.state.dropped == ()


def test_an_adoption_result_without_a_decision_answers_the_propose_winner(begin: Begin) -> None:
    selection = core.TrustedBaseline(revision=FACTS.baseline)

    async def proposer(ctx: core.Flow, _config: Config) -> None:
        proposal = core.ProposeWinner(
            decision_id=core.DecisionId(root="propose"), scope=core.THIS_RUN, selection=selection
        )
        await ctx.finish(finish(type(await ctx.wait(proposal, SHORT)).__name__))

    state = core.initial_state()
    observation = core.Observation(
        event_id=core.EventId(root="event"),
        request_id=core.RequestId(root="request"),
        scope=core.Scope(owner=state.run.run_id, generation=0),
        sequence=1,
        observed_at=1.0,
        status=core.ObservationStatus.FAILED,
        terminal=True,
        released=True,
    )
    adoption = core.AdoptionFinished(
        decision=None,
        result=core.AdoptionFailed(
            selection=selection,
            reason=core.AdoptionFailureReason.ADOPT_FAILED,
            rounds=1,
            observation=observation,
        ),
    )
    run = begin(proposer)
    run.send(adoption)
    assert run.finished is not None
    assert run.finished.result.reason == "AdoptionFinished"
    assert run.state.dropped == ()
