"""A stop is a pause the operator can lift: the run goes on with its history.

An explicit resume lifts a stop that stands (``StopLifted``). The flow runtime then takes the
stop back from every flow it has not yet interrupted, and a flow that already took ``Stopped``
stays ended. Each story runs live and with a restart after every step (``begin``), so the
journal replays both events and the replay tells the same story. Core's own rule, which
accepts the resume exactly while the run has not committed its ending, is told in the second
half.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

import vs_core.api as core
from vs_core.api.testing import FAKE_REGISTRY, FakeView

from .flow_support import Config, Plan, answer_of, ask, start
from .test_flows import Begin, answer, ended, stop_aware, waits, worker
from .test_flows_codec import ask_twice

if TYPE_CHECKING:
    from vs_core.api.testing import FlowRun

_ROUNDS = 3


@pytest.fixture(params=[False, True], ids=["live", "restart-every-step"])
def begin(request: pytest.FixtureRequest) -> Begin:
    """Starts a flow; the fixture's two settings are a live run and a restart after every step."""
    restarting = bool(request.param)
    return lambda root, view=None, adopting=True: start(
        root, restarting=restarting, view=view, adopting=adopting
    )


class History(core.Value):
    """The answers a search has collected, in order, and whether a stop cut it short."""

    answers: tuple[int, ...] = ()
    cut_short: bool = False


async def rounds(ctx: core.Flow, _config: Config) -> core.NoWinner:
    """Ask for ``_ROUNDS`` answers one after the other, between which a standing stop ends it."""
    history = History()
    for index in range(_ROUNDS):
        if ctx.stop_requested:
            ctx.publish(history.model_copy(update={"cut_short": True}))
            return core.NoWinner(reason="stopped")
        history = history.model_copy(
            update={"answers": (*history.answers, answer_of(await ask(ctx, f"round-{index}")))}
        )
        ctx.publish(history)
    return core.NoWinner(reason="done")


def _history(run: FlowRun[Config]) -> History:
    found = run.published(History)
    assert found is not None
    return found


def _play(run: FlowRun[Config], *, stop_in: int, lifted: bool) -> None:
    """Answer every round; the stop (and its lift) arrive while round ``stop_in`` is asked."""
    for index in range(_ROUNDS):
        if run.finished is not None:
            return
        asked = run.expect_turn(f"round-{index}")
        if index == stop_in:
            run.stop()
            if lifted:
                run.lift_stop()
        run.reply(asked, Plan(n=index * 10))


@pytest.mark.parametrize("stop_in", range(_ROUNDS))
def test_a_lifted_stop_leaves_the_search_with_every_round_it_would_have_had(
    begin: Begin,
    stop_in: int,
) -> None:
    """Regression: a stop recorded in the run's state could never be taken back by a resume."""
    run = begin(rounds)
    _play(run, stop_in=stop_in, lifted=True)
    assert _history(run) == History(answers=(0, 10, 20))
    assert ended(run).reason == "no-winner: done"
    assert not run.state.live.stop


@pytest.mark.parametrize("stop_in", range(_ROUNDS))
def test_a_stop_that_stands_still_cuts_the_search_short_after_the_round_in_flight(
    begin: Begin,
    stop_in: int,
) -> None:
    run = begin(rounds)
    _play(run, stop_in=stop_in, lifted=False)
    last = stop_in == _ROUNDS - 1
    assert _history(run) == History(
        answers=tuple(10 * i for i in range(stop_in + 1)), cut_short=not last
    )


def test_a_stop_after_a_lift_stands_again(begin: Begin) -> None:
    run = begin(rounds)
    first = run.expect_turn("round-0")
    run.stop()
    run.lift_stop()
    run.stop("stop-2")
    assert run.state.live.stop is not None
    run.reply(first, Plan(n=0))
    assert _history(run) == History(answers=(0,), cut_short=True)


def test_a_lift_with_no_stop_standing_changes_nothing(begin: Begin) -> None:
    run = begin(rounds)
    run.lift_stop()
    _play(run, stop_in=-1, lifted=False)
    assert _history(run) == History(answers=(0, 10, 20))


def test_a_stop_a_task_has_not_yet_taken_is_lifted_with_it(begin: Begin) -> None:
    """A stop that came during a shielded setup is pending: the lift cancels it."""
    run = begin(stop_aware)
    run.stop()  # while both workstreams are still opening
    run.lift_stop()
    answer(run, "open")
    answer(run, "open")
    assert {"a/work", "b/work"} <= set(waits(run))
    answer(run, "work")
    answer(run, "work")
    assert waits(run)[-2:] == ["a/settle", "b/settle"]  # the workstreams settle as done
    answer(run, "settle")
    answer(run, "settle")
    answer(run, "plan", 5)
    reason = ended(run).reason
    assert "a done | b done" in reason


def test_a_task_the_stop_already_ended_stays_ended_after_the_lift(begin: Begin) -> None:
    """Work the stop ended is not brought back: its task ended as stopped."""
    run = begin(stop_aware)
    for asked in list(run.open):
        if asked.prompt is not None and asked.prompt.text == "open":
            run.reply(asked, Plan(n=0))
    run.stop()
    assert waits(run)[-2:] == ["a/settle-stopped", "b/settle-stopped"]
    run.lift_stop()
    answer(run, "settle-stopped")
    answer(run, "settle-stopped")
    answer(run, "plan", 5)
    reason = ended(run).reason
    assert "a done | b done" in reason  # the workstreams returned what they were stopped with
    assert not run.state.live.stop


def test_a_run_saved_stopped_by_the_previous_version_resumes_and_lifts() -> None:
    """A saved envelope holds the stop in core's controls and in the flows' state; both lift."""
    saved = Path(__file__).parent / "envelopes" / "v36-stopped.json"
    envelope = FAKE_REGISTRY.decode_envelope(
        core.RunEnvelope[core.FlowState], saved.read_text(encoding="utf-8")
    )
    assert envelope.schema_version == core.ENVELOPE_SCHEMA_VERSION
    assert core.requested_stop_mode(envelope.core.run) is core.StopMode.DRAIN
    assert envelope.strategy.live.stop is not None

    resume = core.ControlInput(
        control_id=core.ControlId(root="resume"), action="resume", when="now"
    )
    transition = core.step(envelope.core, core.RunControlEvent(control=resume, now_at=1.0))
    lifted = next(e for e in transition.events if isinstance(e, core.StopLiftRecorded))
    assert core.requested_stop_mode(transition.state.run) is None

    run = start(ask_twice)
    flows, _ = run.driver.update(
        envelope.strategy, core.StopLifted(control=lifted.control), FakeView().current()
    )
    assert flows.live.stop is None


@pytest.mark.regression
@example(count=17, restarting=True)
@given(count=st.integers(min_value=1, max_value=24), restarting=st.booleans())
def test_new_epoch_replays_every_live_wait_instead_of_restarting_from_old_checkpoints(
    count: int, *, restarting: bool
) -> None:
    async def pending(ctx: core.Flow, config: Config) -> core.NoWinner:
        ctx.checkpoint(config)
        async with ctx.group() as tasks:
            for index in range(count):
                tasks.start(str(index), worker, str(index))
            answers: list[int] = []
            async for value in tasks:
                assert isinstance(value, int)
                answers.append(value)
        return core.NoWinner(reason=str(sum(answers)))

    run = start(pending, restarting=restarting)
    admitted = tuple(run.open)
    before = tuple(run.decisions)
    run.send(
        core.StopLifted(
            control=core.ControlInput(
                control_id=core.ControlId(root="epoch:2"), action="resume", when="now"
            ),
            new_epoch=True,
        )
    )
    assert tuple(run.decisions) == before
    for index, asked in enumerate(admitted):
        run.reply(asked, Plan(n=index))
    assert ended(run).reason == f"no-winner: {sum(range(count))}"
    assert not run.state.dropped
    assert not run.state.restart_settled


# -- a resume that reopens an ended run -------------------------------------------------------


class Marker(core.Value):
    """Published each time the root starts, so a test sees a restart."""

    started: int


async def waits_for_a_decision(ctx: core.Flow, config: Config) -> core.NoWinner:
    """A root winding down: it asked for the winner to be adopted and waits for the answer."""
    ctx.checkpoint(config)
    ctx.publish(Marker(started=len(ctx.settlements)))
    await ask(ctx, "adopt")
    return core.NoWinner(reason="adopted")


def _asked_so_far(run: FlowRun[Config]) -> int:
    """How many decisions the flows have made, from the start of the run."""
    return len(run.decisions)


def test_a_resume_that_reopens_an_ended_run_asks_again_for_the_decision_core_never_took(
    begin: Begin,
) -> None:
    """Regression: the root waited for ever on a decision the ended run's core never kept.

    A run core ended while the root was adopting its winner is reopened by a resume. The adoption
    the root waits for belongs to the authority that ended: nothing will ever answer it, and
    the root, not concluded, was left idle (`run.concluded` was the only reason to restart).
    """
    run = begin(waits_for_a_decision)
    before = _asked_so_far(run)
    run.stop("recovery-blocked")
    run.lift_stop(new_epoch=True, ended=True)
    assert run.state.base is not None, "every flow started again from its checkpoint"
    assert _asked_so_far(run) > before, "the root asked again"


@pytest.mark.parametrize("new_epoch", [False, True], ids=["same-epoch", "new-epoch"])
def test_a_resume_of_a_run_core_did_not_end_leaves_the_flows_waiting_for_their_decisions(
    begin: Begin, *, new_epoch: bool
) -> None:
    """A pause, a park or a stop the search is still taking: core holds what the flows await."""
    run = begin(waits_for_a_decision)
    before = _asked_so_far(run)
    run.stop("pause")
    run.lift_stop(new_epoch=new_epoch)
    assert run.state.base is None
    assert _asked_so_far(run) == before


class Resume(StrEnum):
    """How a run was resumed: where core stood when the operator's resume came."""

    LIVE = "live"
    """Core had not ended the run: it was paused, parked or stopped, and holds the decisions."""
    ENDED = "ended"
    """Core had ended the run, and the resume reopened it."""


@given(
    stops=st.integers(0, 3), resumes=st.lists(st.sampled_from(list(Resume)), min_size=1, max_size=4)
)
def test_no_flow_awaits_a_decision_of_an_authority_that_ended(
    stops: int, resumes: list[Resume]
) -> None:
    """Whatever stopped and resumed before: a resume of an ended run asks every wait again."""
    run = start(waits_for_a_decision, restarting=False)
    for index, resume in enumerate(resumes):
        before = _asked_so_far(run)
        if index < stops:
            run.stop(f"stop-{index}")
        run.lift_stop(f"resume-{index}", new_epoch=True, ended=resume is Resume.ENDED)
        assert (_asked_so_far(run) > before) is (resume is Resume.ENDED)
