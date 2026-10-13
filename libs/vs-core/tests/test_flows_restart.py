"""A code change between stop and resume: flows restart from their latest safe point.

The journal replays only code that is unchanged. A search built with registered flows
(``@flow``) restarts instead of raising: each running flow resumes from its latest snapshot with
the new code, a task is resumed by its key when its group starts it again, and the results of the
decisions that were in flight are dropped.
"""

from __future__ import annotations

import pytest

import vs_core.api as core
from vs_core.api.testing import FlowRun

from .flow_support import DECLARATION, FACTS, SHORT, Config, Plan, ask, finish, start


class Job(core.Value):
    """What the worker flow can be restarted from."""

    step: int = 0


@core.flow(snapshot=Job, name="worker")
async def worker_v1(ctx: core.Flow, _job: Job) -> int:
    await ctx.wait(ask("one"), SHORT, reply=Plan)
    await ctx.safe_point(Job(step=1))
    await ctx.wait(ask("two"), SHORT, reply=Plan)
    return 2


@core.flow(snapshot=Job, name="worker")
async def worker_v2(ctx: core.Flow, job: Job) -> int:
    if job.step < 1:
        await ctx.wait(ask("uno"), SHORT, reply=Plan)
        await ctx.safe_point(Job(step=1))
    await ctx.wait(ask("two"), SHORT, reply=Plan)  # same label: ids must still not repeat
    await ctx.wait(ask("three"), SHORT, reply=Plan)
    return 3


def root_for(worker: object):  # noqa: ANN201
    async def root(ctx: core.Flow, _config: Config) -> None:
        async with ctx.group() as workers:
            result = await workers.start("w", worker, Job())  # ty: ignore[invalid-argument-type]
        await ctx.finish(finish(f"worker gave {result}"))

    return root


def _run_v1() -> FlowRun[Config]:
    config = Config()
    search = core.FlowSearch(DECLARATION, root_for(worker_v1), config, flows=(worker_v1,))
    return FlowRun.start(search, config, FACTS)


def _upgrade(run: FlowRun[Config]) -> None:
    run.search = core.FlowSearch(DECLARATION, root_for(worker_v2), Config(), flows=(worker_v2,))


def test_after_a_code_change_flows_resume_from_their_latest_snapshot() -> None:
    run = _run_v1()
    run.reply(run.expect(core.AskAgent, label="one"), Plan(n=1))
    in_flight = run.expect(core.AskAgent, label="two")
    assert in_flight.decision.root == "w/two.1"

    _upgrade(run)
    run.reply(in_flight, Plan(n=2))  # the first event the new code sees: a result of the old run

    # the task resumed at step 1 by its key: no "uno"; numbering continued, so no id repeats
    assert [a.decision.root for a in run.open if a is not in_flight] == ["w/two.2"]
    assert [d.decision for d in run.state.dropped] == [in_flight.decision]
    assert run.state.base is not None
    assert len(run.state.journal) == 2  # the restart, then the dropped result

    run.reply(run.expect(core.AskAgent, label="two"), Plan(n=2))
    run.reply(run.expect(core.AskAgent, label="three"), Plan(n=3))
    assert run.finished is not None
    assert run.finished.result.reason == "worker gave 3"


def test_a_restarted_run_can_itself_be_restarted_from_json_after_every_step() -> None:
    run = _run_v1()
    run.reply(run.expect(core.AskAgent, label="one"), Plan(n=1))
    stale = run.expect(core.AskAgent, label="two")
    _upgrade(run)
    run.restarting = True
    run.reply(stale, Plan(n=2))
    run.reply(run.expect(core.AskAgent, label="two"), Plan(n=2))
    run.reply(run.expect(core.AskAgent, label="three"), Plan(n=3))
    assert run.finished is not None
    assert run.restarts >= 3


def test_a_published_record_and_a_stop_survive_a_restart() -> None:
    class Seen(core.Value):
        marker: str

    async def v1(ctx: core.Flow, _config: Config) -> None:
        ctx.publish(Seen(marker="before"))
        await ctx.wait(ask("a"), SHORT, reply=Plan)

    async def v2(ctx: core.Flow, _config: Config) -> None:
        ctx.publish(Seen(marker="before"))
        await ctx.wait(ask("renamed"), SHORT, reply=Plan)

    config = Config()
    run = FlowRun.start(core.FlowSearch(DECLARATION, v1, config, flows=()), config, FACTS)
    run.search = core.FlowSearch(DECLARATION, v2, config, flows=())
    run.stop()
    assert run.search.published(run.state, Seen) == Seen(marker="before")
    assert run.state.live.stop is not None
    assert run.state.base is not None


def test_without_registered_flows_a_code_change_is_refused() -> None:
    async def v1(ctx: core.Flow, _config: Config) -> None:
        await ctx.wait(ask("a"), SHORT, reply=Plan)
        await ctx.wait(ask("b"), SHORT, reply=Plan)

    async def v2(ctx: core.Flow, _config: Config) -> None:
        await ctx.wait(ask("a"), SHORT, reply=Plan)
        await ctx.wait(ask("c"), SHORT, reply=Plan)

    run = start(v1)
    run.reply(run.expect(core.AskAgent), Plan(n=1))
    changed = core.FlowSearch(DECLARATION, v2, Config())
    with pytest.raises(core.FlowCodeChanged) as caught:
        changed.update(run.state, core.TimeReached(time=1.0), run.view.current())
    assert caught.value.step == 1


def test_a_flow_that_is_not_registered_cannot_be_restarted() -> None:
    async def loose(ctx: core.Flow) -> None:
        await ctx.wait(ask("loose"), SHORT, reply=Plan)

    async def parent(ctx: core.Flow, _config: Config) -> None:
        async with ctx.group() as tasks:
            tasks.start("l", loose)

    async def parent_v2(ctx: core.Flow, _config: Config) -> None:
        await ctx.wait(ask("other"), SHORT, reply=Plan)

    config = Config()
    run = FlowRun.start(core.FlowSearch(DECLARATION, parent, config, flows=()), config, FACTS)
    changed = core.FlowSearch(DECLARATION, parent_v2, config, flows=())
    with pytest.raises(core.FlowCodeChanged):
        changed.update(run.state, core.TimeReached(time=1.0), run.view.current())


def test_only_a_registered_flow_has_safe_points() -> None:
    async def sloppy(ctx: core.Flow) -> None:
        await ctx.safe_point(Job())

    async def parent(ctx: core.Flow, _config: Config) -> None:
        async with ctx.group() as tasks:
            tasks.start("s", sloppy)

    with pytest.raises(core.ContractError, match="registered"):
        start(parent)


def test_a_snapshot_must_be_the_registered_type() -> None:
    async def wrong(ctx: core.Flow, _config: Config) -> None:
        await ctx.safe_point(Plan(n=1))

    with pytest.raises(core.ContractError, match="snapshots are"):
        start(wrong)
