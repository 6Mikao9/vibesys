"""The issue-queue search as a story: file the task, work the board round by round, adopt the tip.

This is the whole search, top to bottom. The board is the source of truth: the root reads it
before every decision, and `Queue` (a pure value) says what the board calls for next: work an
issue, evaluate performance, or end. Each job is a task in a group of one, so it is restartable
from its start, and the operator's stop reaches it (it settles what it started) while the root
only sees the stop, starts nothing more and returns. The closed issues build on one another, so
the winner is the last of them: after the accuracy proof its adoption needs, it is named
(`ctx.choose`) and the framework adopts it.
"""

from typing import TYPE_CHECKING

from vibesys.orchestration.issue_queue.search._board import bootstrap_title, briefs
from vibesys.orchestration.issue_queue.search._config import IssueQueueConfig
from vibesys.orchestration.issue_queue.search._issue import issue_job
from vibesys.orchestration.issue_queue.search._operations import BOARD, OPERATIONS
from vibesys.orchestration.issue_queue.search._perf import perf_job
from vibesys.orchestration.issue_queue.search._proof import prove
from vibesys.orchestration.issue_queue.search._queue import (
    Assess,
    Finish,
    Queue,
    QueueSnapshot,
    Work,
)
from vibesys.orchestration.issue_queue.search._roles import ROLES
from vibesys.orchestration.issue_queue.search._tracker import apply, read_board
from vibesys.orchestration.issue_queue.search._values import Ending, Failure, IssueJob, PerfJob
from vibesys.orchestration.issue_queue.search._writes import bootstrap
from vs_core.api.search import INPUT, Flow, NoWinner, search

if TYPE_CHECKING:
    from vs_issue_tracker.api import IssueRow

BLANK_OBJECTIVE = (
    "the run's objective is blank, so there is no first issue to file: give the task an objective"
)


@search("plain", config=IssueQueueConfig, operations=OPERATIONS, roles=ROLES, owned=BOARD)
async def issue_queue(ctx: Flow, config: IssueQueueConfig) -> NoWinner | None:
    """File the task as the first issue, work the board in rounds, and name the last closed issue."""
    ctx.checkpoint(config)
    title = bootstrap_title(ctx.task.objective)
    if not title:
        return NoWinner(reason=BLANK_OBJECTIVE)
    queue = Queue()
    filed = await apply(
        ctx,
        config,
        bootstrap(ctx.task.run_id, title, queue.iteration),
        "filing the bootstrap issue",
    )
    if isinstance(filed, Failure):
        queue = queue.ended(
            Ending.failure(f"the bootstrap issue could not be filed: {filed.describe()}")
        )
    issues: tuple[IssueRow, ...] = ()
    async with ctx.group(limit=1) as jobs:
        while queue.active:
            if ctx.stop_requested:
                queue = queue.stopped()
                break
            read = await read_board(ctx, config)
            if isinstance(read, Failure):
                queue = queue.ended(
                    Ending.failure(f"the issue tracker is unavailable: {read.describe()}")
                )
                break
            issues = read
            step = queue.decide(issues, config)
            ctx.publish(QueueSnapshot.of(queue, issues, step))
            base = queue.base(ctx.facts.baseline)
            common = {
                "sequence": queue.next_sequence,
                "iteration": queue.iteration,
                "run_id": ctx.task.run_id,
                "base": base,
                "board": briefs(issues),
                "config": config,
            }
            match step:
                case Finish(ending=ending):
                    queue = queue.ended(ending)
                    continue
                case Work(issue=issue):
                    start = IssueJob(**common, issue=issue)
                    jobs.start(start.key, issue_job, start)
                case Assess():
                    evaluation = PerfJob(**common, prior=queue.performance)
                    jobs.start(evaluation.key, perf_job, evaluation)
            queue = queue.started().folded(await jobs)
    ctx.publish(QueueSnapshot.of(queue, issues, None))
    return await conclude(ctx, queue)


async def conclude(ctx: Flow, queue: Queue) -> NoWinner | None:
    """Name the winner and say whether the run found one.

    The winner is the last closed issue's candidate, unless the operator stopped the run (which
    adopts nothing, as it always has) or no issue was closed with a change (the input stands).
    A run that built something and then stopped short of its goal still adopts what it built.
    """
    ending = queue.ending
    tip = None if queue.stopping else queue.tip
    if tip is None:
        ctx.choose(INPUT)
        if not queue.stopping and ending is not None and ending.outcome == "failure":
            return NoWinner(reason=ending.reason)
        return None
    await prove(ctx, tip)
    ctx.choose(tip)
    return None
