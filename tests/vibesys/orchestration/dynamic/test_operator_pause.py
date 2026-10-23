"""An operator Pause of the dynamic search settles what it admitted, and a relaunch ends it as no pause would.

The pause lands before any request of a one- or two-wide run, so it meets a planner turn, an
implementer turn, an evaluation that is running or ended, the interpretation of its evidence,
the judge's prompt and turn, and a settlement. The run drains, parks with no admitted attempt
open and nothing waiting on the clock, and the operator relaunches it as the next epoch. This is
the product-level half of ``libs/vs-core/tests/test_operator_pause_drain.py``: the same rule on
the real search, which also issues the interpretation of evidence and the judge's prompt render
as operations of the attempt.
"""

from __future__ import annotations

import enum
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from hypothesis import assume, example, given, note, settings
from hypothesis import strategies as st
from tests.support.dynamic_script import REGISTRY, config, harness
from tests.vibesys.dynamic_world import DynamicWorld
from tests.vibesys.orchestration.dynamic._replies import (
    implement,
    implemented,
    plan_reply,
    reviewed,
)

from vibesys.orchestration.dynamic.search.api import MergeOutcome, RunSnapshot, dynamic
from vs_core.api import (
    AttemptPhase,
    ContinuationPhase,
    ControlId,
    ControlInput,
    EpochResumed,
    FlowDriver,
    PauseOwner,
    RecoveryStarted,
    RunControlEvent,
    RunStatus,
)
from vs_core.api.testing import Faults, drive
from vs_sim.api.testing import examples

if TYPE_CHECKING:
    from vs_core.api import CoreEvent, CoreState, FlowState, Request
    from vs_core.api.testing import Answer, Trace

_STEPS = 4000
"""A hang guard: a run still going after this many steps is stuck."""
# Two whole runs of the dynamic search per example (with and without the pause): PR depth draws few.
_SETTINGS = settings(max_examples=examples(pr=4, nightly=40), deadline=None, database=None)


@dataclass
class _Operator:
    """The far side of the run (``world``) and the operator who pauses it and relaunches it."""

    world: DynamicWorld
    pause_at: int | None
    seen: int = 0
    paused: bool = False
    parked: list[CoreState] = field(default_factory=list)

    def __call__(self, request: Request, core: CoreState) -> Answer:
        """What the executors report for ``request``."""
        return self.world(request, core)

    def during_turn(self, request: Request, core: CoreState) -> tuple[CoreEvent, ...]:
        """The Pause, just before the ``pause_at``-th request is answered if the run is open."""
        del request
        ordinal = self.seen
        self.seen += 1
        if ordinal != self.pause_at or core.run.status is not RunStatus.RUNNING or core.run.result:
            return ()
        self.paused = True
        control = ControlInput(
            control_id=ControlId(root="pause"), action="pause", owner=PauseOwner.OPERATOR
        )
        return (RunControlEvent(control=control, now_at=core.run.now_at),)

    def operator(self, core: CoreState) -> tuple[CoreEvent, ...]:
        """The relaunch, once the run parked."""
        if core.run.status is not RunStatus.PARKED:
            return ()
        self.parked.append(core)
        return (EpochResumed(epoch=core.records.recovery.epoch + 1, now_at=core.run.now_at),)


class Shape(enum.StrEnum):
    """What the scripted run does: the planner's rounds, what candidates measure, how judged."""

    ONE_ROUND = "one-round"
    TWO_ROUNDS = "two-rounds"
    MIXED = "mixed"
    """Two rounds, a candidate whose benchmark fails, and a judge that sends one back once."""


def _world(width: int, shape: Shape) -> DynamicWorld:
    first = plan_reply(*(implement(f"h{n}") for n in range(width)))
    second = plan_reply(*(implement(f"g{n}") for n in range(width)))
    rounds = 1 if shape is Shape.ONE_ROUND else 2
    hypotheses = width * rounds
    return DynamicWorld(
        planner=[first] if rounds == 1 else [first, second],
        implementer=[implemented() for _ in range(hypotheses * 2)],
        judge=(
            [reviewed(passed=False), *(reviewed() for _ in range(hypotheses * 2))]
            if shape is Shape.MIXED
            else [reviewed() for _ in range(hypotheses)]
        ),
        throughputs=(None, 120.0) if shape is Shape.MIXED else (100.0,),
        max_in_flight=width,
        max_rounds=rounds,
    )


def _run(
    width: int, pause_at: int | None, shape: Shape, crash: int | None = None
) -> tuple[Trace[FlowState], _Operator]:
    selected = config(max_in_flight=width, max_rounds=1 if shape is Shape.ONE_ROUND else 2)
    operator = _Operator(world=_world(width, shape), pause_at=pause_at)
    trace = drive(
        dynamic.driver(selected, REGISTRY),
        selected,
        operator,
        harness(
            selected,
            shell_events=True,
            during_turn=operator.during_turn,
            operator=operator.operator,
            events=(RecoveryStarted(epoch=1, now_at=0.0),),
            max_steps=_STEPS,
        ),
        None if crash is None else Faults(crash_after=frozenset({crash})),
    )
    return trace, operator


def _moved_to(snapshot: RunSnapshot) -> bool:
    """Whether main is a revision a recorded merge or fast-forward moved it to."""
    return snapshot.main in {
        item.revision
        for item in snapshot.merges
        if item.outcome is MergeOutcome.MERGED and item.revision is not None
    }


def _snapshot(trace: Trace[FlowState]) -> RunSnapshot:
    snapshot = FlowDriver.published(trace.state, RunSnapshot)
    assert snapshot is not None
    return snapshot


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
@example(width=1, pause_at=20, shape=Shape.ONE_ROUND, crash=None)
@example(width=2, pause_at=45, shape=Shape.ONE_ROUND, crash=None)
@example(width=1, pause_at=20, shape=Shape.ONE_ROUND, crash=70)  # the host dies while it drains
@given(
    width=st.integers(1, 2),
    pause_at=st.integers(0, 160),
    shape=st.sampled_from(tuple(Shape)),
    crash=st.none() | st.integers(1, 400),
)
def test_a_pause_settles_the_admitted_attempts_and_the_relaunch_ends_as_no_pause_would(
    width: int, pause_at: int, shape: Shape, crash: int | None
) -> None:
    # The scripted judge sends back whichever candidate it meets first, so two candidates
    # in flight do not meet it in an order a pause leaves alone.
    assume(shape is not Shape.MIXED or width == 1)
    normal, _ = _run(width, None, shape)
    paused, operator = _run(width, pause_at, shape, crash)
    note(
        f"paused before request {pause_at}: {operator.paused}, parked {len(operator.parked)} times"
    )
    assert paused.finished
    assert paused.core.run.status is RunStatus.TERMINAL
    for parked in operator.parked:
        assert _open_work(parked) == []
        # It parked when the work ran out, not when the clock reached the run's deadline.
        assert parked.run.now_at < parked.run.deadline_at
    assert paused.core.run.result is not None
    assert paused.core.run.result.outcome != "blocked", paused.core.run.result.reason
    # No effect ran twice: a Pause drains, it does not cancel and repeat.
    assert max(Counter(paused.effects).values(), default=0) == 1
    before, after = _snapshot(normal), _snapshot(paused)
    assert after.baseline == before.baseline
    assert {row.plan.work_id for row in after.finished} == {
        row.plan.work_id for row in before.finished
    }
    if width == 1 and shape is Shape.ONE_ROUND:
        assert after.main == before.main
    else:
        # Main moves to the first settled candidate built on it that beats it, so it follows
        # the order the candidates settled in, and a later round is planned against the main of
        # the moment. A pause may change both; either way a recorded move left main.
        assert after.main == before.main or all(_moved_to(snapshot) for snapshot in (before, after))
