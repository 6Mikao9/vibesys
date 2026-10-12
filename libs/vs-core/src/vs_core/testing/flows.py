"""Drive a ``FlowSearch`` by hand, as a script: no asyncio, no clock, no core.

``FlowRun`` plays the host. It starts the search, keeps the list of waits the search has
opened, and delivers the events a story names::

    run = FlowRun.start(search, config, facts)
    asked = run.expect(AskAgent, label="planner")   # the next open wait that matches
    run.reply(asked, Plan(...))                     # delivers TurnFinished for it
    run.fail(asked2, FailureReason.TIMED_OUT)
    run.advance_to(30.0)
    run.stop()
    assert run.finished is not None

``FlowRun.start(..., restarting=True)`` round-trips the state through JSON into a new
``FlowSearch`` after every step, so any story can be repeated as a crash-at-every-step test.
``FakeView`` is the ``RunView`` the search reads: set the clock, attempts and settlements on it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from vs_core.api import (
    AskAgent,
    ControlInput,
    Event,
    Failed,
    FailureReason,
    Finish,
    FlowSearch,
    FlowState,
    InvocationId,
    InvocationRef,
    RejectionCode,
    RunFacts,
    SessionId,
    StopRequested,
    TimeReached,
    TurnFinished,
    Wait,
    WakeAt,
    Work,
    run_view,
)
from vs_core.testing.builders import initial_state
from vs_core.types.common import ControlId, DecisionId

if TYPE_CHECKING:
    from pydantic import BaseModel

    from vs_core.api import Decision, RunView, Seconds, Within
    from vs_core.types.agents import Checkpoint


@dataclass
class FakeView:
    """The run view a search reads. Change the fields between steps; ``current`` is the view."""

    now_at: Seconds = 0.0
    changes: dict[str, object] = field(default_factory=dict)
    """Other ``RunView`` fields to override: ``attempts``, ``settlements``, ``capabilities``..."""

    def current(self) -> RunView:
        """The view as it stands now."""
        base = run_view(initial_state())
        return base.model_copy(update={"now_at": self.now_at, **self.changes})


@dataclass(frozen=True)
class Asked:
    """One wait the search opened: its decision, the request and its limit."""

    decision: DecisionId
    request: Work | Finish
    until: Within | None
    """The limit of a wait; None for a ``Finish``, which core answers only by refusing it."""

    @property
    def label(self) -> str:
        """The id root the author gave: ``a/planner.2`` was written as ``planner``."""
        return self.decision.root.rpartition("/")[2].rpartition(".")[0]


class FlowRun[C]:
    """A flow search being played by a story. See the module docstring."""

    def __init__(
        self,
        search: FlowSearch[C],
        *,
        view: FakeView | None = None,
        restarting: bool = False,
    ) -> None:
        """Build a run that has not started; ``start`` is the way in."""
        self.search = search
        self.view = view or FakeView()
        self.restarting = restarting
        self.state: FlowState
        self.decisions: list[Decision] = []
        """Every decision the search issued, in order."""
        self._open: list[Asked] = []
        self._sleeps: list[Seconds] = []
        self.finished: Finish | None = None
        """The ``Finish`` the search issued, once it did."""
        self.restarts = 0

    @classmethod
    def start(
        cls,
        search: FlowSearch[C],
        config: C,
        facts: RunFacts,
        *,
        view: FakeView | None = None,
        restarting: bool = False,
    ) -> FlowRun[C]:
        """Start the search and take its first decisions."""
        run = cls(search, view=view, restarting=restarting)
        run.state, issued = search.init(config, facts)
        run._take(issued)
        return run

    # -- what is open ------------------------------------------------------------------------

    @property
    def open(self) -> tuple[Asked, ...]:
        """The waits not yet answered, in the order they were opened."""
        return tuple(self._open)

    @property
    def sleeping_until(self) -> tuple[Seconds, ...]:
        """The wake-up times asked for and not yet reached."""
        return tuple(self._sleeps)

    def expect(self, kind: type[Work | Finish], *, label: str | None = None) -> Asked:
        """The first open wait for a ``kind`` request (with this id root, if given)."""
        for asked in self._open:
            if isinstance(asked.request, kind) and label in (None, asked.label):
                return asked
        wanted = f"{kind.__name__} {label or ''}".strip()
        have = [f"{type(a.request).__name__} {a.decision.root}" for a in self._open]
        message = f"no open wait for {wanted}; open: {have}"
        raise AssertionError(message)

    # -- delivering events -------------------------------------------------------------------

    def reply(
        self, asked: Asked, reply: BaseModel, *, checkpoint: Checkpoint | None = None
    ) -> None:
        """Deliver the agent's ``reply`` to an ``AskAgent`` wait."""
        assert isinstance(asked.request, AskAgent), "only an AskAgent has a reply"  # noqa: S101  # LW-220426 [S101]; a story error, reported as the failed assertion it is.
        invocation = InvocationRef(
            session_id=SessionId(root="session"),
            invocation_id=InvocationId(root=f"invocation-{len(self.state.journal)}"),
            generation=0,
        )
        self._close(asked)
        self.send(
            TurnFinished(
                decision=asked.decision, invocation=invocation, reply=reply, checkpoint=checkpoint
            )
        )

    def fail(self, asked: Asked, reason: FailureReason, **fields: object) -> None:
        """Deliver ``Failed(reason)`` for a wait; ``fields`` complete the reason's own fields."""
        defaults: dict[str, object] = {}
        if reason is FailureReason.TIMED_OUT:
            defaults["limit_at"] = self.view.now_at
        if reason is FailureReason.REFUSED:
            defaults["cause"] = RejectionCode.BUDGET
        self._close(asked)
        self.send(Failed(reason=reason, decision=asked.decision, **{**defaults, **fields}))  # ty: ignore[invalid-argument-type]

    def answer(self, asked: Asked, event: Event) -> None:
        """Deliver any event as the answer to a wait (a workspace opened, an operation result)."""
        self._close(asked)
        self.send(event)

    def stop(self) -> None:
        """The operator stops the run."""
        self.send(
            StopRequested(control=ControlInput(control_id=ControlId(root="stop"), action="stop"))
        )

    def advance_to(self, time: Seconds) -> None:
        """Move the clock to ``time`` and deliver ``TimeReached`` for it."""
        self.view.now_at = time
        self._sleeps = [t for t in self._sleeps if t > time]
        self.send(TimeReached(time=time))

    def send(self, event: Event) -> None:
        """Deliver an event as it is, then restart if the run is restarting after each step."""
        self.state, issued = self.search.update(self.state, event, self.view.current())
        self._take(issued)
        if self.restarting:
            self.restart()

    def restart(self) -> None:
        """Round-trip the state through JSON and continue with a new ``FlowSearch``."""
        self.restarts += 1
        self.state = FlowState.model_validate_json(self.state.model_dump_json())
        self.search = self.search.fresh()

    # -- bookkeeping -------------------------------------------------------------------------

    def _take(self, issued: list[Decision]) -> None:
        self.decisions.extend(issued)
        for decision in issued:
            match decision:
                case Wait(request=request, until=until):
                    self._open.append(Asked(request.decision_id, request, until))
                case WakeAt(time=time):
                    self._sleeps.append(time)
                case Finish():
                    self.finished = decision
                    self._open.append(Asked(decision.decision_id, decision, None))

    def _close(self, asked: Asked) -> None:
        if asked in self._open:
            self._open.remove(asked)
