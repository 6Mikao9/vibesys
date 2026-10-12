"""The closed sets a search declares: the work it uses and the event kinds it handles.

A search lists the kinds of work it starts (``uses``) and the kinds of event it handles
(``handles``). Core knows which events each kind of work produces, so a search that starts
work and does not handle its result, or declares an event nothing it uses can produce, is
refused at startup instead of failing in the middle of a run.
"""

from __future__ import annotations

from enum import StrEnum


class WorkKind(StrEnum):
    """What a ``Wait`` can ask for, plus the clock wake-up."""

    REQUEST_TURN = "request_turn"
    START_ATTEMPT = "start_attempt"
    WITHDRAW = "withdraw"
    MEASURE = "measure"
    PROPOSE_WINNER = "propose_winner"
    OPERATION = "operation"
    WAKE_AT = "wake_at"


class EventKind(StrEnum):
    """The kinds of event a search receives, past tense, one per thing that happened."""

    WORKSPACE_OPENED = "workspace_opened"
    WORKSPACE_CLOSED = "workspace_closed"
    TURN_FINISHED = "turn_finished"
    MEASUREMENT_FINISHED = "measurement_finished"
    OPERATION_FINISHED = "operation_finished"
    ADOPTION_FINISHED = "adoption_finished"
    STOP_REQUESTED = "stop_requested"
    TIME_REACHED = "time_reached"
    FAILED = "failed"
    MESSAGE_DELIVERED = "message_delivered"
    MESSAGE_DROPPED = "message_dropped"
    INTERRUPTED = "interrupted"


ALWAYS: frozenset[EventKind] = frozenset({EventKind.FAILED, EventKind.STOP_REQUESTED})
"""Delivered to every search: any wait can fail, and an operator can ask to stop."""

OPT_IN: frozenset[EventKind] = frozenset(
    {EventKind.MESSAGE_DELIVERED, EventKind.MESSAGE_DROPPED, EventKind.INTERRUPTED}
)
"""Delivered only to a search that declares it; core handles them itself otherwise."""

PRODUCES: dict[WorkKind, frozenset[EventKind]] = {
    WorkKind.REQUEST_TURN: frozenset({EventKind.TURN_FINISHED}),
    WorkKind.START_ATTEMPT: frozenset({EventKind.WORKSPACE_OPENED}),
    WorkKind.WITHDRAW: frozenset({EventKind.WORKSPACE_CLOSED}),
    WorkKind.MEASURE: frozenset({EventKind.MEASUREMENT_FINISHED}),
    WorkKind.PROPOSE_WINNER: frozenset({EventKind.ADOPTION_FINISHED}),
    WorkKind.OPERATION: frozenset({EventKind.OPERATION_FINISHED}),
    WorkKind.WAKE_AT: frozenset({EventKind.TIME_REACHED}),
}
"""The result event each kind of work ends with (``FAILED`` is always possible as well)."""
