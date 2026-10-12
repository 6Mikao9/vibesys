"""Crash-at-every-step sweep: a restart after every step tells the same story as a live run.

Toy flows are generated from small programs (asks, sleeps, reads, children that may be
stoppable, joins, ``until``), and so are the event orders (answer an open wait with a reply or a
failure, move the clock, stop). The same orders drive two runs in lockstep: one live, one that
writes its state to JSON and replays it in a new search after every step. They must agree on
every decision and every state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from hypothesis import given, settings
from hypothesis import strategies as st

import vs_core.api as core

from .flow_support import Config, Plan, ask, finish, start

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from vs_core.api.testing import FlowRun


@dataclass(frozen=True)
class Ask:
    label: str


@dataclass(frozen=True)
class Sleep:
    until: float


@dataclass(frozen=True)
class Read:
    pass


@dataclass(frozen=True)
class Join:
    pass


@dataclass(frozen=True)
class Until:
    results: int


@dataclass(frozen=True)
class Spawn:
    body: tuple[Op, ...]
    stoppable: bool


type Op = Ask | Sleep | Read | Join | Until | Spawn

_LEAVES = st.one_of(
    st.builds(Ask, label=st.sampled_from(["x", "y"])),
    st.builds(Sleep, until=st.integers(1, 30).map(float)),
    st.just(Read()),
    st.just(Join()),
    st.builds(Until, results=st.integers(0, 3)),
)


def _bodies(inner: st.SearchStrategy[tuple[Op, ...]]) -> st.SearchStrategy[tuple[Op, ...]]:
    spawn = st.builds(Spawn, body=inner, stoppable=st.booleans())
    return st.lists(st.one_of(_LEAVES, spawn), max_size=4).map(tuple)


PROGRAMS = st.recursive(st.lists(_LEAVES, max_size=3).map(tuple), _bodies, max_leaves=10)


async def interpret(ctx: core.Flow, body: tuple[Op, ...], results: list[str]) -> str:
    """Run a program: the toy flow every generated program is an instance of."""
    children: list[core.FlowChild[str]] = []
    mine: list[str] = []
    for index, op in enumerate(body):
        match op:
            case Ask(label=label):
                event = await ctx.wait(ask(label), core.Within(limit=50.0), reply=Plan)
                mine.append(type(event).__name__)
            case Sleep(until=until):
                await ctx.wake_at(until)
            case Read():
                mine.append(str(ctx.read(lambda view: view.now_at, float)))
            case Join():
                mine.extend([await ctx.join(child) for child in children])
            case Until(results=count):
                await ctx.until(lambda count=count: len(mine) >= count or ctx.stop_requested)
            case Spawn(body=sub, stoppable=stoppable):
                children.append(
                    ctx.spawn(f"c{index}", guarded, sub, stoppable=stoppable, then=mine.append)
                )
    results.extend(mine)
    return ",".join(mine)


async def guarded(ctx: core.Flow, body: tuple[Op, ...]) -> str:
    """A child: its program, and a farewell ask if it is stopped."""
    try:
        return await interpret(ctx, body, [])
    except core.Stopped:
        await ctx.wait(ask("bye"), core.Within(limit=5.0), reply=Plan)
        return "stopped"


def make_root(body: tuple[Op, ...]) -> Callable[[core.Flow, Config], Coroutine[Any, Any, None]]:
    async def root(ctx: core.Flow, _config: Config) -> None:
        results: list[str] = []
        await ctx.wait(
            ask("start"), core.Within(limit=50.0), reply=Plan
        )  # the view is readable after it
        reason = await interpret(ctx, body, results)
        await ctx.finish(finish(reason or "empty"))

    return root


@settings(max_examples=60, deadline=None)
@given(body=PROGRAMS, data=st.data())
def test_a_restart_after_every_step_equals_the_live_run(
    body: tuple[Op, ...], data: st.DataObject
) -> None:
    live = start(make_root(body))
    crashing = start(make_root(body), restarting=True)
    _same(live, crashing)
    for _ in range(data.draw(st.integers(0, 14))):
        action = _action(data, live)
        if action is None:
            break
        for run in (live, crashing):
            action(run)
        _same(live, crashing)


def _same(live: FlowRun[Config], crashing: FlowRun[Config]) -> None:
    assert live.decisions == crashing.decisions
    assert live.state == crashing.state
    assert live.open == crashing.open


def _action(data: st.DataObject, run: FlowRun[Config]) -> Callable[[FlowRun[Config]], None] | None:
    """Draw the next host action from what the live run has open; None when nothing can happen."""
    choices: list[str] = ["advance", "stop"]
    choices += [f"reply{i}" for i, _ in enumerate(run.open)]
    choices += [f"fail{i}" for i, _ in enumerate(run.open)]
    choice = data.draw(st.sampled_from(choices))
    step = data.draw(st.integers(1, 15))
    answer = data.draw(st.integers(0, 9))
    if choice == "stop":
        return lambda r: r.stop()
    if choice == "advance":
        target = run.view.now_at + step
        return lambda r: r.advance_to(target)
    asked = run.open[int(choice.removeprefix("reply").removeprefix("fail"))]
    if choice.startswith("fail"):
        return lambda r: r.fail(asked, core.FailureReason.TIMED_OUT)
    if isinstance(asked.request, core.AskAgent):
        return lambda r: r.reply(asked, Plan(n=answer))
    return lambda r: r.fail(asked, core.FailureReason.REFUSED)
