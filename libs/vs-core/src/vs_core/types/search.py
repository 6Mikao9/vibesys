"""The contract between a search and core: events in, decisions out, one narrow view.

A search is a pure state machine. ``init`` returns its first state and decisions;
``update`` takes one event at a time and returns the next state and decisions. It reads
facts other owners keep (the clock, the baseline, measurements, settlements) through
``RunView`` and never copies them into its own state. Work starts only inside a ``Wait``,
so every wait states how long it may take (see ``until``).

Events are past tense and typed. A result carries the decision it answers, so a search
(or ``Children``) can tell which of its waits ended. A wait that ends without a result
arrives as ``Failed`` with a closed reason; nothing here is free text.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Literal, Protocol

from pydantic import Field

from .attempts import AttemptView
from .common import (
    AttemptRef,
    Capabilities,
    ControlInput,
    DecisionId,
    Limits,
    RunFacts,
    Seconds,
    Value,
)
from .evaluation import EvidenceRef, MeasurementResult
from .records import OperationResult
from .session_inputs import InputDelivered, InputDropped
from .sessions import InterruptCompleted, InterruptFailed, TurnResult
from .settlement import AdoptionFailed, AdoptionResult, Settlement
from .until import Failed

if TYPE_CHECKING:
    from .strategy import Decision, SearchDeclaration, StrategyState


class WorkspaceOpened(Value):
    """An attempt's workspace is ready to use."""

    kind: Literal["workspace_opened"] = "workspace_opened"
    decision: DecisionId
    attempt: AttemptRef


class WorkspaceClosed(Value):
    """An attempt's workspace is closed and settled."""

    kind: Literal["workspace_closed"] = "workspace_closed"
    decision: DecisionId | None
    settlement: Settlement


class TurnFinished(Value):
    """A turn ended with a reply or a typed turn failure."""

    kind: Literal["turn_finished"] = "turn_finished"
    decision: DecisionId
    result: TurnResult


class MeasurementFinished(Value):
    """A measurement ended with evidence, or a typed failure and partial evidence."""

    kind: Literal["measurement_finished"] = "measurement_finished"
    decision: DecisionId | None
    result: MeasurementResult


class OperationFinished(Value):
    """A registered operation ended with its outcome."""

    kind: Literal["operation_finished"] = "operation_finished"
    decision: DecisionId
    result: OperationResult


class AdoptionFinished(Value):
    """Adoption of a proposed winner ended with a verified revision or a typed failure."""

    kind: Literal["adoption_finished"] = "adoption_finished"
    decision: DecisionId | None
    result: AdoptionResult | AdoptionFailed


class StopRequested(Value):
    """An operator asked the run to stop, steer, pause or resume."""

    kind: Literal["stop_requested"] = "stop_requested"
    control: ControlInput


class TimeReached(Value):
    """The run clock reached a time the search asked to be woken at."""

    kind: Literal["time_reached"] = "time_reached"
    time: Seconds


class MessageDelivered(Value):
    """A queued message reached the session it was addressed to (opt-in)."""

    kind: Literal["message_delivered"] = "message_delivered"
    delivery: InputDelivered


class MessageDropped(Value):
    """A queued message can no longer reach its session (opt-in)."""

    kind: Literal["message_dropped"] = "message_dropped"
    drop: InputDropped


class Interrupted(Value):
    """An interruption ended with a checkpoint, or failed to produce one (opt-in)."""

    kind: Literal["interrupted"] = "interrupted"
    result: InterruptCompleted | InterruptFailed


type Event = Annotated[
    WorkspaceOpened
    | WorkspaceClosed
    | TurnFinished
    | MeasurementFinished
    | OperationFinished
    | AdoptionFinished
    | StopRequested
    | TimeReached
    | MessageDelivered
    | MessageDropped
    | Interrupted
    | Failed,
    Field(discriminator="kind"),
]
"""Everything ``Search.update`` can receive. A search declares which kinds it handles."""


class RunView(Value):
    """What a search may read about the run, and nothing it could write.

    The facts here belong to other owners. A search reads them when it needs them and
    keeps none of them in its own state.
    """

    now_at: Seconds
    deadline_at: Seconds
    facts: RunFacts
    capabilities: Capabilities
    limits: Limits
    attempts: tuple[AttemptView, ...]
    measurements: tuple[EvidenceRef, ...]
    settlements: tuple[Settlement, ...]


class Search[S: StrategyState, C](Protocol):
    """A search: a pure state machine from events to decisions.

    ``update`` is one exhaustive ``match`` over the search's phase and the event. An
    unexpected pair raises. The search's state carries a ``schema_version``.
    """

    @property
    def declaration(self) -> SearchDeclaration:
        """The agents, operations, capabilities and event kinds the search declares."""
        ...

    @property
    def state_model(self) -> type[S]:
        """The class of the search's state, so the shell can decode a saved run."""
        ...

    def init(self, config: C, facts: RunFacts) -> tuple[S, list[Decision]]:
        """The first state and decisions of a new run."""
        ...

    def update(self, state: S, event: Event, view: RunView) -> tuple[S, list[Decision]]:
        """The next state and decisions after one event."""
        ...
