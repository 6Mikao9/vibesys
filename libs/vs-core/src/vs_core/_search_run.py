"""Run a search against core: deliver events one at a time, submit what it decides.

The shell hands this module the events that happened; it calls ``Search.update`` once per
event, submits the decisions with the core step the shell supplies, and keeps feeding the
events those steps publish until the search has nothing more to say. Everything happens
inside one call, so the shell commits search state, core state and published events
together. A ``WakeAt`` is not submitted to core: it is returned for the shell to wait on.

Also here: the startup check of the event kinds a search declares, and the runtime check
that backs up the type checker for what a search returns.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from ._registry import ContractError
from ._search_events import run_view, translate
from .types.common import RunStatus
from .types.kernel import ProposalSubmitted
from .types.kinds import OPT_IN, EventKind, WorkKind
from .types.records import RecoveryPhase
from .types.strategy import Finish, Wait, WakeAt
from .types.until import Within

if TYPE_CHECKING:
    from collections.abc import Callable

    from ._registry import OperationRegistry
    from .types.common import Seconds
    from .types.kernel import CoreEvent, CoreState, StrategyEvent, Transition
    from .types.search import Discarded, Event, Search
    from .types.strategy import Decision, SearchDeclaration, StrategyState, Submission

type CoreStep = Callable[[CoreState, CoreEvent], Transition]
"""The core step the shell uses (production or a fake of it)."""


def check_decisions(declaration: SearchDeclaration, decisions: object) -> tuple[Decision, ...]:
    """The runtime backstop for what a search returned (the type checker is the first line).

    A decision is a ``Wait`` with a ``Within``, a ``WakeAt`` or a ``Finish``, and only of a
    kind the search declared it uses. A bare request, a bare watch, a bare number or a wait
    without a limit is refused with the path of the offending item.
    """
    if not isinstance(decisions, list | tuple):
        raise ContractError(("decisions",), "a search returns a list of decisions")
    checked: list[Decision] = []
    for index, decision in enumerate(decisions):
        path = ("decisions", index)
        match decision:
            case Wait():
                if not isinstance(decision.until, Within):
                    raise ContractError((*path, "until"), "a wait needs a Within, not a bare watch")
                kind = WorkKind(decision.request.kind)
            case WakeAt():
                kind = WorkKind.WAKE_AT
            case Finish():
                checked.append(decision)
                continue
            case _:
                raise ContractError(
                    path, "a search returns Wait, WakeAt or Finish; start work inside a Wait"
                )
        if kind not in declaration.uses:
            raise ContractError((*path, "request"), "work kind is not in the declaration's uses")
        checked.append(decision)
    return tuple(checked)


def _deliverable(declaration: SearchDeclaration, event: Event) -> bool:
    """Whether ``event`` reaches the search. Opt-in kinds the search skipped are core's."""
    kind = EventKind(event.kind)
    if kind in declaration.handles:
        return True
    if kind in OPT_IN:
        return False
    raise ContractError(
        ("event", event.kind), "event kind was not declared in the search's handles"
    )


@dataclass(frozen=True)
class Inbox:
    """What waits for the search: events to deliver, and decisions core could not take yet.

    ``discarded`` are events core already kept from the search (see ``translate``); they
    are passed on in ``Advanced.discarded`` so the shell logs everything in one place.
    """

    events: tuple[Event, ...] = ()
    held: tuple[Decision, ...] = ()
    discarded: tuple[Discarded, ...] = ()


@dataclass(frozen=True)
class Advanced[S: StrategyState]:
    """What one call changed: the search state, core, the events core published, the wakes.

    ``held`` are decisions the search made while core could not take them (recovery is not
    ready); the shell keeps them and passes them back in the next ``Inbox``.
    """

    state: S
    core: CoreState
    discarded: tuple[Discarded, ...] = field(default=())
    published: tuple[StrategyEvent, ...] = field(default=())
    wakes: tuple[Seconds, ...] = field(default=())
    submitted: tuple[Submission, ...] = field(default=())
    held: tuple[Decision, ...] = field(default=())


@dataclass(frozen=True)
class SearchHost:
    """What the shell lends the search loop: core's step and the operation codec."""

    step: CoreStep
    registry: OperationRegistry


@dataclass
class _Pass:
    """One call's working set: events waiting for the search, and what the call produced."""

    host: SearchHost
    queue: list[Event]
    published: list[StrategyEvent] = field(default_factory=list)
    wakes: list[Seconds] = field(default_factory=list)
    sent: list[Submission] = field(default_factory=list)
    held: list[Decision] = field(default_factory=list)
    discarded: list[Discarded] = field(default_factory=list)

    def submit(self, decisions: tuple[Decision, ...], core: CoreState) -> CoreState:
        """Send the decisions to core, or hold them until recovery is ready."""
        self.wakes.extend(d.time for d in decisions if isinstance(d, WakeAt))
        work = tuple(d for d in decisions if isinstance(d, Wait | Finish))
        if not work or core.run.status == RunStatus.TERMINAL:
            return core
        if core.records.recovery.phase != RecoveryPhase.READY:
            self.held.extend(work)
            return core
        submissions = tuple(self.host.registry.validate_submission(d) for d in work)
        self.sent.extend(submissions)
        transition = self.host.step(
            core, ProposalSubmitted(decisions=submissions, expected_revision=core.revision)
        )
        self.published.extend(transition.events)
        translated = translate(transition.state, transition.events)
        self.queue.extend(translated.events)
        self.discarded.extend(translated.discarded)
        return transition.state

    def drain[S: StrategyState, C](
        self, search: Search[S, C], state: S, core: CoreState
    ) -> Advanced[S]:
        """Deliver queued events one at a time until the search has nothing more to say."""
        declaration = search.declaration
        while self.queue:
            event = self.queue.pop(0)
            if not _deliverable(declaration, event):
                continue
            state, decisions = search.update(state, event, run_view(core))
            core = self.submit(check_decisions(declaration, decisions), core)
        return Advanced(
            state=state,
            core=core,
            discarded=tuple(self.discarded),
            published=tuple(self.published),
            wakes=tuple(self.wakes),
            submitted=tuple(self.sent),
            held=tuple(self.held),
        )


def advance[S: StrategyState, C](
    search: Search[S, C], state: S, core: CoreState, host: SearchHost, inbox: Inbox
) -> Advanced[S]:
    """Submit what was held, deliver the events one by one, and feed back what that causes."""
    run = _Pass(host, list(inbox.events), discarded=list(inbox.discarded))
    core = run.submit(inbox.held, core)
    return run.drain(search, state, core)


def begin[S: StrategyState, C](
    search: Search[S, C],
    config: C,
    core: CoreState,
    host: SearchHost,
    arrived: Inbox | None = None,
) -> Advanced[S]:
    """Start a new run: ``init`` on the run's facts, submit its decisions, then ``arrived``."""
    state, decisions = search.init(config, core.run.facts)
    held = check_decisions(search.declaration, decisions)
    return advance(search, state, core, host, replace(arrived or Inbox(), held=held))
