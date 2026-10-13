"""One workstream: a slot's worth of work, from parent verification to settlement.

The story of `workstream` is the story of the slot: prove the parent, open an attempt, let an
agent do the work, close the attempt with what was established. The attempt is a resource, so
it always settles: after the work, after a failure that ends it (a `WorkEndedError` with a typed
failure), and after an operator stop, which interrupts the work wherever it awaits. Opening and
settling are never interrupted.
"""

from collections.abc import AsyncGenerator

from pydantic import BaseModel

from vibesys.candidates.api import assessment, until_deadline
from vibesys.candidates.api import settle_for as candidate_settle_for
from vibesys.metrics import reading_of
from vibesys.orchestration.dynamic.flow._outcomes import (
    STOPPED,
    Attempt,
    Closing,
    NotOpenedError,
    WorkEndedError,
    WorkstreamDone,
    WorkstreamStart,
    failure_of,
    not_opened_by,
)
from vibesys.orchestration.dynamic.flow._work import do_work
from vibesys.orchestration.dynamic.search.api import (
    Failure,
    FailureKind,
    ParentVerification,
    VerifyParentRevision,
    WorkKind,
)
from vs_core.api.search import (
    THIS_RUN,
    AssessmentKind,
    AttemptBudget,
    DecisionId,
    EvidenceKind,
    Failed,
    Flow,
    Operation,
    OperationFinished,
    Settle,
    Settlement,
    StartAttempt,
    Stopped,
    UnexpectedEvent,
    Withdraw,
    Within,
    WorkspaceClosed,
    WorkspaceMode,
    WorkspaceOpened,
    WorkspacePlan,
    item_id,
    resource,
)


async def workstream(ctx: Flow, w: WorkstreamStart) -> WorkstreamDone:
    """Run one scheduled workstream to the settlement of its attempt."""
    try:
        await verify_parent(ctx, w)
        async with attempt(ctx, w) as a:
            a.closing = await do_work(ctx, w, a)
    except NotOpenedError as refused:
        return WorkstreamDone.not_opened(w, refused)
    return WorkstreamDone.settled(w, a)


@resource
async def attempt(ctx: Flow, w: WorkstreamStart) -> AsyncGenerator[Attempt]:
    """An open attempt that is always settled, however the work inside it ends."""
    opened = await open_attempt(ctx, w)
    try:
        yield opened
    except Stopped:
        opened.ended(WorkEndedError(STOPPED))
    except WorkEndedError as ended:
        opened.ended(ended)
    await settle(ctx, w, opened)


async def verify_parent(ctx: Flow, w: WorkstreamStart) -> None:
    """A parent other than the input must still reproduce its retained content."""
    if w.parent == ctx.facts.baseline:
        return
    request = Operation(
        decision_id=DecisionId(root="verify"),
        scope=THIS_RUN,
        request=VerifyParentRevision(parent=w.parent),
    )
    match await ctx.wait(request, Within(limit=w.config.operation_seconds)):
        case OperationFinished(result=result) if _reproduces(result.outcome):
            return
        case OperationFinished():
            unverified = Failure(kind=FailureKind.PARENT_UNVERIFIED, revision=w.parent)
            raise NotOpenedError(unverified, withheld=w.parent)
        case Failed() as failed:
            raise not_opened_by(failed)
        case unexpected:
            raise UnexpectedEvent(w, unexpected)


def _reproduces(outcome: BaseModel | None) -> bool:
    return (
        isinstance(outcome, ParentVerification)
        and outcome.status == "succeeded"
        and outcome.verified
    )


async def open_attempt(ctx: Flow, w: WorkstreamStart) -> Attempt:
    """Ask core for the attempt's workspace and wait for it."""
    isolated = w.plan.kind is WorkKind.IMPLEMENT
    request = StartAttempt(
        decision_id=DecisionId(root="start"),
        scope=THIS_RUN,
        attempt_id=w.attempt,
        item_id=item_id(w.plan.work_id, w.sequence),
        workspace=WorkspacePlan(
            mode=WorkspaceMode.ISOLATED_CHILD if isolated else WorkspaceMode.READ_ONLY_REVISION,
            base=w.parent,
        ),
        budget=AttemptBudget(
            admission_charge=1,
            paid_invocation_limit=w.config.max_retries_per_round,
            retry_limit=w.config.max_corrections + w.config.max_turn_drops,
        ),
    )
    match await ctx.wait(request, ctx.read(until_deadline, Within)):
        case WorkspaceOpened(attempt=opened):
            return Attempt(ref=opened)
        case Failed() as failed:
            raise not_opened_by(failed)
        case unexpected:
            raise UnexpectedEvent(w, unexpected)


async def settle(ctx: Flow, w: WorkstreamStart, attempt: Attempt) -> None:
    """Close the attempt, asserting what the workstream established.

    If core will not take that assertion, the plain close (discard, failed) is proposed once:
    a close nobody takes would hold the slot for ever.
    """
    settled = await withdraw(ctx, attempt, _assertion(w, attempt.closing))
    if isinstance(settled, Failed):
        attempt.closing = attempt.closing.refused_by(failure_of(settled, None))
        settled = await withdraw(ctx, attempt, _PLAIN)
    attempt.settlement = None if isinstance(settled, Failed) else settled


_PLAIN = Settle(
    assessments=(), eligible=False, retention="discard", outcome="failed", candidate=None
)


def _assertion(w: WorkstreamStart, closing: Closing) -> Settle:
    """The assessments, eligibility and retention to propose for a finished attempt.

    A profile workstream measures a revision without proposing one, so it settles as ineligible
    and discarded.
    """
    if w.plan.kind is WorkKind.PROFILE:
        profile = reading_of(closing.readings, EvidenceKind.PROFILING)
        return Settle(
            assessments=assessment(AssessmentKind.PROFILING, profile, None),
            eligible=False,
            retention="discard",
            outcome="failed" if closing.failed else "succeeded",
            candidate=None,
        )
    return candidate_settle_for(closing, w.baseline, w.config)


async def withdraw(ctx: Flow, attempt: Attempt, disposition: Settle) -> Settlement | Failed:
    """Close the attempt's workspace: core's settlement, or the failure that refused it."""
    request = Withdraw(
        decision_id=DecisionId(root="settle"),
        scope=THIS_RUN,
        target=attempt.ref,
        disposition=disposition,
    )
    match await ctx.wait(request, ctx.read(until_deadline, Within)):
        case WorkspaceClosed(settlement=settlement):
            return settlement
        case Failed() as refused:
            return refused
        case unexpected:
            raise UnexpectedEvent(attempt, unexpected)
