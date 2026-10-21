"""The flows' conversation with the issue board: read it, change it, log to it.

The board is owned by the tracker library; a flow asks its owner through `ctx.owners`. A write is
idempotent per key, so one whose effect stayed unknown is sent again up to the config's bound
and a write that did land is not repeated. A request that cannot be performed is a typed
`Failure`, never an exception the caller must guess at; the progress log is a mirror for people,
so a log entry that cannot be written does not stop the work.
"""

from pydantic import BaseModel

from vibesys.orchestration.issue_queue.search._config import IssueQueueConfig
from vibesys.orchestration.issue_queue.search._values import Failure, FailureKind, JobEndedError
from vibesys.orchestration.issue_queue.search._writes import Writes
from vs_core.api.search import (
    Again,
    CoreValue,
    Failed,
    Flow,
    GaveUp,
    OperationRequest,
    RetryPolicy,
    Tries,
)
from vs_issue_tracker.api import (
    AppliedChanges,
    ApplyIssueChanges,
    BoardSnapshot,
    IssueRow,
    ReadBoard,
)

READING = "reading the board"


def _unavailable(doing: str, detail: str) -> Failure:
    return Failure(kind=FailureKind.BOARD, reason=doing, detail=detail)


async def perform(
    ctx: Flow, config: IssueQueueConfig, request: OperationRequest, doing: str
) -> BaseModel | CoreValue | Failure:
    """The owner's outcome for ``request``, sent again while its effect stays unknown."""
    seconds = ctx.task.terms.operation_seconds

    async def attempt(_tries: Tries[str]) -> BaseModel | CoreValue | Again[str]:
        outcome = await ctx.owners.perform(request, within=seconds)
        return Again(reason=outcome.reason.value) if isinstance(outcome, Failed) else outcome

    match await ctx.retry(attempt, RetryPolicy(max_tries=config.max_board_retries + 1)):
        case GaveUp(reasons=reasons):
            return _unavailable(doing, f"no answer after {len(reasons)} tries ({reasons[-1]})")
        case outcome:
            return outcome


async def read_board(ctx: Flow, config: IssueQueueConfig) -> tuple[IssueRow, ...] | Failure:
    """Every issue on the board, as it is now."""
    outcome = await perform(ctx, config, ReadBoard(), READING)
    if isinstance(outcome, Failure):
        return outcome
    if not isinstance(outcome, BoardSnapshot) or outcome.status != "succeeded":
        return _unavailable(READING, _status(outcome))
    return outcome.issues


async def apply(
    ctx: Flow, config: IssueQueueConfig, writes: Writes, doing: str
) -> AppliedChanges | Failure:
    """Log a step's entries, then apply its changes as one batch."""
    for entry in writes.logs:
        await perform(
            ctx, config, entry, "logging progress"
        )  # a mirror: its failure is not the work's
    if not writes.changes:
        return AppliedChanges(status="succeeded")
    outcome = await perform(ctx, config, ApplyIssueChanges(changes=writes.changes), doing)
    if isinstance(outcome, Failure):
        return outcome
    if not isinstance(outcome, AppliedChanges) or outcome.status != "succeeded":
        return _unavailable(doing, _status(outcome))
    return outcome


async def write(ctx: Flow, config: IssueQueueConfig, writes: Writes, doing: str) -> AppliedChanges:
    """Apply a job's writes; a job that cannot write to the board ends."""
    outcome = await apply(ctx, config, writes, doing)
    if isinstance(outcome, Failure):
        raise JobEndedError(outcome)
    return outcome


def _status(outcome: BaseModel | CoreValue) -> str:
    if isinstance(outcome, BoardSnapshot | AppliedChanges):
        return f"{outcome.status}: {outcome.detail}" if outcome.detail else outcome.status
    return f"unexpected outcome {type(outcome).__name__}"
