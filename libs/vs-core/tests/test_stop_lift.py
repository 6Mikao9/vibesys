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
from hypothesis import example, given, settings
from hypothesis import strategies as st

import vs_core.api as core
from vs_core.api.testing import FAKE_REGISTRY
from vs_sim.api.testing import examples

from .flow_support import Config, Plan, answer_of, ask, start
from .test_flows import Begin, answer, ended, stop_aware, waits, worker
from .test_flows_codec import ask_twice

if TYPE_CHECKING:
    from vs_core.api.testing import Asked, FlowRun

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
    holds = run.view.current().model_copy(
        update={
            "taken": tuple(
                core.DecisionId(root=source)
                for waiting in run.driver.waiting(envelope.strategy)
                for source in waiting.sources
            )
        }
    )
    flows, _ = run.driver.update(envelope.strategy, core.StopLifted(control=lifted.control), holds)
    assert flows.live.stop is None
    assert flows.base is None, "core holds what the flows wait for, so they were not restarted"


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


# -- a resume of a run whose decisions core dropped ------------------------------------------


async def two_rounds(ctx: core.Flow, config: Config) -> core.NoWinner:
    """A root that asks one turn after the other, checkpointed so a resume starts it again."""
    ctx.checkpoint(config)
    for index in range(_ROUNDS):
        await ask(ctx, f"round-{index}")
    return core.NoWinner(reason="done")


def _asked_so_far(run: FlowRun[Config]) -> int:
    """How many decisions the flows have made, from the start of the run."""
    return len(run.decisions)


def _open_turn(run: FlowRun[Config]) -> Asked | None:
    """The oldest turn the flows wait on, if any (a ``Finish`` is answered by nobody)."""
    return next((asked for asked in run.open if isinstance(asked.request, core.RequestTurn)), None)


def _answer_everything(run: FlowRun[Config]) -> None:
    """The host answers the turns that are open, until the flows ask no more."""
    for _ in range(10 * _ROUNDS):
        asked = _open_turn(run)
        if asked is None:
            return
        run.reply(asked, Plan(n=1))


def test_a_resume_asks_again_for_a_decision_core_dropped_when_it_ended_the_run(
    begin: Begin,
) -> None:
    """Regression: the root waited for ever on a decision the ended run's core never took.

    Core ended the run (recovery was blocked) while the root was between two turns. The next
    turn the root asked was dropped, as core drops what a terminal run is sent. The resume
    reopened the run, but the root, not concluded, still waited for the answer to a decision
    nobody had, and sat idle: `run.concluded` was the only reason to restart the flows.
    """
    run = begin(two_rounds)
    run.end_run()
    answer(run, "round-0")
    assert not run.open, "core dropped the decision the root asked next"
    run.stop("recovery-blocked")
    run.reopen()
    run.lift_stop(new_epoch=True)
    assert run.state.base is not None, "every flow started again from its checkpoint"
    _answer_everything(run)
    assert ended(run).reason == "no-winner: done"


@pytest.mark.parametrize("new_epoch", [False, True], ids=["same-epoch", "new-epoch"])
def test_a_resume_leaves_the_flows_waiting_for_the_decisions_core_holds(
    begin: Begin, *, new_epoch: bool
) -> None:
    """A pause, a park or a stop the search is still taking: core holds what the flows await."""
    run = begin(two_rounds)
    before = _asked_so_far(run)
    run.stop("pause")
    run.lift_stop(new_epoch=new_epoch)
    assert run.state.base is None
    assert _asked_so_far(run) == before


class Move(StrEnum):
    """What can happen to a run between the root's decisions: the host, the operator, core."""

    ANSWER = "answer"
    """The host answers the oldest open turn."""
    END = "end"
    """Core ends the run: it drops every decision it is sent."""
    REOPEN = "reopen"
    """Core takes decisions again."""
    STOP = "stop"
    LIFT = "lift"


@settings(max_examples=examples(pr=60, nightly=600))
@example(moves=[Move.END, Move.ANSWER], restarting=False)
@example(
    moves=[Move.ANSWER, Move.END, Move.ANSWER, Move.STOP, Move.REOPEN, Move.LIFT], restarting=True
)
@given(moves=st.lists(st.sampled_from(list(Move)), max_size=12), restarting=st.booleans())
def test_a_run_resumed_after_core_dropped_decisions_goes_on_to_its_end(
    moves: list[Move], *, restarting: bool
) -> None:
    """Whatever happened to the run before: after the resume the host's answers finish it.

    The root decides while core is ready, while it has ended the run, in any order with the
    operator's stops and lifts; the last step reopens the run and resumes it. The flows then
    wait only for decisions core holds, so answering what is open ends the search.
    """
    run = start(two_rounds, restarting=restarting)
    for number, move in enumerate(moves):
        match move:
            case Move.ANSWER if (asked := _open_turn(run)) is not None:
                run.reply(asked, Plan(n=1))
            case Move.END:
                run.end_run()
            case Move.REOPEN:
                run.reopen()
            case Move.STOP:
                run.stop(f"stop-{number}")
            case Move.LIFT:
                run.lift_stop(f"resume-{number}", new_epoch=True)
            case _:
                pass
    run.reopen()
    run.lift_stop("resume-last", new_epoch=True)
    _answer_everything(run)
    assert ended(run).reason == "no-winner: done"
