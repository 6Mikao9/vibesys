"""Pure reading of the board snapshot: which issue is next, what is left, whether it is quiet.

The stop decision of a run is a function of the board it last read, so it is the same on every
replay. Nothing here reads a tracker; the flows read the board and hand the snapshot in.
"""

import re

from vibesys.orchestration.issue_queue.search._prompts import IssueBrief, IssueDetail
from vs_issue_tracker.api import IssueRow, IssueStatus, IssueType

BOOTSTRAP_TEMPLATE = "bootstrap_description"
"""The template the board's owner renders the first issue's description from."""

_TYPE_RANK = {IssueType.BUG: 0, IssueType.FEATURE: 1, IssueType.PERF: 2}
_TITLE_LIMIT = 80
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_LABEL = re.compile(r"^(?:objective|goal|task)\s*:[\s*_]*", re.IGNORECASE)


def next_open(issues: tuple[IssueRow, ...]) -> IssueRow | None:
    """The open issue with the earliest type priority, then the earliest creation."""
    candidates = [issue for issue in issues if issue.status is IssueStatus.OPEN]
    if not candidates:
        return None
    return min(candidates, key=lambda issue: (_TYPE_RANK[issue.type], issue.id))


def remaining(issues: tuple[IssueRow, ...]) -> tuple[IssueRow, ...]:
    """Every issue that is not closed."""
    return tuple(issue for issue in issues if issue.status is not IssueStatus.CLOSED)


def blocked_queue(issues: tuple[IssueRow, ...]) -> bool:
    """Whether work remains and every remaining issue is blocked, so nothing can proceed."""
    left = remaining(issues)
    return bool(left) and all(issue.status is IssueStatus.BLOCKED for issue in left)


def has_open(issues: tuple[IssueRow, ...]) -> bool:
    """Whether any issue is open."""
    return any(issue.status is IssueStatus.OPEN for issue in issues)


def brief(issue: IssueRow) -> IssueBrief:
    """One issue as a line of the board list."""
    return IssueBrief(
        id=issue.id,
        type=issue.type.value,
        status=issue.status.value,
        title=issue.title,
        attempts=issue.attempts,
    )


def briefs(issues: tuple[IssueRow, ...]) -> tuple[IssueBrief, ...]:
    """The whole board as a list."""
    return tuple(brief(issue) for issue in issues)


def detail(issue: IssueRow) -> IssueDetail:
    """The issue under work, in full."""
    return IssueDetail(**brief(issue).model_dump(), description=issue.description)


def _plain_lines(objective: str) -> list[str]:
    """The objective's non-blank lines with heading and emphasis markers removed.

    A leading label such as "Objective:" is dropped too. A line that is only a label leaves
    nothing and is skipped, so the title comes from the first line that carries words.
    """
    plain: list[str] = []
    for raw in objective.splitlines():
        text = " ".join(_BOLD.sub(r"\1", raw).split())
        text = _LABEL.sub("", text.lstrip("#*_ "), count=1).strip("*_ ")
        if text:
            plain.append(text)
    return plain


def bootstrap_title(objective: str) -> str:
    """The first issue's title: the objective's first sentence, as plain text on one line.

    The run's first issue is the task itself, so its title comes from the objective and never
    from a fixed phrase that would name one kind of task. Markdown headings, emphasis and a
    leading "Objective:" label are removed. When nothing is left, the title is the objective's
    first non-blank line as written. A blank objective has no title.
    """
    plain = _plain_lines(objective)
    if plain:
        first = plain[0]
    else:
        lines = [" ".join(line.split()) for line in objective.splitlines()]
        first = next((line for line in lines if line), "")
    sentence = re.split(r"(?<=[.!?])\s", first, maxsplit=1)[0]
    if len(sentence) <= _TITLE_LIMIT:
        return sentence
    return sentence[: _TITLE_LIMIT - 3].rstrip() + "..."
