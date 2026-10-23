"""A decision the search made after the run ended is asked again when the run is resumed.

Core takes nothing from a terminal run and tells the search nothing about it, so a search that
decides something then waits for the answer to a decision nobody has. The resume reopens the
run, but a search that waited only for what core took went on waiting for ever (the idle resumed
run). These tests run a flow on the real core with the executors' answers scripted by the test,
and drive the shell as it does: core steps, then the search is told what core owes it.
"""

from __future__ import annotations

from typing import Literal

import vs_core.api as core
from vs_core.api.testing import fake_search

from .flow_support import ROLES, Config
from .test_measurements import plan
from .test_resume_measure_decision import WITHIN, Host

WAKE = 500.0
FOREVER = 1.0e9


class Heard(core.Value):
    """What the root published once its measurement was answered."""

    kind: Literal["heard"] = "heard"
    status: str


@fake_search("sleeping-measurer", Config, ROLES)
async def sleeping_measurer(ctx: core.Flow, config: Config) -> None:
    """Sleep until ``WAKE``, then measure the input, say what came back, and wait."""
    ctx.checkpoint(config)
    await ctx.first(ctx.timer(WAKE))
    answer = await ctx.evaluation.measure(
        plan(purpose="baseline", submitted_at=ctx.clock.now()), within=WITHIN
    )
    failed = isinstance(answer, core.Failed)
    ctx.publish(Heard(status="failed" if failed else answer.status.value))
    await ctx.first(ctx.timer(FOREVER))


STOP = core.ControlInput(
    control_id=core.ControlId(root="stop"), action="stop", mode=core.StopMode.CANCEL
)
ENDING = core.RunResultProposal(outcome="cancelled", reason="operator stop")


def _ended_run() -> Host:
    """A run core ended while the root slept, then the root's alarm went off."""
    host = Host.started(sleeping_measurer)
    host.event(core.RecoveryStarted(epoch=1, now_at=1.0))
    host.event(core.RunControlEvent(control=STOP, result=ENDING, now_at=host.core.run.now_at + 1.0))
    host.event(core.ClockAdvanced(now_at=host.core.run.now_at + 1.0))
    assert host.core.run.status is core.RunStatus.TERMINAL
    host.alarm(WAKE)
    return host


def test_a_decision_made_after_the_run_ended_is_asked_again_by_the_resume() -> None:
    """Regression: the root woke up, asked for its measurement and was never answered.

    Core dropped the measurement (a terminal run takes no decision), so no request exists and no
    answer will come. The resume reopened the run and lifted the stop, and the root, which waits
    only for that measurement, sat idle: it was restarted only when it had itself concluded.
    """
    host = _ended_run()
    assert host.submissions == [], "core took nothing from the ended run"
    assert core.run_view(host.core, host.held).taken == tuple(
        receipt.decision_id for receipt in host.core.run.receipts
    )
    epoch = host.core.records.recovery.epoch + 1
    host.event(core.EpochResumed(epoch=epoch, now_at=host.core.run.now_at + 5.0))
    host.event(core.ClockAdvanced(now_at=host.core.run.now_at + 1.0))
    assert host.core.run.status is core.RunStatus.RUNNING
    host.alarm(host.core.run.now_at + WAKE)
    (request,) = host.submissions
    host.end_run(request)
    assert host.driver.published(host.state, Heard) is not None
