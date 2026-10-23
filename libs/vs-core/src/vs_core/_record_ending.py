"""Ending an open record core owes a result for, and everything its ending sends and tells.

A record ends when its limit passes, when a watch fires, or (at recovery) when its executor is
a host instance that is gone. Ending moves it to BLOCKED, tells the decision that asked for it
once, cancels the lost turn of a dispatch so its session answers its invocation, and terminates
the jobs a submission started. Recovery decides *when* a record ends (`_request_recovery`); this
module is *what* ending one does.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._failure_routing import FailureHearer, failure_hearer
from ._measurements import cancel_settled
from ._request_forward import turn_of
from ._session_scope import lost_cancellation_ask, lost_turn_authority
from .types.common import ExecuteRegisteredOperation, LifecycleClass, RequestId
from .types.evaluation import (
    CancelOwnedJob,
    JobTerminationRequested,
    MeasurementSubmissionEnded,
    SubmitMeasurement,
)
from .types.records import BlockRequest, CancelOwnedResource, InspectRequest, RequestPhase
from .types.sessions import CancelTurn, InvocationCancellationRequested
from .types.until import Failed, FailureReason, WhileAlive, WhileOpen

if TYPE_CHECKING:
    from .types.kernel import RecordsContext, Signal
    from .types.records import RecordsState, RequestRecord


def cancelled_here(record: RequestRecord) -> bool:
    """Whether the record's own resource is cancelled here, by ``CancelOwnedResource``.

    That command reaches the operations executor, which holds receipts for registered
    operations only: it can end an operation's resource and nothing else. Every other
    resource is released by the area that owns it, through the command whose outcome that
    area already defines, so sending a generic cancel for it only adds a command that is
    answered "unknown". Evaluation terminates an owned job (``CancelOwnedJob``) at the job's
    own deadline or the run's close, within its retry limit. A turn's session is cancelled
    through ``lost_turn`` (``CancelTurn``). Sessions closes a session's lease when the block
    wakes the session's wait (``CloseSession``), and an attempt's retirement discards its
    workspace (``DiscardWorkspace``). A request that only names a resource another request
    owns has nothing of its own to release.
    """
    return isinstance(record.request, ExecuteRegisteredOperation) and record.lifecycle not in (
        LifecycleClass.OWNED_JOB,
        LifecycleClass.SESSION_TURN,
    )


def end_open_record(
    state: RecordsState, record: RequestRecord, failure: Failed, *, lost: bool, first: bool
) -> tuple[RecordsState, tuple[Failed, ...]]:
    """Block a record whose result is still owed, and the failure its owner hears of it.

    One that already concluded (it is here for its unreleased children) has told its owner
    already. A lost turn is answered by its session, once it is cancelled: the ask hears that,
    not a failure of the record (which would discard that answer as late).
    """
    if not is_open(record):
        return state, ()
    failed = None if lost else record.failure or failure
    heard = (
        (failed,)
        if failed is not None
        and first
        and _heard_by_decision(record)
        and not _decision_ended(state, record)
        else ()
    )
    blocked = record.model_copy(update={"phase": RequestPhase.BLOCKED, "failure": failed})
    return replace_record(state, blocked), heard


def lost_turn(context: RecordsContext, record: RequestRecord, *, first: bool) -> tuple[Signal, ...]:
    """The cancellation that ends the turn of a request core just ended, else nothing.

    A dispatch that ends at its limit (or a watch) with no result leaves its session executing
    with nothing to answer it. The turn is lost like one whose acceptance stayed unknown:
    Sessions cancels it and answers its invocation as lost, and the search's role decides
    whether to ask again.

    A cancellation of a lost turn that ended unanswered at its bound proves nothing: the turn may
    still run, and the session waits on it for ever, so the ask it serves (a planner call, an
    implementer's turn) has no next step. Cancelling is idempotent, so core asks again under the
    next authority, up to ``Limits.request_retries.limit`` more asks (as for a job's cancel,
    ``cancel_settled``), each one given the bound to answer. Past it the cancellation stays
    unanswered and the session keeps its wait, which halts the run loudly (``OrphanWaitError``).
    """
    request = record.request
    if not first or not is_open(record):
        return ()
    if isinstance(request, CancelTurn):
        limit = context.run.limits.request_retries.limit
        ask = lost_cancellation_ask(
            request.invocation, cancellation=request.request_id, limit=limit
        )
        if ask is None or ask >= limit:
            return ()
        invocation, authority = request.invocation, lost_turn_authority(request.invocation, ask + 1)
    else:
        invocation = None if record.phase == RequestPhase.PREPARED else turn_of(context, record)
        if invocation is None:
            return ()
        authority = lost_turn_authority(invocation)
    return (InvocationCancellationRequested(invocation=invocation, authority=authority),)


def orphaned_jobs(
    state: RecordsState, context: RecordsContext, record: RequestRecord, reason: FailureReason
) -> tuple[JobTerminationRequested, ...]:
    """The termination of every unfinished job a submission started, when core ends it.

    A strategy's ``Measure`` waits on its submission's record. Once that record ends at its
    limit or a watch, nothing waits on the job any longer: evaluation cancels it rather than
    leave it in the scheduler's queue, holding nodes nobody will read. A cancel of such a job
    that ends blocked at its bound proved nothing, so core asks again, within the backend's
    retry limit (``cancel_settled``); each such cancel's own end brings it back here. A job
    whose id arrives only after its submission ended is cancelled when that late answer ends
    the submission again. A job an agent submitted is its wait's to end (``_continuations``).
    """
    source = _orphan_source(state, context, record)
    if source is None:
        return ()
    cause = (
        "deadline"
        if reason in (FailureReason.TIMED_OUT, FailureReason.QUEUE_BUDGET_SPENT)
        else "retirement"
    )
    return tuple(
        JobTerminationRequested(resource_id=job.resource_id, cause=cause)
        for job in context.evaluation.jobs
        if job.submission_id == source
        and job.resource_id is not None
        and not job.terminal
        and not cancel_settled(state, context.run, job.resource_id)
    )


def submission_ended(
    record: RequestRecord, reason: FailureReason
) -> tuple[MeasurementSubmissionEnded, ...]:
    """The end of a measurement submission core blocks while its result is still owed.

    Every measurement submission, whoever asked for it: its budget entry is the evaluation
    area's, and a run the comparisons of its input wait on must end there even when no
    observation ever reaches it (its submission hung, or a restarted host could not find it).
    """
    if not isinstance(record.request, SubmitMeasurement) or not is_open(record):
        return ()
    return (MeasurementSubmissionEnded(request_id=record.request_id, reason=reason),)


def _heard_by_decision(record: RequestRecord) -> bool:
    """Whether the record's decision hears its failure (``_failure_routing``)."""
    return failure_hearer(record.request) == FailureHearer.DECISION


def _orphan_source(
    state: RecordsState, context: RecordsContext, record: RequestRecord
) -> RequestId | None:
    """The strategy's submission whose jobs core now ends, as *record* ends; else ``None``.

    That is *record* itself when it is the submission, and the job's submission when
    *record* is a cancel of a job whose submission core ended before.
    """
    request = record.request
    if isinstance(request, SubmitMeasurement):
        source: RequestId | None = record.request_id
    elif isinstance(request, CancelOwnedJob):
        source = next(
            (
                job.submission_id
                for job in context.evaluation.jobs
                if job.resource_id == request.resource_id
            ),
            None,
        )
        ended = state.record(source) if source is not None else None
        if ended is None or ended.failure is None:
            return None
    else:
        return None
    if any(call.request_id == source for call in context.evaluation.agent_calls) or any(
        run.request_id == source for run in context.evaluation.core_runs
    ):
        return None
    return source


def _decision_ended(state: RecordsState, record: RequestRecord) -> bool:
    """Whether another request of the record's decision already ended with a failure.

    A decision hears of its ending once; its later requests end quietly, as the owner has
    moved on.
    """
    decision = record.request.decision_id
    return decision is not None and any(
        row.failure is not None
        and _heard_by_decision(row)
        and row.request.decision_id == decision
        and row.request_id != record.request_id
        for row in state.records
    )


def failure_of(record: RequestRecord, reason: FailureReason, limit_at: float) -> Failed:
    """The typed result a record ends with: what ended it, and which watch or limit.

    ``limit_at`` is the record's bound (``_wait_clock.record_bound``): its stated limit plus
    its job's queue credit, or when its job's queue budget ran out.
    """
    watch = next(
        (
            watch
            for watch in record.until.watches
            if (isinstance(watch, WhileOpen) and reason == FailureReason.WORKSPACE_CLOSED)
            or (isinstance(watch, WhileAlive) and reason == FailureReason.OWNER_GONE)
        ),
        None,
    )
    return Failed(
        reason=reason,
        decision=record.request.decision_id,
        watch=watch,
        limit_at=limit_at
        if reason in (FailureReason.TIMED_OUT, FailureReason.QUEUE_BUDGET_SPENT)
        else None,
    )


def replace_record(state: RecordsState, record: RequestRecord) -> RecordsState:
    return state.model_copy(
        update={
            "records": tuple(
                record if row.request_id == record.request_id else row for row in state.records
            )
        }
    )


def _canonical(record: RequestRecord) -> bool:
    return not isinstance(record.request, InspectRequest | CancelOwnedResource | BlockRequest)


def is_open(record: RequestRecord) -> bool:
    """A canonical record that has not ended: core owes it a result by its ``until``."""
    return _canonical(record) and record.phase in (
        RequestPhase.PREPARED,
        RequestPhase.DISPATCHED,
        RequestPhase.RECONCILING,
    )


def command_open(record: RequestRecord) -> bool:
    """An inspection, cancel or block that has not ended: its limit ends it if nothing else."""
    return not _canonical(record) and record.phase in (
        RequestPhase.PREPARED,
        RequestPhase.DISPATCHED,
        RequestPhase.RECONCILING,
    )
