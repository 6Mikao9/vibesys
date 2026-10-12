"""One job: working one issue, or one performance evaluation, from its attempt to its settlement.

A job is a machine of its own. Its phase says what it waits for and holds only the data valid
there; `update` is one `match` over the phase and the event. An issue goes claim, implement,
record, review, file the verdict (and, when its attempts are spent, block), then settles; an
evaluation goes evaluate, file, settle. A job that fails after its workspace opened still
settles it; one that fails before it never held a slot, so it is simply over.

When a job is over its phase is `Done`; the queue retires it. Retirement is a rule of that
phase, not a flag the job sets: the queue reads what `Done` holds and nothing else.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import Field

from vibesys.candidates.api import until_deadline
from vibesys.orchestration.issue_queue.models import (
    GenericIssuePerfEvalResult,
    IssueImplementerResult,
    IssueJudgeResult,
    IssuePerfEvalResult,
    NewBug,
    NewIssue,
)
from vibesys.orchestration.issue_queue.search import _board as board
from vibesys.orchestration.issue_queue.search import _evidence as evidence
from vibesys.orchestration.issue_queue.search._config import IssueQueueConfig
from vibesys.orchestration.issue_queue.search._ids import decision, is_log, job_key
from vibesys.orchestration.issue_queue.search._operations import InterpretEvidence
from vibesys.orchestration.issue_queue.search._prompts import (
    ImplementPrompt,
    PerformancePrompt,
    PriorPerformance,
    ReviewEvaluation,
    ReviewPrompt,
)
from vibesys.orchestration.issue_queue.search._values import (
    Evaluation,
    FailureKind,
    FiledIssue,
    Implemented,
    JobFailure,
    JobKind,
    Judged,
    PerformanceRecord,
    Role,
)
from vs_core.api.search import (
    THIS_RUN,
    AskAgent,
    AssessmentKind,
    AssessmentProposal,
    AttemptBudget,
    AttemptId,
    AttemptRef,
    Decision,
    DecisionId,
    Declined,
    Event,
    EvidenceRef,
    Failed,
    FailureReason,
    InvocationRef,
    Operation,
    OperationFinished,
    OperationRequest,
    RevisionRef,
    RunView,
    Scope,
    Settle,
    Settlement,
    StartAttempt,
    StopRequested,
    TurnFinished,
    UnexpectedEvent,
    Value,
    Wait,
    Withdraw,
    Within,
    WorkspaceClosed,
    WorkspaceMode,
    WorkspaceOpened,
    WorkspacePlan,
    attempt_id,
    item_id,
)
from vs_issue_tracker.api import (
    AppliedChanges,
    ApplyIssueChanges,
    CreateIssue,
    IssueChange,
    IssueRow,
    IssueStatus,
    IssueType,
    LogProgress,
    RecordAttempt,
    SetIssueStatus,
    TextBody,
)

_NOTE_LIMIT = 200
_FEEDBACK_LIMIT = 500
ENDED_AFTER_SEND = frozenset({FailureReason.TIMED_OUT, FailureReason.LOST})
"""The endings that leave a board write's effect unknown: it is sent again, as the same keys."""

type Moved = tuple[Job, list[Decision]]
"""A job after one event, and the decisions that move it on."""

type ProgressStep = Literal["implement", "review", "performance"]


# -- phases ------------------------------------------------------------------------------------


class Starting(Value):
    """The attempt was requested; ``stopped`` records a stop that arrived before it opened."""

    kind: Literal["starting"] = "starting"
    stopped: bool = False


class Claiming(Value):
    """The issue is being marked in progress; ``sent`` counts how often the write was sent."""

    kind: Literal["claiming"] = "claiming"
    sent: int = Field(default=1, ge=1)


class Implementing(Value):
    """An implementer turn is out; ``spent`` counts the paid turns of this issue so far."""

    kind: Literal["implementing"] = "implementing"
    spent: int = Field(ge=1)


class Recording(Value):
    """The attempt is being written to the board."""

    kind: Literal["recording"] = "recording"
    spent: int = Field(ge=1)
    work: Implemented
    candidate: RevisionRef | None
    sent: int = Field(default=1, ge=1)


class ReadingEvidence(Value):
    """The evidence owner is decoding the evaluations the implementer ran."""

    kind: Literal["reading_evidence"] = "reading_evidence"
    spent: int = Field(ge=1)
    work: Implemented
    candidate: RevisionRef | None
    refs: tuple[EvidenceRef, ...]


class Reviewing(Value):
    """A judge turn is out."""

    kind: Literal["reviewing"] = "reviewing"
    spent: int = Field(ge=1)
    work: Implemented
    candidate: RevisionRef | None


class Filing(Value):
    """The judge's verdict, and the bug it found, are being written to the board."""

    kind: Literal["filing"] = "filing"
    spent: int = Field(ge=1)
    candidate: RevisionRef | None
    verdict: Judged
    judge: InvocationRef
    sent: int = Field(default=1, ge=1)


class Blocking(Value):
    """An issue whose attempts are spent is being marked blocked."""

    kind: Literal["blocking"] = "blocking"
    sent: int = Field(default=1, ge=1)


class Evaluating(Value):
    """A performance evaluator turn is out."""

    kind: Literal["evaluating"] = "evaluating"


class Reporting(Value):
    """The issues the evaluation filed are being written to the board."""

    kind: Literal["reporting"] = "reporting"
    evaluation: Evaluation
    sent: int = Field(default=1, ge=1)


class Closing(Value):
    """What a job asserts when it settles its workspace.

    ``judge`` and ``candidate`` are set for a closed issue whose turn changed the tree: that
    candidate is retained, vouched for by the judge, and the next job builds on it.
    """

    failure: JobFailure | None = None
    issue_closed: bool = False
    candidate: RevisionRef | None = None
    judge: InvocationRef | None = None
    evaluation: Evaluation | None = None
    created: tuple[int, ...] = ()


class Settling(Value):
    """The close of the workspace is out; ``refused`` counts the closes core did not take."""

    kind: Literal["settling"] = "settling"
    closing: Closing
    refused: int = Field(default=0, ge=0)


class Done(Value):
    """The job is over; the queue retires it.

    ``settlement`` is absent when the workspace was never opened or core took no close.
    """

    kind: Literal["done"] = "done"
    failure: JobFailure | None
    settlement: Settlement | None = None
    issue_closed: bool = False
    record: PerformanceRecord | None = None


type JobPhase = Annotated[
    Starting
    | Claiming
    | Implementing
    | Recording
    | ReadingEvidence
    | Reviewing
    | Filing
    | Blocking
    | Evaluating
    | Reporting
    | Settling
    | Done,
    Field(discriminator="kind"),
]


class Job(Value):
    """The one open job: its kind, the issue it works, the revision it builds on and its phase."""

    kind: JobKind
    # One more than the jobs opened before it, so each job's identities are its own.
    sequence: int = Field(gt=0)
    iteration: int = Field(gt=0)
    issue: IssueRow | None = None
    # The revision the job builds on: the tip when it opened.
    base: RevisionRef
    attempt: AttemptId
    generation: int = Field(default=0, ge=0)
    phase: JobPhase

    @property
    def key(self) -> str:
        """The prefix of every decision of this job."""
        return job_key(self.kind.value, self.sequence)

    @property
    def ref(self) -> AttemptRef:
        """The exact attempt generation."""
        return AttemptRef(attempt_id=self.attempt, generation=self.generation)

    @property
    def scope(self) -> Scope:
        """The scope the attempt's own work runs in."""
        return Scope(owner=self.attempt, generation=self.generation)

    @property
    def issue_row(self) -> IssueRow:
        """The issue an issue job works on."""
        if self.issue is None:
            message = f"{self.kind.value} job {self.sequence} holds no issue"
            raise ValueError(message)
        return self.issue


@dataclass(frozen=True)
class Env:
    """What a job reads and never keeps: the policy, the board as last read and the run's view."""

    config: IssueQueueConfig
    view: RunView
    # The revision a new job builds on: the last closed issue's candidate, else the input.
    base: RevisionRef
    board: tuple[IssueRow, ...]
    performance: tuple[PerformanceRecord, ...]


STOPPED = JobFailure(kind=FailureKind.STOPPED)


def failure_of(event: Failed, role: Role | None) -> JobFailure:
    """The typed account of a wait that ended without a result."""
    return JobFailure(
        kind=FailureKind.ENDED,
        role=role,
        reason=event.reason,
        cause=event.cause,
        refusal=event.refusal,
        turn_failure=event.turn_failure,
    )


# -- decisions ---------------------------------------------------------------------------------


def operation(name: DecisionId, request: OperationRequest, seconds: float) -> Decision:
    """A registered operation, awaited for as long as the config allows one."""
    return Wait(
        request=Operation(decision_id=name, scope=THIS_RUN, request=request),
        until=Within(limit=seconds),
    )


def _start(job: Job, env: Env) -> Decision:
    config = env.config
    paid = config.max_attempts_per_issue if job.kind is JobKind.ISSUE else 1
    # A logical turn may be corrected or asked again this often; an issue has a turn for the
    # implementer and one for the judge for each of its attempts.
    turns = 2 * paid
    request = StartAttempt(
        decision_id=decision(job.key, "start"),
        scope=THIS_RUN,
        attempt_id=job.attempt,
        item_id=item_id(job.kind.value, job.sequence),
        workspace=WorkspacePlan(mode=WorkspaceMode.ISOLATED_CHILD, base=job.base),
        budget=AttemptBudget(
            admission_charge=1,
            paid_invocation_limit=paid,
            retry_limit=(config.max_corrections + config.max_turn_drops) * turns,
        ),
    )
    return Wait(request=request, until=until_deadline(env.view))


def _turn_seconds(config: IssueQueueConfig, role: Role) -> float:
    match role:
        case Role.IMPLEMENTER:
            return config.implementer_turn_seconds
        case Role.JUDGE:
            return config.judge_turn_seconds
        case Role.PERF:
            return config.perf_turn_seconds


def _ask(job: Job, env: Env, role: Role, prompt: Value, revision: RevisionRef) -> Decision:
    """Ask ``role`` for the turn the job's phase is waiting for."""
    request = AskAgent(
        decision_id=decision(job.key, role.value, _turn_number(job)),
        scope=job.scope,
        agent=role.value,
        input=prompt,
        revision=revision,
    )
    return Wait(request=request, until=Within(limit=_turn_seconds(env.config, role)))


def _ask_implementer(job: Job, env: Env, spent: int, rejected: Judged | None) -> Moved:
    prompt = ImplementPrompt(
        issue=board.detail(job.issue_row),
        board=board.briefs(env.board),
        feedback=None if rejected is None else (rejected.feedback or rejected.analysis),
        analysis=None if rejected is None else rejected.analysis,
    )
    moved = _moved(job, Implementing(spent=spent))
    return moved, [_ask(moved, env, Role.IMPLEMENTER, prompt, job.base)]


def _begin_review(job: Job, env: Env, phase: Recording) -> Moved:
    """The attempt is recorded: decode the evaluations its author ran, then ask the judge."""
    review = Reviewing(spent=phase.spent, work=phase.work, candidate=phase.candidate)
    refs = evidence.review_refs(env.view, job.scope)
    if not refs:
        return _ask_judge(job, env, review, ())
    reading = ReadingEvidence(
        spent=phase.spent, work=phase.work, candidate=phase.candidate, refs=refs
    )
    name = decision(job.key, "review-evidence", phase.spent - 1)
    request = Operation(decision_id=name, scope=job.scope, request=InterpretEvidence(evidence=refs))
    wait = Wait(request=request, until=Within(limit=env.config.operation_seconds))
    return _moved(job, reading), [wait]


def _after_evidence(job: Job, env: Env, event: Event, phase: ReadingEvidence) -> Moved:
    """The owner decoded the evaluations, or could not: the judge is asked either way."""
    name = decision(job.key, "review-evidence", phase.spent - 1)
    decided = getattr(event, "decision", None)
    if decided not in (name, _start_id(job)):
        return job, []
    if isinstance(event, OperationFinished):
        readings = evidence.readings_of(event.result.outcome, phase.refs)
        review = Reviewing(spent=phase.spent, work=phase.work, candidate=phase.candidate)
        return _ask_judge(job, env, review, evidence.evaluations(phase.refs, readings))
    return fail(job, env, failure_of(event, None))  # ty: ignore[invalid-argument-type]


def _ask_judge(
    job: Job, env: Env, phase: Reviewing, evaluations: tuple[ReviewEvaluation, ...]
) -> Moved:
    prompt = ReviewPrompt(
        issue=board.detail(job.issue_row),
        board=board.briefs(env.board),
        summary=phase.work.summary,
        files_touched=phase.work.files_touched,
        self_check=phase.work.self_check,
        evaluations=evaluations,
    )
    moved = _moved(job, phase)
    return moved, [_ask(moved, env, Role.JUDGE, prompt, phase.candidate or job.base)]


def _ask_evaluator(job: Job, env: Env) -> Moved:
    prompt = PerformancePrompt(
        iteration=job.iteration,
        max_new_issues=env.config.max_issues_per_perf_eval,
        board=board.briefs(env.board),
        prior=tuple(
            PriorPerformance(
                iteration=record.iteration,
                primary_trend=record.primary_trend,
                secondary_trend=record.secondary_trend or "",
                metrics_json=record.metrics_json,
                new_issues=len(record.new_issues),
            )
            for record in env.performance
        ),
        load_levels_json=env.config.load_levels_json,
    )
    moved = _moved(job, Evaluating())
    return moved, [_ask(moved, env, Role.PERF, prompt, job.base)]


# -- board writes ------------------------------------------------------------------------------


def change_key(run: str, job: Job | None, what: str) -> str:
    """The key of one board change: unique to this run, job and fact.

    The tracker stores it with the issue or event it writes, which is how a replayed write
    is recognized as already landed. The run identity keeps two runs on one GitHub
    repository from recognizing each other's writes.
    """
    scope = "run" if job is None else job.key
    return f"{run}/{scope}/{what}"


def _run_of(env: Env) -> str:
    return env.view.run.owner.root


def _creates(
    job: Job, env: Env, filed: tuple[FiledIssue, ...], created_by: str, tag: int
) -> tuple[IssueChange, ...]:
    return tuple(
        CreateIssue(
            key=change_key(_run_of(env), job, f"filed.{tag}.{index}"),
            type=item.type,
            title=item.title,
            body=TextBody(text=item.description),
            created_by=created_by,
            iteration=job.iteration,
        )
        for index, item in enumerate(filed)
    )


def _implementation_json(work: Implemented) -> str:
    return IssueImplementerResult(
        summary=work.summary, files_touched=work.files_touched, self_check=work.self_check
    ).model_dump_json()


def _verdict_json(verdict: Judged) -> str:
    return IssueJudgeResult(
        analysis=verdict.analysis,
        feedback=verdict.feedback,
        verdict="pass" if verdict.passed else "fail",
        new_issues=tuple(
            NewBug(type="bug", title=item.title, description=item.description)
            for item in verdict.filed
        ),
    ).model_dump_json()


def _log(job: Job, env: Env, step: ProgressStep, payload: str, what: str) -> Decision:
    """A progress entry, named ``what``; nothing waits for its answer."""
    issue_id = None if job.issue is None else job.issue.id
    entry = LogProgress(
        key=change_key(_run_of(env), job, what),
        iteration=job.iteration,
        step=step,
        issue_id=issue_id,
        payload_json=payload,
    )
    return operation(decision(job.key, what), entry, env.config.operation_seconds)


def _claim_write(job: Job, env: Env) -> tuple[IssueChange, ...]:
    return (
        SetIssueStatus(
            key=change_key(_run_of(env), job, "claim"),
            issue_id=job.issue_row.id,
            status=IssueStatus.IN_PROGRESS,
            actor="loop",
            iteration=job.iteration,
            note="claimed for processing",
        ),
    )


def _record_write(job: Job, env: Env, spent: int, work: Implemented) -> tuple[IssueChange, ...]:
    return (
        RecordAttempt(
            key=change_key(_run_of(env), job, f"attempt.{spent}"),
            issue_id=job.issue_row.id,
            actor="implementer",
            iteration=job.iteration,
            note=work.summary[:_NOTE_LIMIT],
            payload_json=_implementation_json(work),
        ),
    )


def _verdict_write(job: Job, env: Env, spent: int, verdict: Judged) -> tuple[IssueChange, ...]:
    note = (
        f"closed by judge after attempt {spent}"
        if verdict.passed
        else verdict.feedback[:_FEEDBACK_LIMIT]
    )
    # A failed verdict's note is the feedback the implementer will read, or the analysis when
    # the judge gave none.
    if not verdict.passed and not verdict.feedback:
        note = verdict.analysis[:_FEEDBACK_LIMIT]
    status = IssueStatus.CLOSED if verdict.passed else IssueStatus.IN_PROGRESS
    change = SetIssueStatus(
        key=change_key(_run_of(env), job, f"verdict.{spent}"),
        issue_id=job.issue_row.id,
        status=status,
        actor="judge",
        iteration=job.iteration,
        note=note,
        payload_json=_verdict_json(verdict),
    )
    created = _creates(job, env, verdict.filed, "judge", spent)
    return (change, *created)


def _block_write(job: Job, env: Env) -> tuple[IssueChange, ...]:
    return (
        SetIssueStatus(
            key=change_key(_run_of(env), job, "block"),
            issue_id=job.issue_row.id,
            status=IssueStatus.BLOCKED,
            actor="loop",
            iteration=job.iteration,
            note=f"exhausted {env.config.max_attempts_per_issue} attempts",
        ),
    )


def _send(job: Job, env: Env, what: str, sent: int, changes: tuple[IssueChange, ...]) -> Decision:
    """One board write, numbered by how often it was sent so each send has its own identity."""
    return operation(
        decision(job.key, what, sent - 1),
        ApplyIssueChanges(changes=changes),
        env.config.operation_seconds,
    )


def _landed(event: Event, name: DecisionId) -> AppliedChanges | None:
    """The outcome of the write ``name`` when this event is its answer."""
    if isinstance(event, OperationFinished) and event.decision == name:
        outcome = event.result.outcome
        return outcome if isinstance(outcome, AppliedChanges) else None
    return None


# -- moving between phases ---------------------------------------------------------------------


def _moved(job: Job, phase: JobPhase) -> Job:
    return job.model_copy(update={"phase": phase})


def _disposition(job: Job, closing: Closing) -> Settle:
    """The assessments, eligibility and retention to propose for a finished job.

    A closed issue whose turn changed the tree is a retained, eligible candidate vouched for by
    the judge, and the next job builds on it. Nothing else is kept: an issue that closed
    without a change, a blocked or failed one, and a performance evaluation settle discarded.
    """
    failure = closing.failure
    if failure is not None and failure.kind is FailureKind.STOPPED:
        return Settle(assessments=(), eligible=False, retention="discard", outcome="cancelled")
    if (
        job.kind is JobKind.ISSUE
        and closing.issue_closed
        and closing.candidate is not None
        and closing.judge is not None
    ):
        return Settle(
            assessments=(
                AssessmentProposal(
                    kind=AssessmentKind.LOCAL_VALIDATION,
                    verdict="satisfied",
                    sources=(closing.judge,),
                    candidate=closing.candidate,
                    schema_version=1,
                ),
            ),
            eligible=True,
            retention="candidate",
            outcome="succeeded",
            candidate=closing.candidate,
        )
    failed = failure is not None or (job.kind is JobKind.ISSUE and not closing.issue_closed)
    return Settle(
        assessments=(),
        eligible=False,
        retention="discard",
        outcome="failed" if failed else "succeeded",
    )


def _close(job: Job, settling: Settling, env: Env) -> Decision:
    """The close that settles the attempt: the full assertion, then the plain one."""
    if settling.refused:
        disposition = Settle(assessments=(), eligible=False, retention="discard", outcome="failed")
    else:
        disposition = _disposition(job, settling.closing)
    request = Withdraw(
        decision_id=decision(job.key, "settle", settling.refused),
        scope=THIS_RUN,
        target=job.ref,
        disposition=disposition,
    )
    return Wait(request=request, until=until_deadline(env.view))


def settle(job: Job, env: Env, closing: Closing) -> Moved:
    """Close the workspace, asserting what the job established."""
    settling = Settling(closing=closing)
    return _moved(job, settling), [_close(job, settling, env)]


def fail(job: Job, env: Env, failure: JobFailure) -> Moved:
    """End the job with a typed failure; an admitted attempt must still settle."""
    if isinstance(job.phase, Starting):
        return _moved(job, Done(failure=failure)), []
    return settle(job, env, Closing(failure=failure))


def _record_of(job: Job, closing: Closing) -> PerformanceRecord | None:
    evaluation = closing.evaluation
    if job.kind is not JobKind.PERF or closing.failure is not None or evaluation is None:
        return None
    return PerformanceRecord(
        iteration=job.iteration,
        primary_trend=evaluation.primary_trend,
        secondary_trend=evaluation.secondary_trend,
        metrics_json=evaluation.metrics_json,
        new_issues=closing.created,
    )


def _done(job: Job, closing: Closing, settlement: Settlement | None) -> Job:
    return _moved(
        job,
        Done(
            failure=closing.failure,
            settlement=settlement,
            issue_closed=closing.issue_closed,
            record=_record_of(job, closing),
        ),
    )


# -- the first decisions of a job --------------------------------------------------------------


def begin(kind: JobKind, sequence: int, issue: IssueRow | None, iteration: int, env: Env) -> Moved:
    """A new job, building on the revision the environment names: its attempt is requested."""
    job = Job(
        kind=kind,
        sequence=sequence,
        iteration=iteration,
        issue=issue,
        base=env.base,
        attempt=attempt_id(kind.value, sequence),
        phase=Starting(),
    )
    return job, [_start(job, env)]


def _opened(job: Job, env: Env) -> Moved:
    """The attempt holds a slot: claim the issue, or start the evaluation."""
    if job.kind is JobKind.PERF:
        return _ask_evaluator(job, env)
    claim = _send(job, env, "claim", 1, _claim_write(job, env))
    return _moved(job, Claiming()), [claim]


# -- update ------------------------------------------------------------------------------------


def update(job: Job, event: Event, env: Env) -> Moved:
    """One event: move to the next phase and say what that phase needs."""
    match job.phase, event:
        # a progress entry is written and forgotten: its answer changes nothing
        case _, OperationFinished(decision=name) | Failed(decision=name) if is_log(name):
            moved = job, []
        # an operator stop reaches every phase; it never cancels a turn, it settles around it
        case Starting(), StopRequested():
            moved = _moved(job, Starting(stopped=True)), []
        case Settling() | Done(), StopRequested():
            moved = job, []
        case _, StopRequested():
            moved = settle(job, env, Closing(failure=STOPPED))
        case Starting() as phase, WorkspaceOpened() | Failed():
            moved = _started(job, phase, event, env)
        case Settling() as settling, WorkspaceClosed(settlement=settlement):
            moved = _done(job, settling.closing, settlement), []
        case Settling() as settling, Failed(decision=name) if name == decision(
            job.key, "settle", settling.refused
        ):
            moved = _close_refused(job, env, settling, event)
        case Settling(), _:
            # a late result of work the settlement already went around
            moved = job, []
        case Evaluating() | Reporting(), _:
            moved = _evaluation_event(job, event, env)
        case _:
            moved = _issue_event(job, event, env)
    return moved


def _started(job: Job, phase: Starting, event: Event, env: Env) -> Moved:
    """The attempt's start was answered: it holds a slot, or it never will."""
    match event:
        case WorkspaceOpened(attempt=opened):
            ready = job.model_copy(update={"generation": opened.generation})
            if phase.stopped:
                return settle(ready, env, Closing(failure=STOPPED))
            return _opened(ready, env)
        case Failed():
            failure = STOPPED if phase.stopped else failure_of(event, None)
            return _moved(job, Done(failure=failure)), []
        case _:
            raise UnexpectedEvent(job.phase, event)


def _issue_event(job: Job, event: Event, env: Env) -> Moved:
    """One event of an issue job: its claim, implementation, record, review and verdict."""
    match job.phase, event:
        case Claiming(sent=sent), OperationFinished() | Failed():
            moved = _after_claim(job, env, event, sent)
        case Implementing(spent=spent), TurnFinished(
            reply=IssueImplementerResult() as reply, checkpoint=checkpoint
        ):
            moved = _implemented(job, env, spent, reply, checkpoint)
        case Implementing(), Failed():
            moved = _failed_turn(job, env, event, Role.IMPLEMENTER)
        case Recording() as phase, OperationFinished() | Failed():
            moved = _after_record(job, env, event, phase)
        case ReadingEvidence() as phase, OperationFinished() | Failed():
            moved = _after_evidence(job, env, event, phase)
        case Reviewing() as phase, TurnFinished(
            reply=IssueJudgeResult() as reply, invocation=invocation
        ):
            moved = _reviewed(job, env, phase, reply, invocation)
        case Reviewing(), Failed():
            moved = _failed_turn(job, env, event, Role.JUDGE)
        case Filing() as phase, OperationFinished() | Failed():
            moved = _after_verdict(job, env, event, phase)
        case Blocking(sent=sent), OperationFinished() | Failed():
            moved = _after_block(job, env, event, sent)
        case _:
            raise UnexpectedEvent(job.phase, event)
    return moved


def _evaluation_event(job: Job, event: Event, env: Env) -> Moved:
    """One event of a performance job: the evaluator's turn and the filing of its issues."""
    match job.phase, event:
        case Evaluating(), TurnFinished(
            reply=IssuePerfEvalResult() | GenericIssuePerfEvalResult() as reply,
            checkpoint=checkpoint,
        ):
            return _evaluated(job, env, reply, checkpoint)
        case Evaluating(), Failed():
            return _failed_turn(job, env, event, Role.PERF)
        case Reporting() as phase, OperationFinished() | Failed():
            return _after_report(job, env, event, phase)
        case _:
            raise UnexpectedEvent(job.phase, event)


def _failed_turn(job: Job, env: Env, event: Failed, role: Role) -> Moved:
    if event.decision not in (decision(job.key, role.value, _turn_number(job)), _start_id(job)):
        return job, []
    return fail(job, env, failure_of(event, role))


def _turn_number(job: Job) -> int:
    phase = job.phase
    match phase:
        case Implementing(spent=spent) | Reviewing(spent=spent):
            return spent - 1
        case _:
            return 0


def _start_id(job: Job) -> DecisionId:
    return decision(job.key, "start")


@dataclass(frozen=True)
class Sent:
    """A board write in flight: its identity, how often it was sent and what follows its answer."""

    name: DecisionId
    number: int
    resend: Callable[[int], Moved]
    landed: Callable[[AppliedChanges], Moved]


def _answer(job: Job, env: Env, event: Event, sent: Sent) -> Moved:
    """Fold the answer of a board write.

    A write whose effect stayed unknown is sent again, as a new request with the same keys, so
    one that did land is not repeated; when the sends are spent the job fails.
    """
    decided = getattr(event, "decision", None)
    if decided not in (sent.name, _start_id(job)):
        return job, []  # the answer of a send the job already went past
    outcome = _landed(event, sent.name)
    if outcome is not None:
        return _written(job, env, outcome, sent)
    if not isinstance(event, Failed):
        return job, []
    if event.reason not in ENDED_AFTER_SEND or decided != sent.name:
        return fail(job, env, failure_of(event, None))
    if sent.number > env.config.max_board_retries:
        return fail(job, env, JobFailure(kind=FailureKind.BOARD_UNKNOWN))
    return sent.resend(sent.number + 1)


def _written(job: Job, env: Env, outcome: AppliedChanges, sent: Sent) -> Moved:
    if outcome.status != "succeeded":
        return fail(job, env, JobFailure(kind=FailureKind.BOARD_REFUSED, status=outcome.status))
    return sent.landed(outcome)


def _after_claim(job: Job, env: Env, event: Event, sent: int) -> Moved:
    def resend(number: int) -> Moved:
        write = _send(job, env, "claim", number, _claim_write(job, env))
        return _moved(job, Claiming(sent=number)), [write]

    def landed(_: AppliedChanges) -> Moved:
        return _ask_implementer(job, env, 1, None)

    name = decision(job.key, "claim", sent - 1)
    return _answer(job, env, event, Sent(name, sent, resend, landed))


def _implemented(
    job: Job,
    env: Env,
    spent: int,
    reply: IssueImplementerResult,
    checkpoint: object,
) -> Moved:
    """Fold the implementer's reply with core's checkpoint of the turn, which it always awaits."""
    match checkpoint:
        case None:
            raise UnexpectedEvent(job.phase, reply)
        case Declined(reason=declined):
            failure = JobFailure(
                kind=FailureKind.CHECKPOINT_DECLINED, role=Role.IMPLEMENTER, declined=declined
            )
            return fail(job, env, failure)
        case retained:
            revision = retained.revision  # ty: ignore[unresolved-attribute]
    changed = None if revision == job.base else revision
    work = Implemented(
        summary=reply.summary, files_touched=reply.files_touched, self_check=reply.self_check
    )
    phase = Recording(spent=spent, work=work, candidate=changed)
    write = _send(job, env, f"record.{spent}", 1, _record_write(job, env, spent, work))
    log = _log(job, env, "implement", _implementation_json(work), f"log.implement.{spent}")
    return _moved(job, phase), [write, log]


def _after_record(job: Job, env: Env, event: Event, phase: Recording) -> Moved:
    def resend(number: int) -> Moved:
        write = _send(
            job,
            env,
            f"record.{phase.spent}",
            number,
            _record_write(job, env, phase.spent, phase.work),
        )
        return _moved(job, phase.model_copy(update={"sent": number})), [write]

    def landed(_: AppliedChanges) -> Moved:
        return _begin_review(job, env, phase)

    name = decision(job.key, f"record.{phase.spent}", phase.sent - 1)
    return _answer(job, env, event, Sent(name, phase.sent, resend, landed))


def _files(filed: tuple[NewBug | NewIssue, ...]) -> tuple[FiledIssue, ...]:
    return tuple(
        FiledIssue(type=IssueType(item.type), title=item.title, description=item.description)
        for item in filed
    )


def _reviewed(
    job: Job, env: Env, phase: Reviewing, reply: IssueJudgeResult, invocation: InvocationRef
) -> Moved:
    verdict = Judged(
        passed=reply.verdict == "pass",
        analysis=reply.analysis,
        feedback=reply.feedback,
        filed=_files(reply.new_issues),
    )
    spent = phase.spent
    filing = Filing(spent=spent, candidate=phase.candidate, verdict=verdict, judge=invocation)
    write = _send(job, env, f"verdict.{spent}", 1, _verdict_write(job, env, spent, verdict))
    log = _log(job, env, "review", _verdict_json(verdict), f"log.review.{spent}")
    return _moved(job, filing), [write, log]


def _after_verdict(job: Job, env: Env, event: Event, phase: Filing) -> Moved:
    def resend(number: int) -> Moved:
        write = _send(
            job,
            env,
            f"verdict.{phase.spent}",
            number,
            _verdict_write(job, env, phase.spent, phase.verdict),
        )
        return _moved(job, phase.model_copy(update={"sent": number})), [write]

    def landed(_: AppliedChanges) -> Moved:
        return _after_filed(job, env, phase)

    name = decision(job.key, f"verdict.{phase.spent}", phase.sent - 1)
    return _answer(job, env, event, Sent(name, phase.sent, resend, landed))


def _after_filed(job: Job, env: Env, phase: Filing) -> Moved:
    """The verdict landed: close the issue, ask again, or block an issue whose attempts are spent."""
    if phase.verdict.passed:
        closing = Closing(issue_closed=True, candidate=phase.candidate, judge=phase.judge)
        return settle(job, env, closing)
    if phase.spent >= env.config.max_attempts_per_issue:
        block = _send(job, env, "block", 1, _block_write(job, env))
        return _moved(job, Blocking()), [block]
    return _ask_implementer(job, env, phase.spent + 1, phase.verdict)


def _after_block(job: Job, env: Env, event: Event, sent: int) -> Moved:
    def resend(number: int) -> Moved:
        write = _send(job, env, "block", number, _block_write(job, env))
        return _moved(job, Blocking(sent=number)), [write]

    def landed(_: AppliedChanges) -> Moved:
        return settle(job, env, Closing())

    name = decision(job.key, "block", sent - 1)
    return _answer(job, env, event, Sent(name, sent, resend, landed))


def _evaluated(
    job: Job,
    env: Env,
    reply: IssuePerfEvalResult | GenericIssuePerfEvalResult,
    checkpoint: object,
) -> Moved:
    """Fold the evaluator's reply with core's checkpoint of the turn, which it always awaits."""
    match checkpoint:
        case None:
            raise UnexpectedEvent(job.phase, reply)
        case Declined(reason=declined):
            failure = JobFailure(
                kind=FailureKind.CHECKPOINT_DECLINED, role=Role.PERF, declined=declined
            )
            return fail(job, env, failure)
        case _:
            pass
    if isinstance(reply, GenericIssuePerfEvalResult):
        primary, secondary = reply.primary_metric_trend, reply.secondary_metric_trend
    else:
        primary, secondary = reply.throughput_trend, reply.latency_trend
    evaluation = Evaluation(
        primary_trend=primary,
        secondary_trend=secondary,
        metrics_json=_metrics_json(reply),
        filed=_files(reply.new_issues),
    )
    log = _log(job, env, "performance", reply.model_dump_json(), "log.performance")
    changes = _report_write(job, env, evaluation)
    if not changes:  # nothing to file: the evaluation is over
        moved, decisions = settle(job, env, Closing(evaluation=evaluation))
        return moved, [log, *decisions]
    write = _send(job, env, "filing", 1, changes)
    return _moved(job, Reporting(evaluation=evaluation)), [write, log]


def _metrics_json(reply: IssuePerfEvalResult | GenericIssuePerfEvalResult) -> str:
    return json.dumps(reply.metrics.model_dump(mode="json"), sort_keys=True)


def _report_write(job: Job, env: Env, evaluation: Evaluation) -> tuple[IssueChange, ...]:
    return _creates(job, env, evaluation.filed, "perf_eval", 0)


def _after_report(job: Job, env: Env, event: Event, phase: Reporting) -> Moved:
    def resend(number: int) -> Moved:
        write = _send(job, env, "filing", number, _report_write(job, env, phase.evaluation))
        return _moved(job, phase.model_copy(update={"sent": number})), [write]

    def landed(outcome: AppliedChanges) -> Moved:
        created = tuple(result.issue_id for result in outcome.results)
        return settle(job, env, Closing(evaluation=phase.evaluation, created=created))

    name = decision(job.key, "filing", phase.sent - 1)
    return _answer(job, env, event, Sent(name, phase.sent, resend, landed))


def _close_refused(job: Job, env: Env, settling: Settling, event: Failed) -> Moved:
    """Core would not take the close: propose the plain one, and give up if that fails too.

    A close that nobody takes would leave the job, and its slot, waiting for ever.
    """
    closing = settling.closing
    if settling.refused:
        failure = closing.failure or failure_of(event, None)
        return _done(job, closing.model_copy(update={"failure": failure}), None), []
    plain = settling.model_copy(update={"refused": 1})
    return _moved(job, plain), [_close(job, plain, env)]
