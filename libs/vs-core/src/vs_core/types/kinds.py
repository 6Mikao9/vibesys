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

    ASK_AGENT = "ask_agent"
    """Ask an agent for a reply; core runs render, turn, validation, corrections and resumes."""
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
    TURN_ENDED = "turn_ended"
    """Plumbing of the agent pipeline: one provider turn ended (reply or typed failure)."""
    TURN_YIELDED = "turn_yielded"
    """Plumbing of the agent pipeline: core recorded the wait a turn ended with."""
    YIELD_FAILED = "yield_failed"
    """Plumbing of the agent pipeline: core could not publish the wait a turn ended with."""
    RESUME_READY = "resume_ready"
    """Plumbing of the agent pipeline: core authorized the resume of a suspended turn."""
    CHECKPOINT_KNOWN = "checkpoint_known"
    """Plumbing of the agent pipeline: core recorded or declined a turn's checkpoint."""


ALWAYS: frozenset[EventKind] = frozenset({EventKind.FAILED, EventKind.STOP_REQUESTED})
"""Delivered to every search: any wait can fail, and an operator can ask to stop."""

PIPELINE: frozenset[EventKind] = frozenset(
    {
        EventKind.TURN_ENDED,
        EventKind.TURN_YIELDED,
        EventKind.YIELD_FAILED,
        EventKind.RESUME_READY,
        EventKind.CHECKPOINT_KNOWN,
    }
)
"""The events of one provider turn. Only core's agent pipeline declares and handles them."""

OPT_IN: frozenset[EventKind] = (
    frozenset({EventKind.MESSAGE_DELIVERED, EventKind.MESSAGE_DROPPED, EventKind.INTERRUPTED})
    | PIPELINE
)
"""Delivered only to a search that declares it; core handles them itself otherwise."""

PRODUCES: dict[WorkKind, frozenset[EventKind]] = {
    WorkKind.ASK_AGENT: frozenset({EventKind.TURN_FINISHED}),
    WorkKind.REQUEST_TURN: PIPELINE,
    WorkKind.START_ATTEMPT: frozenset({EventKind.WORKSPACE_OPENED}),
    WorkKind.WITHDRAW: frozenset({EventKind.WORKSPACE_CLOSED}),
    WorkKind.MEASURE: frozenset({EventKind.MEASUREMENT_FINISHED}),
    WorkKind.PROPOSE_WINNER: frozenset({EventKind.ADOPTION_FINISHED}),
    WorkKind.OPERATION: frozenset({EventKind.OPERATION_FINISHED}),
    WorkKind.WAKE_AT: frozenset({EventKind.TIME_REACHED}),
}
"""The result event each kind of work ends with (``FAILED`` is always possible as well)."""
