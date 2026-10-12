"""Story tests for the flow primitives, each run live and with a restart after every step.

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
    from collections.abc import Callable

    from vs_core.api.testing import FlowRun


type Begin = Callable[..., FlowRun[Config]]


@pytest.fixture(params=[False, True], ids=["live", "restart-every-step"])
def begin(request: pytest.FixtureRequest) -> Begin:
    """Starts a flow; the fixture's two settings are a live run and a restart after every step."""
    restarting = bool(request.param)
    return lambda root, view=None: start(root, restarting=restarting, view=view)


# -- wait and match ---------------------------------------------------------------------------


async def plan_then_finish(ctx: core.Flow, _config: Config) -> None:
    event = await ctx.wait(ask("planner"), SHORT, reply=Plan)
    match event:
        case core.TurnFinished(reply=Plan(n=n)):
            await ctx.finish(finish(f"planned {n}"))
        case core.Failed(reason=reason):
            await ctx.finish(finish(reason.value, outcome="failure"))
        case _:
            raise AssertionError(event)


def test_a_wait_returns_the_typed_reply_and_the_flow_matches_on_it(begin: Begin) -> None:
    run = begin(plan_then_finish)
    asked = run.expect(core.AskAgent, label="planner")
    assert asked.decision.root == "planner.1"
    assert asked.until == SHORT
    run.reply(asked, Plan(n=3))
    assert run.finished is not None
    assert run.finished.result.reason == "planned 3"


def test_a_failure_is_returned_not_raised(begin: Begin) -> None:
    run = begin(plan_then_finish)
    run.fail(run.expect(core.AskAgent), core.FailureReason.TIMED_OUT)
    assert run.finished is not None
    assert (run.finished.result.outcome, run.finished.result.reason) == ("failure", "timed-out")


def test_the_runtime_restamps_each_label_with_a_counter(begin: Begin) -> None:
    async def twice(ctx: core.Flow, _config: Config) -> None:
        for _ in range(2):
            await ctx.wait(ask("planner"), SHORT, reply=Plan)
        await ctx.finish(finish("two"))

    run = begin(twice)
    run.reply(run.expect(core.AskAgent), Plan(n=1))
    assert [a.decision.root for a in run.open] == ["planner.2"]
    run.reply(run.expect(core.AskAgent), Plan(n=2))
    assert run.finished is not None
    assert run.finished.decision_id.root == "done.1"


# -- wake_at ----------------------------------------------------------------------------------


async def sleeper_and_napper(ctx: core.Flow, _config: Config) -> None:
    async def short_nap(child: core.Flow) -> str:
        await child.wake_at(10.0)
        return "rested"

    napped: list[str] = []
    ctx.spawn("nap", short_nap, then=napped.append)
    await ctx.wake_at(30.0)
    await ctx.finish(finish(f"woke after {napped}"))


def test_wake_at_resumes_on_the_first_time_reached_at_or_after_it(begin: Begin) -> None:
    run = begin(sleeper_and_napper)
    assert run.sleeping_until == (10.0, 30.0)
    run.advance_to(5.0)
    assert run.sleeping_until == (10.0, 30.0)
    run.advance_to(20.0)
    assert run.sleeping_until == (30.0,)
    assert run.finished is None
    run.advance_to(31.0)
    assert run.finished is not None
    assert run.finished.result.reason == "woke after ['rested']"


# -- spawn, join, then ------------------------------------------------------------------------


async def worker(ctx: core.Flow) -> int:
    return answer_of(await ctx.wait(ask("work"), SHORT, reply=Plan))


async def fan_out(ctx: core.Flow, _config: Config) -> None:
    folded: list[int] = []
    a = ctx.spawn("a", worker, then=folded.append)
    b = ctx.spawn("b", worker, then=folded.append)
    total = await ctx.join(a) + await ctx.join(b)
    await ctx.finish(finish(f"{total} from {folded}"))


def test_children_run_to_their_first_await_and_their_ids_carry_their_key(begin: Begin) -> None:
    run = begin(fan_out)
    assert [a.decision.root for a in run.open] == ["a/work.1", "b/work.1"]
    run.reply(run.expect(core.AskAgent), Plan(n=1))
    assert run.finished is None


def test_then_runs_in_the_parent_when_the_child_returns_and_join_gives_the_result(
    begin: Begin,
) -> None:
    run = begin(fan_out)
    first, second = run.open
    run.reply(second, Plan(n=20))
    run.reply(first, Plan(n=1))
    assert run.finished is not None
    assert run.finished.result.reason == "21 from [20, 1]"


def test_children_nest_and_their_ids_stack_their_keys(begin: Begin) -> None:
    async def inner(ctx: core.Flow) -> int:
        return answer_of(await ctx.wait(ask("deep"), SHORT, reply=Plan))

    async def middle(ctx: core.Flow) -> int:
        return await ctx.join(ctx.spawn("b", inner))

    async def outer(ctx: core.Flow, _config: Config) -> None:
        got = await ctx.join(ctx.spawn("a", middle))
        await ctx.finish(finish(f"got {got}"))

    run = begin(outer)
    asked = run.expect(core.AskAgent)
    assert asked.decision.root == "a/b/deep.1"
    run.reply(asked, Plan(n=7))
    assert run.finished is not None
    assert run.finished.result.reason == "got 7"


def test_a_key_cannot_be_reused_while_its_child_is_live() -> None:
    async def twice(ctx: core.Flow, _config: Config) -> None:
        ctx.spawn("a", worker)
        ctx.spawn("a", worker)

    with pytest.raises(core.ContractError, match="already running"):
        start(twice)


# -- until ------------------------------------------------------------------------------------


async def first_two_of_three(ctx: core.Flow, _config: Config) -> None:
    done: list[int] = []
    for key in "abc":
        ctx.spawn(key, worker, then=done.append)
    await ctx.until(lambda: len(done) >= 2)
    await ctx.finish(finish(f"first two {done}"))


def test_until_resumes_once_its_predicate_holds_after_an_event(begin: Begin) -> None:
    run = begin(first_two_of_three)
    a, b, c = run.open
    run.reply(c, Plan(n=3))
    assert run.finished is None
    run.reply(a, Plan(n=1))
    assert run.finished is not None
    assert run.finished.result.reason == "first two [3, 1]"
    assert b in run.open


# -- stop -------------------------------------------------------------------------------------


async def careful(ctx: core.Flow) -> str:
    try:
        await ctx.wait(ask("long"), SHORT, reply=Plan)
    except core.Stopped:
        await ctx.wait(ask("cleanup"), SHORT, reply=Plan)
        return "wound down"
    return "finished"


async def stop_aware(ctx: core.Flow, _config: Config) -> None:
    wound: list[str] = []
    s = ctx.spawn("s", careful, stoppable=True, then=wound.append)
    ctx.spawn("t", careful, stoppable=True, then=wound.append)
    ctx.spawn("p", worker)  # not stoppable: the stop does not reach it
    await ctx.until(lambda: ctx.stop_requested)
    await ctx.join(s)
    await ctx.until(lambda: len(wound) == 2)
    await ctx.finish(finish(f"stopped: {wound}", outcome="cancelled"))


def test_stop_reaches_stoppable_children_only_once_in_spawn_order_and_they_may_await(
    begin: Begin,
) -> None:
    run = begin(stop_aware)
    long_s = run.expect(core.AskAgent, label="long")
    assert [a.decision.root for a in run.open] == ["s/long.1", "t/long.1", "p/work.1"]
    run.stop()
    assert [a.decision.root for a in run.open][-2:] == ["s/cleanup.1", "t/cleanup.1"]
    assert run.finished is None
    run.stop()  # a second stop throws nothing more
    assert [a.decision.root for a in run.open].count("s/cleanup.1") == 1
    run.reply(run.expect(core.AskAgent, label="cleanup"), Plan(n=0))
    run.reply(run.expect(core.AskAgent, label="cleanup"), Plan(n=0))
    assert run.finished is not None
    assert run.finished.result.reason == "stopped: ['wound down', 'wound down']"
    assert long_s in run.open  # the runtime abandoned it; the host still owns the request


def test_a_late_result_of_an_abandoned_wait_is_dropped_and_recorded(begin: Begin) -> None:
    run = begin(stop_aware)
    abandoned = run.expect(core.AskAgent, label="long")
    run.stop()
    before = len(run.decisions)
    run.reply(abandoned, Plan(n=9))
    assert len(run.decisions) == before
    assert [(d.event, d.decision) for d in run.state.dropped] == [
        (core.EventKind.TURN_FINISHED, abandoned.decision)
    ]


def test_other_control_actions_are_ignored(begin: Begin) -> None:
    run = begin(stop_aware)
    run.send(
        core.StopRequested(
            control=core.ControlInput(control_id=core.ControlId(root="c"), action="pause")
        )
    )
    assert [a.decision.root for a in run.open] == ["s/long.1", "t/long.1", "p/work.1"]


# -- finish refused ---------------------------------------------------------------------------


def test_a_refused_finish_returns_to_the_flow(begin: Begin) -> None:
    async def insistent(ctx: core.Flow, _config: Config) -> None:
        refused = await ctx.finish(finish("first try"))
        await ctx.finish(finish(f"second try after {refused.reason.value}"))

    run = begin(insistent)
    run.fail(run.expect(core.Finish), core.FailureReason.REFUSED)
    assert run.finished is not None
    assert run.finished.result.reason == "second try after refused"
    assert run.finished.decision_id.root == "done.2"


# -- read -------------------------------------------------------------------------------------


def test_a_read_is_taken_from_the_live_view_once_and_journaled(begin: Begin) -> None:
    calls: list[float] = []

    def clock(view: core.RunView) -> float:
        calls.append(view.now_at)
        return view.now_at

    async def timed(ctx: core.Flow, _config: Config) -> None:
        await ctx.wait(ask("a"), SHORT, reply=Plan)
        first = ctx.read(clock, float)
        await ctx.wait(ask("b"), SHORT, reply=Plan)
        await ctx.finish(finish(f"read {first}"))

    view = FakeView(now_at=5.0)
    run = begin(timed, view)
    run.reply(run.expect(core.AskAgent, label="a"), Plan(n=1))
    view.now_at = 99.0
    run.reply(run.expect(core.AskAgent, label="b"), Plan(n=2))
    assert run.finished is not None
    assert run.finished.result.reason == "read 5.0"
    assert calls == [5.0]  # replay after a restart takes the journaled value, not the view


def test_the_view_cannot_be_read_while_the_run_starts() -> None:
    async def eager(ctx: core.Flow, _config: Config) -> None:
        ctx.read(lambda view: view.now_at, float)

    with pytest.raises(core.ContractError, match="view is not available"):
        start(eager)


# -- adoption ---------------------------------------------------------------------------------


def test_an_adoption_result_without_a_decision_goes_to_the_propose_winner_wait(
    begin: Begin,
) -> None:
    revision = FACTS.baseline
    selection = core.TrustedBaseline(revision=revision)

    async def proposer(ctx: core.Flow, _config: Config) -> None:
        proposal = core.ProposeWinner(
            decision_id=core.DecisionId(root="propose"), scope=core.THIS_RUN, selection=selection
        )
        event = await ctx.wait(proposal, SHORT)
        await ctx.finish(finish(type(event).__name__))

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


# -- code change ------------------------------------------------------------------------------


def test_replay_detects_flow_code_that_no_longer_matches_its_journal() -> None:
    async def before(ctx: core.Flow, _config: Config) -> None:
        await ctx.wait(ask("a"), SHORT, reply=Plan)
        await ctx.wait(ask("b"), SHORT, reply=Plan)

    async def after(ctx: core.Flow, _config: Config) -> None:
        await ctx.wait(ask("a"), SHORT, reply=Plan)
        await ctx.wait(ask("renamed"), SHORT, reply=Plan)

    run = start(before)
    run.reply(run.expect(core.AskAgent), Plan(n=1))
    changed = core.FlowSearch(run.search.declaration, after, Config())
    with pytest.raises(core.FlowCodeChanged) as caught:
        changed.update(
            run.state,
            core.TimeReached(time=1.0),
            FakeView().current(),
        )
    assert caught.value.step == 1
