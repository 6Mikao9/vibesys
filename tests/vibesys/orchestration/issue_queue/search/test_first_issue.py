"""The first issue of a run comes from the task's objective, never from a fixed phrase."""

from __future__ import annotations

import re

from hypothesis import given, settings
from hypothesis import strategies as st
from tests.vibesys.orchestration.issue_queue.search._decisions import works
from tests.vibesys.orchestration.issue_queue.search.world import (
    FACTS,
    IssueQueueWorld,
    evaluated,
    implemented,
    issue_queue_config,
    judged,
)

from vibesys.orchestration.issue_queue.search.api import (
    Finished,
    IssueQueueSearch,
    bootstrap_title,
)
from vs_core.api import Finish
from vs_dst.api import simulate

_WORDS = st.text(
    alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd"), max_codepoint=0x24F),
    min_size=1,
    max_size=12,
)
_LINE_TEXT = st.lists(_WORDS, min_size=1, max_size=12).map(" ".join)
# Markdown headings, emphasis and labels that an OBJECTIVE.md commonly wraps around its text.
_PREFIXES = st.sampled_from(
    [
        "",
        "# ",
        "## ",
        "### ",
        "**",
        "*",
        "_",
        "# **",
        "**Objective:** ",
        "Objective: ",
        "objective: ",
        "OBJECTIVE:  ",
        "## Objective: ",
        "# Goal: ",
        "Task: ",
        "*Objective:* ",
        "# *",
    ]
)
_SUFFIXES = st.sampled_from(["", "**", "*", "_", ".", "!"])
_LINES = st.one_of(
    st.just(""),
    st.just("   "),
    st.tuples(_PREFIXES, _LINE_TEXT, _SUFFIXES).map("".join),
)
OBJECTIVES = (
    st.lists(_LINES, min_size=1, max_size=6)
    .map("\n".join)
    .filter(lambda text: any(line.strip() for line in text.splitlines()))
)


def _check(title: str, objective: str) -> None:
    assert title
    assert "\n" not in title
    assert len(title) <= 80
    assert not title.startswith(("#", "*"))
    assert not re.match(r"objective\s*:", title, re.IGNORECASE)
    markup_free = re.sub(r"[#*_]", "", objective)
    assert title.removesuffix("...") in " ".join(markup_free.split())
    assert "inference server" not in title


@settings(max_examples=200, deadline=None, derandomize=True, database=None)
@given(objective=OBJECTIVES)
def test_the_title_is_plain_text_taken_from_the_objective_of_any_task(objective: str) -> None:
    _check(bootstrap_title(objective), objective)


def _first_title(objective: str) -> str:
    world = IssueQueueWorld(
        implementer=[implemented()],
        judge=[judged()],
        perf=[evaluated()],
        objective=objective,
        max_rounds=1,
    )
    assert simulate(world).finished
    return world.titles()[0]


@settings(max_examples=6, deadline=None, derandomize=True, database=None)
@given(objective=OBJECTIVES)
def test_the_first_issue_of_a_run_is_titled_from_its_objective(objective: str) -> None:
    title = _first_title(objective)
    assert title == bootstrap_title(objective)
    _check(title, objective)


def test_a_markdown_objective_is_titled_as_plain_text() -> None:
    objective = "# Objective: fast rowwise projection + softmax on one GPU\n\nWrite the kernel."
    assert _first_title(objective) == "fast rowwise projection + softmax on one GPU"


def test_a_rowwise_kernel_task_is_not_seeded_with_a_serving_issue() -> None:
    objective = "Write a PyTorch rowwise matmul and softmax kernel. Make it fast."
    assert _first_title(objective) == "Write a PyTorch rowwise matmul and softmax kernel."


@given(blank=st.text(alphabet=" \t\n\r", max_size=8))
def test_a_task_without_an_objective_fails_the_run_instead_of_seeding_an_unrelated_issue(
    blank: str,
) -> None:
    config = issue_queue_config()
    state, decisions = IssueQueueSearch(config).init(
        config, FACTS.model_copy(update={"objective": blank})
    )
    (decision,) = works(decisions)
    assert isinstance(decision, Finish)
    assert decision.result.outcome == "failure"
    assert "objective" in decision.result.reason
    assert isinstance(state.run, Finished)
