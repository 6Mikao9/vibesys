"""What the jobs of an issue-queue run are told, and what they leave behind, as plain typed values.

A job (working one issue, or one performance evaluation) is told what to do (`IssueJob`,
`PerfJob`) and ends with a `JobDone` or `PerfDone` the run folds into its queue. Whatever stops a
job early is a typed `Failure`; nothing here awaits or reads the run.
"""

from enum import StrEnum
from typing import ClassVar, Literal

from pydantic import Field

from vibesys.orchestration.issue_queue.search._config import IssueQueueConfig
from vibesys.orchestration.issue_queue.search._prompts import IssueBrief
from vs_core.api.search import (
    AttemptBudget,
    AttemptId,
    Failed,
    InvocationRef,
    ItemId,
    Kept,
    RevisionRef,
    Value,
    WorkspaceMode,
    WorkspacePlan,
    attempt_id,
    item_id,
)
from vs_issue_tracker.api import IssueRow


class JobKind(StrEnum):
    """What a job does with its attempt."""

    ISSUE = "issue"
    PERF = "perf"


class FailureKind(StrEnum):
    """Why a job could not be completed. Closed."""

    BOARD = "board"
    """A request to the issue board could not be performed."""
    AGENT = "agent"
    """An agent's turn ended without a usable reply."""
    CHECKPOINT_DECLINED = "checkpoint-declined"
    """Core did not retain the workspace an implementer's turn left."""
    NOT_OPENED = "not-opened"
    """The attempt's workspace could not be opened."""


class Failure(Value):
    """Why a job ended without completing, with the facts that say how."""

    kind: FailureKind
    role: str | None = None
    """The agent role the failure belongs to."""
    reason: str = ""
    """What was being done (a board request), or the typed reason core gave."""
    detail: str = ""

    def describe(self) -> str:
        """The failure as one line of the run's ending."""
        match self.kind:
            case FailureKind.BOARD:
                return f"the issue board failed while {self.reason}: {self.detail}"
            case FailureKind.AGENT:
                return f"{self.role} turn did not complete ({self.reason})"
            case FailureKind.CHECKPOINT_DECLINED:
                return f"{self.role} checkpoint declined ({self.reason})"
            case FailureKind.NOT_OPENED:
                return f"the attempt's workspace could not be opened ({self.reason})"


def failure_of(failed: Failed, role: str | None = None) -> Failure:
    """The typed account of a wait that ended without a result."""
    return Failure(kind=FailureKind.AGENT, role=role, reason=failed.reason.value)


class JobEndedError(Exception):
    """A job cannot go on: it ends with this failure and settles with what it has."""

    def __init__(self, failure: Failure) -> None:
        """Carry the failure the job ends with."""
        super().__init__(failure.describe())
        self.failure = failure


class Ending(Value):
    """How a run ends once nothing is in flight."""

    outcome: Literal["success", "failure"]
    reason: str = Field(min_length=1)

    @classmethod
    def success(cls, reason: str) -> "Ending":
        """The run achieved what it set out to."""
        return cls(outcome="success", reason=reason)

    @classmethod
    def failure(cls, reason: str) -> "Ending":
        """The run stopped short of it."""
        return cls(outcome="failure", reason=reason)


class PerfRecord(Value):
    """A finished performance evaluation, shown to the next one."""

    iteration: int = Field(gt=0)
    primary_trend: str
    secondary_trend: str
    metrics_json: str
    new_issues: tuple[int, ...] = ()
    """The ids of the issues the evaluation filed, in the order filed."""


class _Job(Value):
    """What every job is told: its place in the run, the revision it builds on and the board."""

    kind: ClassVar[JobKind]

    sequence: int = Field(gt=0)
    """One more than the jobs started before it, so each job's identities are its own."""
    iteration: int = Field(gt=0)
    run_id: str
    base: RevisionRef
    """The revision the job builds on: the last closed issue's candidate, else the input."""
    board: tuple[IssueBrief, ...]
    """The board as it was when the job started; the agents' prompts list it."""
    config: IssueQueueConfig

    @property
    def key(self) -> str:
        """The group key of the job: its start position first, so keys sort in start order."""
        return f"{self.sequence:06d}.{self.kind.value}"

    @property
    def attempt(self) -> AttemptId:
        """The identity of the attempt this job opens."""
        return attempt_id(self.kind.value, self.sequence)

    @property
    def item(self) -> ItemId:
        """The scientific item the attempt works on."""
        return item_id(self.kind.value, self.sequence)

    @property
    def workspace(self) -> WorkspacePlan:
        """Every job works in an isolated child of the revision it builds on."""
        return WorkspacePlan(mode=WorkspaceMode.ISOLATED_CHILD, base=self.base)

    def change_key(self, what: str) -> str:
        """The key of one board change: unique to this run, job and fact.

        The tracker stores it with the issue or event it writes, which is how a replayed write
        is recognized as already landed. The run identity keeps two runs on one GitHub
        repository from recognizing each other's writes.
        """
        return change_key(self.run_id, f"{self.kind.value}.{self.sequence}", what)


def change_key(run_id: str, scope: str, what: str) -> str:
    """The key of one board change within a run's scope."""
    return f"{run_id}/{scope}/{what}"


class IssueJob(_Job):
    """Work one issue: claim it, implement, record, review, and close or retry it."""

    kind: ClassVar[JobKind] = JobKind.ISSUE

    issue: IssueRow

    @property
    def budget(self) -> AttemptBudget:
        """One paid implementer turn for each attempt the issue may use."""
        limit = self.config.max_attempts_per_issue
        return AttemptBudget(admission_charge=1, paid_invocation_limit=limit)


class PerfJob(_Job):
    """One performance evaluation of the tip, and the issues it files."""

    kind: ClassVar[JobKind] = JobKind.PERF

    prior: tuple[PerfRecord, ...] = ()
    """The evaluations finished before this one."""

    @property
    def budget(self) -> AttemptBudget:
        """The evaluator's turn is not paid; the attempt still states a bound of one."""
        return AttemptBudget(admission_charge=1, paid_invocation_limit=1)


class Closing(Value):
    """What an issue job established when its work ended: what the workspace settles with."""

    closed: bool = False
    """The judge passed the candidate and the board closed the issue."""
    candidate: RevisionRef | None = None
    """The revision the implementer's last turn left, when it changed anything."""
    judge: InvocationRef | None = None
    """The judge turn the passing verdict came from."""
    failure: Failure | None = None


class JobDone(Value):
    """An issue job is over; the run folds it into its queue."""

    issue_id: int = Field(ge=1)
    closing: Closing
    kept: Kept | None = None
    """The candidate core retained, when the job closed the issue with a change."""
    refunded: bool = False
    """The workspace never opened, so the slot was not used."""

    @property
    def failure(self) -> Failure | None:
        """Why the job could not be completed, if it could not."""
        return self.closing.failure


class PerfDone(Value):
    """A performance evaluation is over; the run folds it into its queue."""

    iteration: int = Field(gt=0)
    record: PerfRecord | None = None
    """What the evaluation measured and filed; None when it did not complete."""
    failure: Failure | None = None
