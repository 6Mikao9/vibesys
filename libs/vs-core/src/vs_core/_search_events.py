"""Translate what core published into the events a search receives, and project its view.

Core publishes lifecycle facts (``StrategyEvent``). A search receives past-tense events
that carry the decision they answer. Plumbing a search never acts on (a turn that only
suspends, a resume authorization, acceptance of a decision) is handled by core and does
not reach it. Pure: it reads the state after the step and returns values.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ._registry import ContractError
from .types.attempts import AttemptExhausted
from .types.evaluation import MeasurementResult
from .types.kernel import ControlChanged
from .types.records import OperationResult
from .types.scheduling import AttemptReady
from .types.search import (
    AdoptionFinished,
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
        attempts=core.attempts.attempts,
        measurements=core.evaluation.evidence,
        settlements=core.settlement.settlements,
    )


def _decision_of(core: CoreState, request_id: RequestId) -> DecisionId | None:
    """The decision whose request produced ``request_id``; None for core's own requests."""
    for record in core.records.records:
        if record.request_id == request_id:
            return record.request.decision_id
    raise ContractError(("request_id", request_id.root), "result for an unknown request")


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


def search_events(core: CoreState, events: tuple[StrategyEvent, ...]) -> tuple[Event, ...]:
    """The events ``core`` (the state after the step) owes the search for ``events``."""
    found = (_TRANSLATE.get(type(event), _ignored)(core, event) for event in events)
    return tuple(event for event in found if event is not None)


def _ignored(core: CoreState, event: StrategyEvent) -> None:
    """Acceptance, suspension plumbing and run end stay inside core."""
    del core, event


def _failed(core: CoreState, event: Failed) -> Event:
    del core
    return event


def _rejected(core: CoreState, event: Rejected) -> Event:
    del core
    return Failed(reason=FailureReason.REFUSED, decision=event.decision_id, cause=event.code)


def _turn(core: CoreState, event: TurnResult) -> Event | None:
    decision = _decision_of(core, event.observation.request_id)
    if event.suspending or decision is None:
        return None
    return TurnFinished(decision=decision, result=event)


def _measurement(core: CoreState, event: MeasurementResult) -> Event:
    source = event.source_request
    decision = None if source is None else _decision_of(core, source)
    return MeasurementFinished(decision=decision, result=event)


def _operation(core: CoreState, event: OperationResult) -> Event | None:
    decision = _decision_of(core, event.observation.request_id)
    return None if decision is None else OperationFinished(decision=decision, result=event)


def _adoption(core: CoreState, event: AdoptionResult | AdoptionFailed) -> Event:
    decision = _decision_of(core, event.observation.request_id)
    return AdoptionFinished(decision=decision, result=event)


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


_TRANSLATE: dict[type, Callable[[CoreState, Any], Event | None]] = {
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
