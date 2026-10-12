"""Properties of how the search reads and writes the board (design properties Q2 to Q5).

The search decides from the last board snapshot it read, files only what its reply schemas
allow, stops by the legacy decision table, and works at most the round cap of issues between two
performance evaluations.
"""

from __future__ import annotations

import json
from itertools import groupby

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError
from tests.vibesys.orchestration.issue_queue.search._decisions import works
from tests.vibesys.orchestration.issue_queue.search.world import (
    FACTS,
    IssueQueueWorld,
    evaluated,
    implemented,
    judged,
)

from vibesys.orchestration.issue_queue.models import (
    JudgeReplyModel,
    perf_result_model,
)
from vibesys.orchestration.issue_queue.search.api import bootstrap_title
from vs_core.api import Finish, Operation, StartAttempt
from vs_dst.api import SimulatedRun, simulate
from vs_issue_tracker.api import IssueStatus, IssueType, ReadBoard

_KINDS = ["bug", "feature", "perf"]


# Q2: no reply can file more issues than its cap or an issue of a type the role may not file.


def _filed(kinds: list[str]) -> list[dict[str, str]]:
    return [{"type": kind, "title": f"t{n}", "description": "d"} for n, kind in enumerate(kinds)]


@given(kinds=st.lists(st.sampled_from(_KINDS), max_size=4))
def test_a_judge_reply_files_at_most_one_bug_and_nothing_else(kinds: list[str]) -> None:
    payload = json.dumps(
        {"analysis": "a", "feedback": "", "verdict": "pass", "new_issues": _filed(kinds)}
    )
    allowed = len(kinds) <= 1 and all(kind == "bug" for kind in kinds)
    if allowed:
        JudgeReplyModel.model_validate_json(payload)
    else:
        with pytest.raises(ValidationError):
            JudgeReplyModel.model_validate_json(payload)


@given(cap=st.integers(1, 4), kinds=st.lists(st.sampled_from([*_KINDS, "other"]), max_size=6))
def test_an_evaluator_reply_files_at_most_its_cap_of_issues_of_known_types(
    cap: int, kinds: list[str]
) -> None:
    reply = json.loads(evaluated())
    reply["new_issues"] = _filed(kinds)
    allowed = len(kinds) <= cap and all(kind in _KINDS for kind in kinds)
    if allowed:
        perf_result_model(cap).model_validate_json(json.dumps(reply))
    else:
        with pytest.raises(ValidationError):
            perf_result_model(cap).model_validate_json(json.dumps(reply))


# Q3: a decision that depends on the board follows a read of it.


def _ran(world: IssueQueueWorld) -> SimulatedRun:
    run = simulate(world)
    assert run.finished
    assert run.halted is None
    assert not run.liveness_violations
    return run


def _preload(world: IssueQueueWorld, statuses: list[IssueStatus]) -> None:
    """Put issues in the given statuses on the world's board before the run starts."""
    for number, status in enumerate(statuses):
        issue = world.tracker.create(
            type=IssueType.FEATURE,
            title=f"issue {number}",
            description="d",
            created_by="human",
            iteration=0,
        )
        if status is not IssueStatus.OPEN:
            world.tracker.update_status(issue.id, status, actor="human", iteration=0)


@settings(max_examples=6, deadline=None, derandomize=True, database=None)
@given(
    judged_bugs=st.lists(st.booleans(), min_size=1, max_size=3),
    failing=st.lists(st.booleans(), min_size=1, max_size=3),
)
def test_every_attempt_starts_from_a_board_read_after_the_previous_attempt(
    judged_bugs: list[bool], failing: list[bool]
) -> None:
    world = IssueQueueWorld(
        implementer=[implemented(f"turn {n}") for n in range(24)],
        judge=[
            judged(
                passed=not failing[n % len(failing)],
                bugs=("a bug",) if judged_bugs[n % len(judged_bugs)] and n == 0 else (),
            )
            for n in range(24)
        ],
        perf=[evaluated() for _ in range(4)],
    )
    run = _ran(world)
    read_since_start = False
    starts = 0
    for item in works(run.decisions):
        if isinstance(item, StartAttempt):
            assert read_since_start or starts == 0, item.decision_id
            read_since_start = False
            starts += 1
        elif isinstance(item, Operation) and isinstance(item.request, ReadBoard):
            read_since_start = True
    assert starts


# Q4: the stop decision of a run is the legacy decision table, on any board.

STATUSES = st.lists(
    st.sampled_from([IssueStatus.CLOSED, IssueStatus.BLOCKED]), min_size=1, max_size=4
)


@settings(max_examples=12, deadline=None, derandomize=True, database=None)
@given(statuses=STATUSES)
def test_a_queue_left_only_blocked_or_closed_ends_by_the_decision_table(
    statuses: list[IssueStatus],
) -> None:
    world = IssueQueueWorld(perf=[evaluated()], implementer=[implemented()], judge=[judged()])
    _preload(world, statuses)
    run = _ran(world)
    final = works(run.decisions)[-1]
    assert isinstance(final, Finish)
    evaluations = [
        item
        for item in works(run.decisions)
        if isinstance(item, StartAttempt) and item.attempt_id.root.startswith("attempt:perf")
    ]
    if IssueStatus.BLOCKED in statuses:
        assert final.result.outcome == "failure"
        assert "blocked" in final.result.reason
        assert evaluations == []
    else:
        assert final.result.outcome == "success"
        assert len(evaluations) == 1


# Q5: at most the round cap of issues are worked between two evaluations.


@settings(max_examples=10, deadline=None, derandomize=True, database=None)
@given(cap=st.integers(1, 3), humans=st.integers(0, 5))
def test_no_round_works_more_issues_than_the_cap(cap: int, humans: int) -> None:
    world = IssueQueueWorld(
        implementer=[implemented() for _ in range(40)],
        judge=[judged() for _ in range(40)],
        perf=[evaluated() for _ in range(8)],
        max_issues_per_round=cap,
        max_rounds=3,
    )
    _preload(world, [IssueStatus.OPEN] * humans)
    run = _ran(world)
    kinds = [
        "perf" if item.attempt_id.root.startswith("attempt:perf") else "issue"
        for item in works(run.decisions)
        if isinstance(item, StartAttempt)
    ]
    for kind, group in groupby(kinds):
        if kind == "issue":
            assert len(list(group)) <= cap
    if humans == 0:
        assert bootstrap_title(FACTS.objective) in world.titles()
