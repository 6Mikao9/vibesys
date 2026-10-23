"""The production loop drains an operator Pause to a parked run, and a resumed host ends it.

The pause lands before any request of a scripted dynamic run on the production shell and loop.
The loop must return the parked run on its own: a drain that held a continuation of an admitted
attempt leaves open work with nothing runnable, which the loop reports as a stall
(``Run.halted``) and which only a signal would end in a product run. A host started by the
operator's Resume then ends the run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hypothesis import example, given, note, settings
from hypothesis import strategies as st
from tests.vibesys.dynamic_world import DynamicWorld
from tests.vibesys.orchestration.dynamic._replies import (
    implement,
    implemented,
    plan_reply,
    reviewed,
)

from vs_core.api import (
    AttemptPhase,
    ContinuationPhase,
    ControlId,
    ControlInput,
    PauseOwner,
    RunControlEvent,
    RunStatus,
)
from vs_project.api import FakeStateStore
from vs_runtime.api.testing import FakePublicationDelivery, HostOptions, scripted_host
from vs_sim.api.testing import examples

if TYPE_CHECKING:
    from collections.abc import Callable

    from vs_core.api import CoreEvent, CoreState, Request, Transition
    from vs_core.api.testing import Answer

# Two whole runs on the production shell per example (drained, then resumed): PR depth draws few.
_SETTINGS = settings(max_examples=examples(pr=4, nightly=40), deadline=None, database=None)


def _world(width: int) -> DynamicWorld:
    return DynamicWorld(
        planner=[plan_reply(*(implement(f"h{n}") for n in range(width)))],
        implementer=[implemented() for _ in range(width)],
        judge=[reviewed() for _ in range(width)],
        max_in_flight=width,
    )


class _PauseAt:
    """Answer like the dynamic world, and pause before the ``pause_at``-th request if the run is open."""

    admit: Callable[[CoreEvent, float], Transition] | None = None

    def __init__(self, world: DynamicWorld, pause_at: int | None) -> None:
        self._world = world
        self._pause_at = pause_at
        self._seen = 0
        self.paused = False

    def __call__(self, request: Request, core: CoreState) -> Answer:
        ordinal = self._seen
        self._seen += 1
        admit = self.admit
        if (
            ordinal == self._pause_at
            and admit is not None
            and core.run.status is RunStatus.RUNNING
            and core.run.result is None
        ):
            self.paused = True
            control = ControlInput(
                control_id=ControlId(root="pause"), action="pause", owner=PauseOwner.OPERATOR
            )
            admit(RunControlEvent(control=control, now_at=core.run.now_at), core.run.now_at)
        return self._world(request, core)


def _open_work(core: CoreState) -> list[str]:
    """What the run still holds open for an attempt it admitted."""
    waits = [
        f"wait {row.continuation_id.root}"
        for row in core.evaluation.continuations
        if row.phase in (ContinuationPhase.WAITING, ContinuationPhase.AUTHORIZED)
    ]
    attempts = [
        f"attempt {row.attempt_id.root}"
        for row in core.attempts.attempts
        if row.phase is AttemptPhase.ACTIVE
    ]
    return [*waits, *attempts]


@_SETTINGS
@example(width=1, pause_at=6)
@example(width=2, pause_at=12)
@given(width=st.integers(1, 2), pause_at=st.integers(0, 80))
def test_the_loop_returns_a_drained_pause_parked_and_a_resumed_host_ends_the_run(
    width: int, pause_at: int
) -> None:
    store = FakeStateStore()
    delivery = FakePublicationDelivery()
    world = _world(width)
    launch = world.setup.new_launch()
    script = _PauseAt(world, pause_at)
    first = scripted_host(
        launch,
        script,
        world.setup.harness,
        launch.search.reply_schemas,
        store=store,
        delivery=delivery,
    )
    drained = first.run()
    note(
        f"paused before request {pause_at}: {script.paused}; ended {drained.core.run.status.value}"
    )
    assert drained.halted is None, drained.halted
    if not script.paused:
        assert drained.core.run.status is RunStatus.TERMINAL
        return
    assert drained.core.run.status is RunStatus.PARKED
    assert _open_work(drained.core) == []
    first.shell.release_lease(now_at=first.clock.now())

    resumed = scripted_host(
        launch,
        script,
        world.setup.harness,
        launch.search.reply_schemas,
        HostOptions(resumed=True),
        store=store,
        delivery=delivery,
        clock_at=first.clock.now() + 1,
    )
    outcome = resumed.run()
    assert outcome.halted is None, outcome.halted
    assert outcome.core.run.status is RunStatus.TERMINAL
    assert outcome.core.run.result is not None
    assert outcome.core.run.result.outcome != "blocked", outcome.core.run.result.reason
