"""Crash-at-every-step sweep: a restart after every step tells the same story as a live run.

Toy flows are generated from small programs (asks, timers, races between a request and a timer
or the stop, retries, shielded regions, resources, and groups of tasks with their own
programs), and so are the event orders (answer an open wait with a reply or a failure, move the
clock, stop). The same orders drive two runs in lockstep: one live, one that writes its state to
JSON and replays it in a new search after every step. They must agree on every decision, every
state and what every flow waits for. A counterexample is printed as a timeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from hypothesis import given, note, settings
from hypothesis import strategies as st

import vs_core.api as core

from .flow_support import SHORT, Config, Plan, answer_of, ask, finish, start

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable, Coroutine

    from vs_core.api.testing import FlowRun


@dataclass(frozen=True)
class Ask:
    label: str


@dataclass(frozen=True)
class Sleep:
    until: float


@dataclass(frozen=True)
class Race:
    """A request against a timer (or, with ``until`` None, against the stop)."""

    label: str
    until: float | None


@dataclass(frozen=True)
class Retry:
    tries: int
    backoff: bool


@dataclass(frozen=True)
class Shield:
    label: str


@dataclass(frozen=True)
class Guarded:
    """A resource around one ask: setup, body, teardown."""

    label: str


@dataclass(frozen=True)
class Scope:
    """A group of tasks, each running its own program; the flow drains their results."""

    tasks: tuple[tuple[Op, ...], ...]
    stops: bool


type Op = Ask | Sleep | Race | Retry | Shield | Guarded | Scope

_LEAVES = st.one_of(
    st.builds(Ask, label=st.sampled_from(["x", "y"])),
    st.builds(Sleep, until=st.integers(1, 30).map(float)),
    st.builds(
        Race, label=st.sampled_from(["r", "s"]), until=st.none() | st.integers(1, 30).map(float)
    ),
    st.builds(Retry, tries=st.integers(1, 3), backoff=st.booleans()),
    st.builds(Shield, label=st.just("z")),
    st.builds(Guarded, label=st.just("g")),
)


def _bodies(inner: st.SearchStrategy[tuple[Op, ...]]) -> st.SearchStrategy[tuple[Op, ...]]:
    scope = st.builds(Scope, tasks=st.lists(inner, max_size=2).map(tuple), stops=st.booleans())
    return st.lists(st.one_of(_LEAVES, scope), max_size=3).map(tuple)


PROGRAMS = st.recursive(st.lists(_LEAVES, max_size=3).map(tuple), _bodies, max_leaves=8)


@core.resource
async def guard(ctx: core.Flow, label: str) -> AsyncGenerator[str]:
    await ctx.wait(ask(f"{label}-open"), SHORT, reply=Plan)
    try:
        yield label
    except core.Stopped:
        await ctx.wait(ask(f"{label}-close"), SHORT, reply=Plan)
        raise
    await ctx.wait(ask(f"{label}-close"), SHORT, reply=Plan)


async def interpret(ctx: core.Flow, body: tuple[Op, ...]) -> str:
    """Run a program: the toy flow every generated program is an instance of."""
    seen: list[str] = []
    for op in body:
        match op:
            case Ask(label=label):
                seen.append(type(await ctx.wait(ask(label), SHORT, reply=Plan)).__name__)
            case Sleep(until=until):
                await ctx.timer(until)
            case Race(label=label, until=until):
                pending = ctx.start(ask(label), SHORT, reply=Plan)
                other = ctx.stopped if until is None else ctx.timer(until)
                seen.append(type(await ctx.first(pending, other)).__name__)
            case Retry(tries=tries, backoff=backoff):
                seen.append(str(await ctx.retry(_attempt(ctx), _policy(tries, backoff=backoff))))
            case Shield(label=label):
                with ctx.shielded():
                    await ctx.wait(ask(label), SHORT, reply=Plan)
            case Guarded(label=label):
                async with guard(ctx, label):
                    await ctx.wait(ask(f"{label}-body"), SHORT, reply=Plan)
            case Scope(tasks=tasks, stops=stops):
                async with ctx.group(stops=stops) as group:
                    for index, task in enumerate(tasks):
                        group.start(f"t{index}", interpret, task)
                    seen.extend([str(done) async for done in group])
    return ",".join(seen)


def _policy(tries: int, *, backoff: bool) -> core.RetryPolicy:
    return core.RetryPolicy(max_tries=tries, backoff=core.Backoff(first=2.0) if backoff else None)


def _attempt(
    ctx: core.Flow,
) -> Callable[[core.Tries[str]], Coroutine[Any, Any, int | core.Again[str]]]:
    async def attempt(tries: core.Tries[str]) -> int | core.Again[str]:
        n = answer_of(await ctx.wait(ask("t"), SHORT, reply=Plan))
        return n if n > 0 else core.Again(reason=str(tries.number))

    return attempt


def make_root(body: tuple[Op, ...]) -> Callable[[core.Flow, Config], Coroutine[Any, Any, None]]:
    async def root(ctx: core.Flow, _config: Config) -> None:
        await ctx.wait(ask("start"), SHORT, reply=Plan)  # the view is readable after it
        await ctx.finish(finish(await interpret(ctx, body) or "empty"))

    return root


@settings(max_examples=60, deadline=None)
@given(body=PROGRAMS, data=st.data())
def test_a_restart_after_every_step_equals_the_live_run(
    body: tuple[Op, ...], data: st.DataObject
) -> None:
    live = start(make_root(body))
    crashing = start(make_root(body), restarting=True)
    for _ in range(data.draw(st.integers(0, 16))):
        action = _action(data, live)
        outcomes = []
        for run in (live, crashing):
            try:
                action(run)
                outcomes.append("ok")
            except core.Deadlock as deadlock:  # a generated program may deadlock; both must
                outcomes.append(f"deadlock {deadlock.flow} {deadlock.sources}")
        note(live.timeline())
        assert outcomes[0] == outcomes[1]
        if outcomes[0] != "ok":
            return
        assert live.decisions == crashing.decisions
        assert live.state == crashing.state
        assert live.open == crashing.open
        assert live.waiting() == crashing.waiting()


def _action(data: st.DataObject, run: FlowRun[Config]) -> Callable[[FlowRun[Config]], None]:
    """Draw the next host action from what the live run has open."""
    choices = ["advance", "stop"]
    choices += [f"reply{i}" for i, _ in enumerate(run.open)]
    choices += [f"fail{i}" for i, _ in enumerate(run.open)]
    choice = data.draw(st.sampled_from(choices))
    step = data.draw(st.integers(1, 15))
    answer = data.draw(st.integers(0, 3))
    if choice == "stop":
        return lambda r: r.stop()
    if choice == "advance":
        target = run.view.now_at + step
        return lambda r: r.advance_to(target)
    asked = run.open[int(choice.removeprefix("reply").removeprefix("fail"))]
    if choice.startswith("fail") or not isinstance(asked.request, core.AskAgent):
        return lambda r: r.fail(asked, core.FailureReason.TIMED_OUT)
    return lambda r: r.reply(asked, Plan(n=answer))
