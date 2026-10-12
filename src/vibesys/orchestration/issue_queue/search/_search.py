"""`IssueQueueSearch`: the issue queue as a pure state machine.

The run reads the board, files the first issue, works the next open issue as a job, assesses
performance when a round has none, and ends. Every decision that depends on the board is made
from a fresh read: a job makes the cached board stale, so the phase after a job is a read. The
open job is a machine of its own (`_job`); the queue gives it each event, and retires it when
its phase is `Done`.

Everything it knows about the run besides its own state it reads from the `RunView`.
"""

from vibesys.candidates import api as candidates
from vibesys.orchestration.issue_queue.search import _board as board
from vibesys.orchestration.issue_queue.search import _job as job_machine
from vibesys.orchestration.issue_queue.search._bootstrap import BOOTSTRAP_TEMPLATE, bootstrap_title
from vibesys.orchestration.issue_queue.search._config import IssueQueueConfig
from vibesys.orchestration.issue_queue.search._ending import operator_stop, result_of
from vibesys.orchestration.issue_queue.search._measure import (
    measurement_recipe,
    verification_plan,
)
from vibesys.orchestration.issue_queue.search._operations import (
    APPLY_KIND,
    INTERPRET_KIND,
    LOG_KIND,
    OPERATIONS,
    READ_KIND,
    VERIFY_KIND,
    ReadBoard,
    board_schema_ref,
)
from vibesys.orchestration.issue_queue.search._schemas import STRATEGY_ID
from vibesys.orchestration.issue_queue.search._state import (
    STATE_SCHEMA,
    Bootstrapping,
    Cursor,
    Finished,
    IssueQueueState,
    Reading,
    Selecting,
    Verifying,
    Working,
)
from vibesys.orchestration.issue_queue.search._values import (
    AllBlocked,
    BlankObjective,
    Drained,
    Ending,
    FailureKind,
    JobFailed,
    JobKind,
    RoundBudgetSpent,
    Tip,
    TrackerUnavailable,
    TrackerUse,
)
from vs_core.api.search import (
    THIS_RUN,
    AdoptionFinished,
    Decision,
    DecisionId,
    Event,
    EventKind,
    Failed,
    FailureReason,
    Finish,
    Measure,
    MeasurementFinished,
    OperationFinished,
    RevisionRef,
    RunFacts,
    RunResultProposal,
    RunView,
    SearchDeclaration,
    StopRequested,
    UnexpectedEvent,
    Wait,
    Within,
)
from vs_core.api.search import WorkKind as CoreWork
from vs_issue_tracker.api import (
    AppliedChanges,
    ApplyIssueChanges,
    BoardSnapshot,
    CreateIssue,
    IssueRow,
    IssueType,
    OutcomeStatus,
    TemplateBody,
)

_VERIFY = DecisionId(root="measure.tip")
_FINISH_AGAIN = DecisionId(root="finish.1")
_REFUSED_RESULT = "core refused the run's result"


def _read_id(number: int) -> DecisionId:
    return DecisionId(root=f"read.{number}")


def _bootstrap_id(number: int) -> DecisionId:
    return DecisionId(root=f"bootstrap.{number}")


class IssueQueueSearch:
    """Policy of the issue queue, parameterized by its static config."""

    def __init__(self, config: IssueQueueConfig) -> None:
        """Bind the search to the policy of one run."""
        self._config = config

    @property
    def declaration(self) -> SearchDeclaration:
        """Operations, capabilities and event kinds the run must (or may) offer."""
        return SearchDeclaration(
            strategy_id=STRATEGY_ID,
            state_schema=STATE_SCHEMA,
            required_operations=(
                board_schema_ref(READ_KIND),
                board_schema_ref(APPLY_KIND),
                board_schema_ref(LOG_KIND),
                OPERATIONS.schema_ref(INTERPRET_KIND),
            ),
            # The kit registers it beside the others; this search builds only on revisions it
            # retained itself, so it never asks.
            optional_operations=(OPERATIONS.schema_ref(VERIFY_KIND),),
            optional=frozenset({"suspend"}),
            uses=frozenset(
                {
                    CoreWork.ASK_AGENT,
                    CoreWork.START_ATTEMPT,
                    CoreWork.WITHDRAW,
                    CoreWork.MEASURE,
                    CoreWork.PROPOSE_WINNER,
                    CoreWork.OPERATION,
                }
            ),
            handles=frozenset(
                {
                    EventKind.WORKSPACE_OPENED,
                    EventKind.WORKSPACE_CLOSED,
                    EventKind.TURN_FINISHED,
                    EventKind.MEASUREMENT_FINISHED,
                    EventKind.OPERATION_FINISHED,
                    EventKind.ADOPTION_FINISHED,
                    EventKind.STOP_REQUESTED,
                    EventKind.FAILED,
                }
            ),
        )

    @property
    def state_model(self) -> type[IssueQueueState]:
        """The class of the persisted state."""
        return IssueQueueState

    # -- start ----------------------------------------------------------------------------------

    def init(
        self, config: IssueQueueConfig, facts: RunFacts
    ) -> tuple[IssueQueueState, list[Decision]]:
        """Read the board, or end at once when the objective gives no first issue."""
        del config
        if not bootstrap_title(facts.objective):
            state = IssueQueueState(run=Finished(winner=None))
            return state, [_finish(result_of(BlankObjective(), Tip(), None))]
        return self._read(IssueQueueState(), Cursor.START, sent=1)

    # -- update ---------------------------------------------------------------------------------

    def update(
        self, state: IssueQueueState, event: Event, view: RunView
    ) -> tuple[IssueQueueState, list[Decision]]:
        """One event: to the phase it belongs to, then whatever follows from the state."""
        match state.run, event:
            case Finished(), Failed(decision=name) if name == candidates.FINISH:
                return self._finish_again(state)
            case Finished(), _:
                return state, []
            case Selecting() as phase, AdoptionFinished() | Failed() if _answers_proposal(event):
                return self._selected(
                    state, phase, candidates.update_selection(phase.adopting, event)
                )
            case _, StopRequested():
                return self._stop(state, event, view)
            case _, _ if getattr(event, "decision", None) is None:
                # an event that answers no decision of this search (an agent's own measurement,
                # a close core asked for) is the run's business, not the search's
                return state, []
            case _:
                return self._in_phase(state, event, view)

    def _in_phase(
        self, state: IssueQueueState, event: Event, view: RunView
    ) -> tuple[IssueQueueState, list[Decision]]:
        match state.run, event:
            case Working(job=job), _:
                return self._work(state, job, event, view)
            case Reading() as phase, OperationFinished() | Failed():
                return self._read_answered(state, phase, event, view)
            case Bootstrapping() as phase, OperationFinished() | Failed():
                return self._bootstrap_answered(state, phase, event, view)
            case Verifying() as phase, MeasurementFinished() | Failed():
                return self._verified(state, phase, event, view)
            case Selecting() | Verifying() | Reading() | Bootstrapping(), OperationFinished():
                return state, []  # the answer of a progress entry the retired job wrote
            case _:
                raise UnexpectedEvent(state.run, event)

    # -- stop -----------------------------------------------------------------------------------

    def _stop(
        self, state: IssueQueueState, event: StopRequested, view: RunView
    ) -> tuple[IssueQueueState, list[Decision]]:
        """An operator stop settles the open job; it cancels no turn and adopts nothing."""
        if event.control.action != "stop":
            return state, []
        state = state.model_copy(update={"stopping": True})
        match state.run:
            case Working(job=job):
                return self._work(state, job, event, view)
            case Selecting():
                return state, []  # the proposal is out: its answer ends the run
            case _:
                return self._end(state, operator_stop(), None)

    # -- the open job ---------------------------------------------------------------------------

    def _env(self, state: IssueQueueState, view: RunView) -> job_machine.Env:
        return job_machine.Env(
            config=self._config,
            view=view,
            base=state.tip.revision or view.facts.baseline,
            board=state.board,
            performance=state.performance,
        )

    def _work(
        self, state: IssueQueueState, job: job_machine.Job, event: Event, view: RunView
    ) -> tuple[IssueQueueState, list[Decision]]:
        moved, decisions = job_machine.update(job, event, self._env(state, view))
        state = state.model_copy(update={"run": Working(job=moved)})
        if not isinstance(moved.phase, job_machine.Done):
            return state, decisions
        state, more = self._retire(state, moved, view)
        return state, [*decisions, *more]

    def _retire(
        self, state: IssueQueueState, job: job_machine.Job, view: RunView
    ) -> tuple[IssueQueueState, list[Decision]]:
        """Clear a finished job and move the run to what follows it.

        A finished issue counts toward the round's cap and a finished evaluation closes the
        round's work; either makes the board stale. A job that failed ends the run: its issue
        is left as the board last recorded it, and nothing is picked up again that could fail
        the same way.
        """
        done = job.phase
        if not isinstance(done, job_machine.Done):
            raise UnexpectedEvent(done, None)
        issue = job.kind is JobKind.ISSUE
        update: dict[str, object] = {"processed": state.processed + (1 if issue else 0)}
        settlement = done.settlement
        if issue and done.issue_closed and settlement is not None and settlement.candidate:
            update["tip"] = Tip(revision=settlement.candidate, settlement=settlement.settlement_id)
        if done.record is not None:
            update["performance"] = (*state.performance, done.record)
        state = state.model_copy(update=update)
        failure = done.failure
        if state.stopping:
            return self._end(state, operator_stop(), None)
        if failure is not None and failure.kind is not FailureKind.STOPPED:
            ending = JobFailed(
                job=job.kind,
                issue_id=None if job.issue is None else job.issue.id,
                iteration=job.iteration,
                failure=failure,
            )
            return self._conclude(state, ending, view)
        then = Cursor.DRAIN if issue else Cursor.AFTER_PERF
        return self._read(state, then, sent=1)

    # -- reading the board ----------------------------------------------------------------------

    def _read(
        self, state: IssueQueueState, then: Cursor, *, sent: int
    ) -> tuple[IssueQueueState, list[Decision]]:
        """Send a read of the board; each send has an identity of its own."""
        request = job_machine.operation(
            _read_id(state.reads), ReadBoard(), self._config.operation_seconds
        )
        state = state.model_copy(
            update={"run": Reading(then=then, sent=sent), "reads": state.reads + 1}
        )
        return state, [request]

    def _read_answered(
        self, state: IssueQueueState, phase: Reading, event: Event, view: RunView
    ) -> tuple[IssueQueueState, list[Decision]]:
        name = _read_id(state.reads - 1)
        match event:
            case OperationFinished(decision=answered, result=result) if answered == name:
                outcome = result.outcome
                if isinstance(outcome, BoardSnapshot) and outcome.status == "succeeded":
                    state = state.model_copy(update={"board": outcome.issues})
                    return self._decide(state, phase.then, view)
                status = outcome.status if isinstance(outcome, BoardSnapshot) else None
                return self._read_again(state, phase, view, status=status, reason=None)
            case Failed(decision=answered, reason=reason) if answered == name:
                if reason in job_machine.ENDED_AFTER_SEND:
                    return self._read_again(state, phase, view, status=None, reason=reason)
                return self._unavailable(state, view, TrackerUse.READING, None, reason)
            case _:
                return state, []

    def _read_again(
        self,
        state: IssueQueueState,
        phase: Reading,
        view: RunView,
        *,
        status: OutcomeStatus | None,
        reason: FailureReason | None,
    ) -> tuple[IssueQueueState, list[Decision]]:
        """Send the read again, as a new request, until the retries are spent."""
        if phase.sent > self._config.max_board_retries:
            return self._unavailable(state, view, TrackerUse.READING, status, reason)
        return self._read(state, phase.then, sent=phase.sent + 1)

    def _unavailable(
        self,
        state: IssueQueueState,
        view: RunView,
        during: TrackerUse,
        status: OutcomeStatus | None,
        reason: FailureReason | None,
    ) -> tuple[IssueQueueState, list[Decision]]:
        ending = TrackerUnavailable(during=during, status=status, reason=reason)
        return self._conclude(state, ending, view)

    # -- the first issue ------------------------------------------------------------------------

    def _bootstrap(
        self, state: IssueQueueState, view: RunView, *, sent: int
    ) -> tuple[IssueQueueState, list[Decision]]:
        create = CreateIssue(
            key=job_machine.change_key(view.run.owner.root, None, "bootstrap"),
            type=IssueType.FEATURE,
            title=bootstrap_title(view.facts.objective),
            body=TemplateBody(template=BOOTSTRAP_TEMPLATE),
            created_by="loop:bootstrap",
            iteration=state.round_idx + 1,
        )
        write = job_machine.operation(
            _bootstrap_id(sent - 1),
            ApplyIssueChanges(changes=(create,)),
            self._config.operation_seconds,
        )
        return state.model_copy(update={"run": Bootstrapping(sent=sent)}), [write]

    def _bootstrap_answered(
        self, state: IssueQueueState, phase: Bootstrapping, event: Event, view: RunView
    ) -> tuple[IssueQueueState, list[Decision]]:
        name = _bootstrap_id(phase.sent - 1)
        match event:
            case OperationFinished(decision=answered, result=result) if answered == name:
                outcome = result.outcome
                if isinstance(outcome, AppliedChanges) and outcome.status == "succeeded":
                    state = state.model_copy(update={"bootstrapped": True})
                    return self._read(state, Cursor.DRAIN, sent=1)
                status = outcome.status if isinstance(outcome, AppliedChanges) else None
                return self._unavailable(state, view, TrackerUse.BOOTSTRAP, status, None)
            case Failed(decision=answered, reason=reason) if answered == name:
                spent = phase.sent > self._config.max_board_retries
                if reason in job_machine.ENDED_AFTER_SEND and not spent:
                    # the same keys: a write that did land is not repeated
                    return self._bootstrap(state, view, sent=phase.sent + 1)
                return self._unavailable(state, view, TrackerUse.BOOTSTRAP, None, reason)
            case _:
                return state, []

    # -- what follows a fresh board -------------------------------------------------------------

    def _decide(
        self, state: IssueQueueState, cursor: Cursor, view: RunView
    ) -> tuple[IssueQueueState, list[Decision]]:
        match cursor:
            case Cursor.START if not state.bootstrapped:
                return self._bootstrap(state, view, sent=1)
            case Cursor.START | Cursor.DRAIN:
                return self._drain(state, view)
            case Cursor.AFTER_PERF:
                return self._after_perf(state, view)

    def _drain(
        self, state: IssueQueueState, view: RunView
    ) -> tuple[IssueQueueState, list[Decision]]:
        config = self._config
        if state.round_idx >= config.max_rounds:
            return self._conclude(state, RoundBudgetSpent(rounds=config.max_rounds), view)
        pick = (
            None if state.processed >= config.max_issues_per_round else board.next_open(state.board)
        )
        if pick is not None:
            return self._open(state, JobKind.ISSUE, pick, view)
        if board.blocked_queue(state.board):
            count = len(board.remaining(state.board))
            return self._conclude(state, AllBlocked(remaining=count), view)
        return self._open(state, JobKind.PERF, None, view)

    def _after_perf(
        self, state: IssueQueueState, view: RunView
    ) -> tuple[IssueQueueState, list[Decision]]:
        last = state.performance[-1] if state.performance else None
        filed = last is not None and last.iteration == state.round_idx + 1 and bool(last.new_issues)
        if not board.has_open(state.board) and not filed:
            return self._conclude(state, Drained(), view)
        state = state.model_copy(update={"round_idx": state.round_idx + 1, "processed": 0})
        return self._drain(state, view)

    def _open(
        self, state: IssueQueueState, kind: JobKind, issue: IssueRow | None, view: RunView
    ) -> tuple[IssueQueueState, list[Decision]]:
        sequence = state.jobs_started + 1
        job, decisions = job_machine.begin(
            kind, sequence, issue, state.round_idx + 1, self._env(state, view)
        )
        state = state.model_copy(update={"run": Working(job=job), "jobs_started": sequence})
        return state, decisions

    # -- the end --------------------------------------------------------------------------------

    def _conclude(
        self, state: IssueQueueState, ending: Ending, view: RunView
    ) -> tuple[IssueQueueState, list[Decision]]:
        """Nothing is left to do: adopt the work the closed issues accumulated, then finish.

        The root workspace stays at the input until adoption, so that work lives in the
        retained candidate of the last closed issue (each issue builds on the one before). The
        run adopts it whatever the ending, so work done before a budget stop or a blocked
        queue is not lost. A run whose issues changed nothing adopts nothing.
        """
        tip = state.tip
        if tip.settlement is None or tip.revision is None:
            return self._end(state, result_of(ending, tip, None), None)
        config = self._config
        if config.accuracy_configured:
            # Core adopts only a revision with an accepted accuracy proof of its own.
            return self._verify(state, ending, tip.revision, view)
        return self._select(state, ending, view)

    def _verify(
        self, state: IssueQueueState, ending: Ending, revision: RevisionRef, view: RunView
    ) -> tuple[IssueQueueState, list[Decision]]:
        """Measure the final candidate's accuracy; core adopts only a revision with that proof."""
        config = self._config
        plan = verification_plan(config, view.facts, revision, view.now_at)
        budget = measurement_recipe(config, view.facts).budget(("accuracy",))
        request = Measure(decision_id=_VERIFY, scope=THIS_RUN, plan=plan)
        wait = Wait(request=request, until=Within(limit=budget))
        return state.model_copy(update={"run": Verifying(ending=ending)}), [wait]

    def _verified(
        self, state: IssueQueueState, phase: Verifying, event: Event, view: RunView
    ) -> tuple[IssueQueueState, list[Decision]]:
        """The measurement ended; core decides at adoption whether it proved the accuracy."""
        match event:
            case MeasurementFinished(decision=answered) | Failed(decision=answered) if (
                answered == _VERIFY
            ):
                return self._select(state, phase.ending, view)
            case _:
                return state, []

    def _select(
        self, state: IssueQueueState, ending: Ending, view: RunView
    ) -> tuple[IssueQueueState, list[Decision]]:
        tip = state.tip
        if tip.revision is None:
            raise UnexpectedEvent(state.run, ending)
        winner = candidates.Winner(
            settlement=tip.settlement, revision=tip.revision, hypothesis_id=None
        )
        adopting, decisions = candidates.begin_selection(winner, view)
        return state.model_copy(
            update={"run": Selecting(ending=ending, adopting=adopting)}
        ), decisions

    def _selected(
        self, state: IssueQueueState, phase: Selecting, winner: candidates.Winner | None
    ) -> tuple[IssueQueueState, list[Decision]]:
        if state.stopping:
            return self._end(state, operator_stop(), winner)
        return self._end(state, result_of(phase.ending, state.tip, winner), winner)

    def _end(
        self, state: IssueQueueState, result: RunResultProposal, winner: candidates.Winner | None
    ) -> tuple[IssueQueueState, list[Decision]]:
        return state.model_copy(update={"run": Finished(winner=winner)}), [_finish(result)]

    @staticmethod
    def _finish_again(state: IssueQueueState) -> tuple[IssueQueueState, list[Decision]]:
        """Core refused the result: the plain failure is proposed once."""
        result = RunResultProposal(outcome="failure", reason=_REFUSED_RESULT)
        plain = Finish(decision_id=_FINISH_AGAIN, scope=THIS_RUN, mode="drain", result=result)
        return state, [plain]


def _answers_proposal(event: Event) -> bool:
    """Whether the event is core's answer to the proposed adoption."""
    return isinstance(event, AdoptionFinished) or (
        isinstance(event, Failed) and event.decision == candidates.PROPOSE
    )


def _finish(result: RunResultProposal) -> Finish:
    return Finish(decision_id=candidates.FINISH, scope=THIS_RUN, mode="drain", result=result)
