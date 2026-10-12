"""Translate what core published into the events a search receives, and project its view.

Core publishes lifecycle facts (``StrategyEvent``). A search receives past-tense events
that carry the decision they answer, and only events for its own decisions. Plumbing a
search never acts on (a turn that only suspends, a resume authorization, acceptance of a
decision) is handled by core and does not reach it. So are two kinds of event a search
must not see: the failure of a request core made for itself (no decision of the search's
is behind it), and a result that arrives after its request ended with a failure (the
search already heard ``Failed``). Both come back as ``Discarded`` for the shell to log.
Pure: it reads the state after the step and returns values.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ._registry import ContractError
from .types.attempts import AttemptExhausted
from .types.evaluation import MeasurementResult
from .types.kernel import ControlChanged
from .types.kinds import EventKind
from .types.records import OperationResult
from .types.scheduling import AttemptReady
from .types.search import (
    AdoptionFinished,
    AttemptSummary,
    Discarded,
    DiscardReason,
    Interrupted,
    MeasurementFinished,
    MessageDelivered,
    MessageDropped,
    OperationFinished,
    RunView,
    StopRequested,
    TurnFinished,
    WorkspaceClosed,
    WorkspaceOpened,
)
from .types.session_inputs import InputDelivered, InputDropped
from .types.sessions import InterruptCompleted, InterruptFailed, TurnResult
from .types.settlement import AdoptionFailed, AdoptionResult, AttemptSettled
from .types.strategy import Rejected
from .types.until import Failed, FailureReason

if TYPE_CHECKING:
    from collections.abc import Callable

    from .types.common import DecisionId, RequestId
    from .types.kernel import CoreState, StrategyEvent
    from .types.records import RequestRecord
    from .types.search import Event


def run_view(core: CoreState) -> RunView:
    """The narrow read-only view of a run that ``Search.update`` receives."""
    run = core.run
    return RunView(
        now_at=run.now_at,
        deadline_at=run.deadline_at,
        facts=run.facts,
        capabilities=run.capabilities,
        limits=run.limits,
        attempts=tuple(AttemptSummary.of(view) for view in core.attempts.attempts),
        measurements=core.evaluation.evidence,
        settlements=core.settlement.settlements,
    )


def _record(core: CoreState, request_id: RequestId) -> RequestRecord:
    for record in core.records.records:
        if record.request_id == request_id:
            return record
    raise ContractError(("request_id", request_id.root), "result for an unknown request")


def _decision_of(core: CoreState, request_id: RequestId) -> DecisionId | None:
    """The decision whose request produced ``request_id``; None for core's own requests."""
    return _record(core, request_id).request.decision_id


def _late(core: CoreState, request_id: RequestId, kind: EventKind) -> Discarded | None:
    """The discard for a result whose request already ended with a failure, else None.

    A record that ended (a limit passed, a watch fired) told its owner ``Failed``. A result
    that still arrives, such as the answer of a later inspection, is not delivered: the
    search has moved on from that decision.
    """
    if _record(core, request_id).failure is None:
        return None
    return Discarded(reason=DiscardReason.LATE_RESULT, request=request_id, event=kind)


def _closing_decision(core: CoreState, event: AttemptSettled) -> DecisionId | None:
    attempt = event.settlement.attempt
    for view in core.attempts.attempts:
        if view.attempt_id == attempt.attempt_id and view.generation == attempt.generation:
            return None if view.closure is None else view.closure.admission_id
    return None


def _exhausted(core: CoreState, event: AttemptExhausted) -> Event:
    admission = next(
        (
            view.admission_id
            for view in core.attempts.attempts
            if view.attempt_id == event.attempt.attempt_id
            and view.generation == event.attempt.generation
        ),
        None,
    )
    return Failed(reason=FailureReason.OUT_OF_BUDGET, decision=admission)


@dataclass(frozen=True)
class Translated:
    """What ``events`` came to: what the search receives, and what core kept to itself."""

    events: tuple[Event, ...] = ()
    discarded: tuple[Discarded, ...] = ()


def translate(core: CoreState, events: tuple[StrategyEvent, ...]) -> Translated:
    """The events ``core`` (the state after the step) owes the search for ``events``."""
    found = [_TRANSLATE.get(type(event), _ignored)(core, event) for event in events]
    return Translated(
        events=tuple(
            item for item in found if item is not None and not isinstance(item, Discarded)
        ),
        discarded=tuple(item for item in found if isinstance(item, Discarded)),
    )


def search_events(core: CoreState, events: tuple[StrategyEvent, ...]) -> tuple[Event, ...]:
    """Only the events part of ``translate``."""
    return translate(core, events).events


def _ignored(core: CoreState, event: StrategyEvent) -> None:
    """Acceptance, suspension plumbing and run end stay inside core."""
    del core, event


def _failed(core: CoreState, event: Failed) -> Event | Discarded:
    """A search hears only the failures of its own decisions.

    One without a decision ended a request core made for itself (a recovery check, a
    release, an inspection). Core owns those: the recovery barrier and the run status
    already react to them, and the shell logs the discard.
    """
    del core
    if event.decision is None:
        return Discarded(reason=DiscardReason.INTERNAL_FAILURE, failure=event)
    return event


def _rejected(core: CoreState, event: Rejected) -> Event:
    del core
    return Failed(reason=FailureReason.REFUSED, decision=event.decision_id, cause=event.code)


def _turn(core: CoreState, event: TurnResult) -> Event | Discarded | None:
    request = event.observation.request_id
    decision = _decision_of(core, request)
    if event.suspending or decision is None:
        return None
    return _late(core, request, EventKind.TURN_FINISHED) or TurnFinished(
        decision=decision, result=event
    )


def _measurement(core: CoreState, event: MeasurementResult) -> Event | Discarded:
    source = event.source_request
    if source is None:
        return MeasurementFinished(decision=None, result=event)
    return _late(core, source, EventKind.MEASUREMENT_FINISHED) or MeasurementFinished(
        decision=_decision_of(core, source), result=event
    )


def _operation(core: CoreState, event: OperationResult) -> Event | Discarded | None:
    request = event.observation.request_id
    decision = _decision_of(core, request)
    if decision is None:
        return None
    return _late(core, request, EventKind.OPERATION_FINISHED) or OperationFinished(
        decision=decision, result=event
    )


def _adoption(core: CoreState, event: AdoptionResult | AdoptionFailed) -> Event | Discarded:
    request = event.observation.request_id
    return _late(core, request, EventKind.ADOPTION_FINISHED) or AdoptionFinished(
        decision=_decision_of(core, request), result=event
    )


def _settled(core: CoreState, event: AttemptSettled) -> Event:
    return WorkspaceClosed(decision=_closing_decision(core, event), settlement=event.settlement)


def _ready(core: CoreState, event: AttemptReady) -> Event:
    del core
    return WorkspaceOpened(decision=event.admission_id, attempt=event.attempt)


def _control(core: CoreState, event: ControlChanged) -> Event:
    del core
    return StopRequested(control=event.control)


def _delivered(core: CoreState, event: InputDelivered) -> Event:
    del core
    return MessageDelivered(delivery=event)


def _dropped(core: CoreState, event: InputDropped) -> Event:
    del core
    return MessageDropped(drop=event)


def _interrupted(core: CoreState, event: InterruptCompleted | InterruptFailed) -> Event:
    del core
    return Interrupted(result=event)


_TRANSLATE: dict[type, Callable[[CoreState, Any], Event | Discarded | None]] = {
    Failed: _failed,
    Rejected: _rejected,
    AttemptExhausted: _exhausted,
    TurnResult: _turn,
    MeasurementResult: _measurement,
    OperationResult: _operation,
    AdoptionResult: _adoption,
    AdoptionFailed: _adoption,
    AttemptSettled: _settled,
    AttemptReady: _ready,
    ControlChanged: _control,
    InputDelivered: _delivered,
    InputDropped: _dropped,
    InterruptCompleted: _interrupted,
    InterruptFailed: _interrupted,
}
"""Per kernel event type, the search event it becomes. Types absent here stay in core."""
