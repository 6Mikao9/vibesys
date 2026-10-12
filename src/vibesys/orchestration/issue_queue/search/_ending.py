"""How a run ends: the operator-facing result a typed ending and what was adopted come to.

The words of a result exist only here, made from the ending's typed facts. The result is the
ending's own outcome, except that a run which closed work it could not adopt did not succeed.
"""

from vibesys.candidates.api import Winner, selection_of
from vibesys.orchestration.issue_queue.search._values import (
    AllBlocked,
    BlankObjective,
    Drained,
    Ending,
    FailureKind,
    JobFailed,
    JobFailure,
    RoundBudgetSpent,
    Tip,
    TrackerUnavailable,
)
from vs_core.api.search import RunResultProposal

OPERATOR_STOP = "stopped by the operator"


def describe_failure(failure: JobFailure) -> str:
    """A job's failure in words."""
    role = "agent" if failure.role is None else failure.role.value
    match failure.kind:
        case FailureKind.STOPPED:
            return "stopped by the operator"
        case FailureKind.ENDED:
            reason = "without a reason" if failure.reason is None else failure.reason.value
            return f"{role} turn did not complete ({reason})"
        case FailureKind.CHECKPOINT_DECLINED:
            declined = "unknown" if failure.declined is None else failure.declined.value
            return f"{role} checkpoint declined ({declined})"
        case FailureKind.BOARD_REFUSED:
            return f"board write {failure.status}"
        case FailureKind.BOARD_UNKNOWN:
            return "a board request stayed unknown"


def describe(ending: Ending) -> str:
    """The reason a run result gives for the ending."""
    match ending:
        case BlankObjective():
            return (
                "the run's objective is blank, so there is no first issue to file: "
                "give the task an objective"
            )
        case RoundBudgetSpent(rounds=rounds):
            return f"the round budget of {rounds} rounds is spent"
        case AllBlocked(remaining=count):
            return f"all {count} remaining issues are blocked"
        case Drained():
            return "the board is drained and the last evaluation filed nothing"
        case TrackerUnavailable(during=during):
            return f"the issue tracker is unavailable while {during.value}"
        case JobFailed(issue_id=issue_id, iteration=iteration, failure=failure):
            subject = (
                f"issue #{issue_id}"
                if issue_id is not None
                else f"performance evaluation {iteration}"
            )
            return f"{subject} could not be completed: {describe_failure(failure)}"


def operator_stop() -> RunResultProposal:
    """The result of a run the operator stopped: nothing is adopted."""
    return RunResultProposal(outcome="cancelled", reason=OPERATOR_STOP)


def result_of(ending: Ending, tip: Tip, winner: Winner | None) -> RunResultProposal:
    """The result of a run that ended on its own, given what its closed issues left."""
    reason = describe(ending)
    outcome = "success" if isinstance(ending, Drained) else "failure"
    if outcome == "success" and tip.settlement is not None and winner is None:
        reason = f"{reason}, but the work of the closed issues was not adopted"
        outcome = "failure"
    # What was adopted is named whatever the ending, so the result says what the root holds.
    selection = None if winner is None else selection_of(winner)
    return RunResultProposal(outcome=outcome, reason=reason, selection=selection)
