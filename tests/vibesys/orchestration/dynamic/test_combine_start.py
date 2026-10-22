"""A combine applies exactly the changes main does not hold, however main moved.

Main moves by fast-forward whenever a winner was built on it, so a part may have been built on
an older main, on another candidate, or on the main the combine starts from. For every such
lineage the start the implementer is given for a part must make ``start..part`` the part's
changes that main lacks: none that main holds (applied twice) and none it lacks (dropped).
These stories play random lineages under a fake host, live and with restarts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from hypothesis import example, given, settings
from hypothesis import strategies as st
from tests.vibesys.orchestration.dynamic._host import PLANNER_LABEL, Host, begin, config
from tests.vibesys.orchestration.dynamic._replies import combine, implement, plan_reply
from tests.vibesys.orchestration.dynamic._slots import (
    implement_and_judge,
    planner_context,
    turn_about,
    verify_parents,
)
from tests.vibesys.orchestration.dynamic.core_policy._contexts import render
from tests.vibesys.vibesys_flows import across_restarts

from vibesys.dynamic_handoff import render_handoff
from vibesys.orchestration.dynamic.search.api import ImplementPrompt, PlannerPrompt, PromptTemplate
from vs_core.api import Measure, Operation, StartAttempt, Withdraw
from vs_sim.api.testing import examples

MODES = across_restarts
STEP = 20.0
"""How much a winner beats the main before it: the input measures 50 (well beyond noise)."""
LOSER = 40.0


@dataclass(frozen=True, kw_only=True)
class Built:
    """One candidate of the lineage, and whether it is a winner: it is built on candidate ``parent``
    (None: main), or, with ``parts``, is a combine of earlier candidates built on main."""

    parent: int | None
    wins: bool
    parts: tuple[int, ...] = ()


def _stories() -> st.SearchStrategy[tuple[list[Built], list[int]]]:
    """A lineage of 2..5 candidates, each built on main or an earlier candidate (or a combine of
    earlier ones) and a winner or not, and 2..3 of them to combine."""

    @st.composite
    def story(draw: st.DrawFn) -> tuple[list[Built], list[int]]:
        size = draw(st.integers(2, 5))
        lineage = []
        for index in range(size):
            earlier = st.integers(0, index - 1)
            combined = (
                st.lists(earlier, min_size=2, max_size=3, unique=True).map(tuple)
                if index >= 2
                else st.just(())
            )
            parts = draw(combined)
            parent = None if parts or index == 0 else draw(st.none() | earlier)
            lineage.append(Built(parent=parent, wins=draw(st.booleans()), parts=parts))
        parts = draw(st.lists(st.sampled_from(range(size)), min_size=2, max_size=3, unique=True))
        return lineage, parts

    return story()


def _settle(host: Host) -> None:
    for _ in range(10):
        pending = [*host.open(Measure), *host.open(Operation), *host.open(Withdraw)]
        if not pending:
            return
        host.answer(pending[0])


def _start(host: Host) -> None:
    for _ in range(3):
        if host.open(StartAttempt):
            break
        verify_parents(host)
    for asked in host.open(StartAttempt):
        host.answer(asked)


def _plan(host: Host, *entries: dict[str, object]) -> PlannerPrompt:
    host.drain_until_turn(PLANNER_LABEL)
    context = planner_context(host)
    assert isinstance(context, PlannerPrompt)
    host.run.reply_raw(host.run.expect_turn(role=PLANNER_LABEL), plan_reply(*entries))
    return context


def _holds(lineage: list[Built]) -> tuple[list[frozenset[int]], list[int | None]]:
    """What each candidate's tree holds (its own change and everything it was built on), and main
    before each candidate: a winner built on main now becomes main."""
    held: list[frozenset[int]] = []
    mains: list[int | None] = []
    main: int | None = None
    for index, item in enumerate(lineage):
        mains.append(main)
        parent = main if item.parent is None else item.parent
        below = frozenset() if parent is None else held[parent]
        held.append(frozenset({index}) | below.union(*(held[part] for part in item.parts)))
        if item.wins and parent == main:
            main = index
    mains.append(main)
    return held, mains


def _play(lineage: list[Built], *, restarting: bool) -> Host:
    throughputs = {}
    level = 50.0
    for index, item in enumerate(lineage):
        level += STEP if item.wins else 0.0
        throughputs[f"c{index}"] = level if item.wins else LOSER
    host = begin(
        config(max_rounds=len(lineage) + 3), restarting=restarting, throughputs=throughputs
    )
    for index, item in enumerate(lineage):
        name = f"h{index}"
        on = (
            {}
            if item.parent is None
            else {
                "parent_hypothesis_id": f"h{item.parent}",
                "parent_revision": f"c{item.parent}",
            }
        )
        if item.parts:
            _plan(host, combine(name, *((f"h{i}", f"c{i}") for i in item.parts)))
        else:
            _plan(host, implement(name, **on))
        _start(host)
        implement_and_judge(host, name, f"c{index}")
        _settle(host)
    return host


def _combined(
    lineage: list[Built], parts: list[int], *, restarting: bool
) -> tuple[ImplementPrompt, PlannerPrompt]:
    """What the implementer of a combine of ``parts`` was asked, and what the planner was offered."""
    host = _play(lineage, restarting=restarting)
    _plan(host, combine("k", *((f"h{i}", f"c{i}") for i in parts)))  # answers the turn asked before
    _start(host)
    asked = turn_about(host, "implementer", "k").context
    assert isinstance(asked, ImplementPrompt)
    host.drain_until_turn(PLANNER_LABEL)
    offered = planner_context(host)
    assert isinstance(offered, PlannerPrompt)
    return asked, offered


@MODES
@settings(max_examples=examples(pr=15, nightly=150), deadline=None, derandomize=True, database=None)
@given(story=_stories())
# main moves to c0 by fast-forward, then c1 (built on the input) and c2 (built on c1) are combined
@example(
    story=(
        [Built(parent=None, wins=True), Built(parent=0, wins=False), Built(parent=1, wins=False)],
        [1, 2],
    )
)
# a combine of two losers wins and moves main: its parts are in main, so combining them again
# applies nothing, and a merge of either brings nothing
@example(
    story=(
        [
            Built(parent=None, wins=False),
            Built(parent=None, wins=False),
            Built(parent=None, wins=True, parts=(0, 1)),
        ],
        [0, 1],
    )
)
def test_a_combine_applies_each_part_exactly_the_changes_main_lacks(
    *, restarting: bool, story: tuple[list[Built], list[int]]
) -> None:
    lineage, parts = story
    held, mains = _holds(lineage)
    asked, offered = _combined(lineage, parts, restarting=restarting)
    main = frozenset() if mains[-1] is None else held[mains[-1]]
    revisions = {f"c{i}": held[i] for i in range(len(lineage))}
    for part in asked.combine:
        start = revisions.get(part.built_on.revision_id.root, frozenset())
        changes = held[int(part.hypothesis_id[1:])]
        assert start <= changes, f"{part.hypothesis_id}: a start the part does not hold"
        assert changes - start == changes - main, (part.hypothesis_id, lineage, parts)
    # a candidate main holds brings nothing, so a merge is not offered it
    assert all(held[int(row.hypothesis_id[1:])] - main for row in offered.mergeable), lineage
    # a move of main without a merge reads as what it is, to the planner and in the handoff file
    history = render(PromptTemplate.PORTFOLIO, offered)
    assert not re.search(r"merge `(h\d+)` #\d+ of `\1`", history), history
    assert ("fast-forward #" in history) == any(item.fast_forward for item in offered.merges)


@MODES
def test_a_candidate_that_moved_main_without_a_merge_is_told_as_a_fast_forward(
    *, restarting: bool
) -> None:
    host = _play([Built(parent=None, wins=True)], restarting=restarting)
    _plan(host, implement("k"))  # answers the turn asked before
    _start(host)
    host.drain_until_turn(PLANNER_LABEL)
    history = render(PromptTemplate.PORTFOLIO, planner_context(host))
    assert "fast-forward #1 of `h0` from `base`" in history, history
    assert "merge `h0`" not in history, history
    written = max(host.handoffs.values(), key=lambda item: item.call)
    handoff = render_handoff(written)
    assert "fast-forward of `h0`" in handoff, handoff
    assert "merge `h0`" not in handoff, handoff
