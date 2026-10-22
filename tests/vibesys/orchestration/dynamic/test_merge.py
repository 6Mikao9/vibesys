"""The run's main revision: new work starts from it, and only a merge that merged moves it.

Main starts at the run's input. A candidate built on main that beats it moves main at its
settlement. A candidate built on an older main needs a merge workstream: a new merger session
merges candidates on a checkout of main; its commit is measured with the trusted evaluations, and
main moves to it when it passes accuracy and measures no worse than main. A refusal, a failed
turn, a commit that fails accuracy or one that regresses leaves main where it was. These tests
play the search under a fake host, live and with restarts: generated stories in which the planner
merges whatever is mergeable, and named stories for each outcome.
"""

from __future__ import annotations

from typing import Unpack

from hypothesis import event, given, note, settings
from hypothesis import strategies as st
from tests.vibesys.orchestration.dynamic._host import (
    PLANNER_LABEL,
    Host,
    World,
    begin,
    config,
)
from tests.vibesys.orchestration.dynamic._main_watch import MainWatch
from tests.vibesys.orchestration.dynamic._replies import implement, merge, merged, plan_reply
from tests.vibesys.orchestration.dynamic._slots import (
    implement_and_judge,
    open_next_slot,
    planner_context,
    turn_about,
    verify_parents,
)
from tests.vibesys.orchestration.dynamic.core_policy._contexts import render
from tests.vibesys.search_faults import Step, play, steps
from tests.vibesys.vibesys_flows import across_restarts

from vibesys.orchestration.dynamic.models import MergeResult
from vibesys.orchestration.dynamic.search.api import (
    FailureKind,
    ImplementPrompt,
    MergeOutcome,
    MergePrompt,
    PlannerPrompt,
    PromptTemplate,
)
from vs_core.api import (
    FailureReason,
    Measure,
    Operation,
    RequestTurn,
    Retained,
    RevisionRef,
    StartAttempt,
    Withdraw,
)
from vs_sim.api.testing import examples

MODES = across_restarts


@MODES
@settings(max_examples=examples(pr=15, nightly=100), deadline=None, derandomize=True, database=None)
@given(
    story=steps(),
    rounds=st.integers(2, 4),
    in_flight=st.integers(1, 2),
    world=st.fixed_dictionaries(
        {
            # The planner merges what is mergeable; how each commit measures, and how the merger
            # answers its turns, in order.
            "merging": st.sampled_from([True]),
            "throughputs": st.dictionaries(
                st.sampled_from([f"c{n}" for n in range(1, 12)]), st.floats(10, 200), max_size=11
            ),
            "merger_answers": st.lists(st.integers(0, 3), max_size=4),
            # Where each merge starts among the buildable hypotheses: it may name one that is
            # not mergeable, which validation must refuse.
            "merge_picks": st.lists(st.integers(0, 5), max_size=4),
        }
    ),
)
def test_main_moves_only_through_a_merge_that_merged_and_new_work_starts_from_it(
    *,
    restarting: bool,
    story: list[Step],
    rounds: int,
    in_flight: int,
    world: World,
) -> None:
    host = begin(
        config(max_rounds=rounds, max_in_flight=in_flight),
        restarting=restarting,
        **world,
    )
    watch = MainWatch(throughputs=dict(world.get("throughputs", {})))
    try:
        play(host, story, check=lambda: watch.check(host.record))
    finally:
        note(host.run.timeline())
    watch.check(host.record)
    record = host.record
    outcomes = sorted({item.outcome.value for item in record.merges}) if record else []
    event(f"merge outcomes: {outcomes}")
    event(f"asked for a merge of an unlisted candidate: {host.unlisted_merges > 0}")
    # Each merge ran in its own new session: no session served two merges.
    sessions = [session for found in host.merger_sessions.values() for session in found]
    assert len(sessions) == len(set(sessions)), host.merger_sessions
    assert all(len(found) == 1 for found in host.merger_sessions.values()), host.merger_sessions


# -- named stories -----------------------------------------------------------------------------


def _start(host: Host) -> None:
    """Verify the parent, when it is not the input, then admit the one workstream waiting."""
    for _ in range(3):
        if host.open(StartAttempt):
            break
        verify_parents(host)
    assert host.open(StartAttempt), (host.run.timeline(), [a.label for a in host.run.open])
    open_next_slot(host)


def _implemented(host: Host, identifier: str, candidate: str, outcome: str = "supported") -> None:
    """Play one implement workstream through its measurement and settlement."""
    _start(host)
    implement_and_judge(host, identifier, candidate, outcome=outcome)
    _settle(host)


def _settle(host: Host) -> None:
    """Answer the measurement, the reading of its evidence and the close, as the world would."""
    for _ in range(10):
        pending = [*host.open(Measure), *host.open(Operation), *host.open(Withdraw)]
        if not pending:
            return
        host.answer(pending[0])


def _merger(host: Host) -> RequestTurn:
    (asked,) = host.open(RequestTurn, label="merger")
    assert isinstance(asked.request, RequestTurn)
    return asked.request


def _merge(host: Host, reply: str, commit: str | None) -> MergePrompt:
    """Admit the merge workstream and have its merger answer ``reply``, committing ``commit``."""
    _start(host)
    (asked,) = host.open(RequestTurn, label="merger")
    context = asked.context
    assert isinstance(context, MergePrompt)
    checkpoint = None if commit is None else Retained(revision=RevisionRef.of_git_commit(commit))
    host.run.reply(asked, MergeResult.model_validate_json(reply), checkpoint=checkpoint)
    _settle(host)
    _ask_again(host)
    return context


def _plan(host: Host, *entries: dict[str, object]) -> PlannerPrompt:
    host.drain_until_turn(PLANNER_LABEL)
    context = planner_context(host)
    assert isinstance(context, PlannerPrompt)
    host.run.reply_raw(host.run.expect_turn(role=PLANNER_LABEL), plan_reply(*entries))
    return context


def _ask_again(host: Host) -> None:
    """The planner's turn, asked while a workstream was still running, times out and is asked again.

    With two agent slots the planner is asked as soon as one is free, so its prompt predates the
    settlement of the workstream still running. Its fresh turn shows everything that settled.
    """
    host.run.fail(host.run.expect_turn(role=PLANNER_LABEL), FailureReason.TIMED_OUT)


def _first_candidate(*, restarting: bool, **world: Unpack[World]) -> Host:
    """A run whose first workstream made candidate ``c1`` of ``h1``, beating the input.

    ``c1`` was built on main, so it moved main at its settlement; no merge is needed or offered.
    """
    host = begin(config(max_rounds=5), restarting=restarting, **world)
    first = _plan(host, implement("h1"))
    assert first.main_revision == first.base_revision
    assert first.mergeable == ()
    _implemented(host, "h1", "c1")
    return host


def _stale_candidate(*, restarting: bool, **world: Unpack[World]) -> Host:
    """A run in which ``h1``'s candidate ``c2`` beats main but was built on an older main.

    Two workstreams start from the input together: ``h2`` settles first with ``c1``, which moves
    main at once, and ``h1`` settles after with ``c2``, built on the input and beating ``c1``, so
    only a merge can bring it into main. Throughputs the world sets win over the defaults here.
    """
    throughputs = {"c1": 100.0, "c2": 150.0, **world.pop("throughputs", {})}
    host = begin(
        config(max_rounds=5, max_in_flight=2),
        restarting=restarting,
        throughputs=throughputs,
        **world,
    )
    first = _plan(host, implement("h1"), implement("h2"))
    assert first.main_revision == first.base_revision
    for asked in host.open(StartAttempt):
        host.answer(asked)
    implement_and_judge(host, "h2", "c1")
    _settle(host)
    implement_and_judge(host, "h1", "c2")
    _settle(host)
    _ask_again(host)
    return host


@MODES
def test_a_merge_that_merged_moves_main_and_the_next_workstream_starts_from_it(
    *, restarting: bool
) -> None:
    host = _stale_candidate(restarting=restarting, throughputs={"c3": 200.0})
    context = _plan(host, merge("m1", "h1"))
    assert [row.hypothesis_id for row in context.mergeable] == ["h1"]
    assert context.main_revision == RevisionRef.of_git_commit("c1")  # h2 moved it
    prompt = _merge(host, merged(), "c3")
    assert [source.hypothesis_id for source in prompt.candidates] == ["h1"]
    assert prompt.main_revision == context.main_revision  # merged on a checkout of main
    after = _plan(host, implement("h3"))
    main = RevisionRef.of_git_commit("c3")
    assert after.main_revision == main
    assert after.main_measurement is not None
    assert after.main_measurement.merge_id == "m1"
    assert [row.value for row in after.main_measurement.previous] == [100.0]  # c1's, main then
    assert after.mergeable == ()  # h1 is in main now, and nothing else beats it
    moved, record = after.merges
    assert moved.merge_id == "h2"  # the settlement of h2 moved main to c1
    assert record.merge_id == "m1"
    assert record.outcome is MergeOutcome.MERGED
    _start(host)
    started = turn_about(host, "implementer", "h3").context
    assert isinstance(started, ImplementPrompt)
    assert started.parent_revision == main


@MODES
def test_a_refused_merge_leaves_main_unchanged_and_the_planner_hears_why(
    *, restarting: bool
) -> None:
    host = _stale_candidate(restarting=restarting)
    before = _plan(host, merge("m1", "h1"))
    _merge(host, merged("refused", "h1 rewrites the kernel the instructions keep"), None)
    after = _plan(host, implement("h3"))
    main = RevisionRef.of_git_commit("c1")  # h2's settlement moved it
    assert after.main_revision == before.main_revision == main != before.base_revision
    _, record = after.merges
    assert record.outcome is MergeOutcome.REFUSED
    assert record.reason == "h1 rewrites the kernel the instructions keep"
    text = render(PromptTemplate.PORTFOLIO, after)
    assert "h1 rewrites the kernel the instructions keep" in text
    assert [row.hypothesis_id for row in after.mergeable] == ["h1"]  # still mergeable


@MODES
def test_a_merge_whose_turn_failed_leaves_main_unchanged(*, restarting: bool) -> None:
    host = _stale_candidate(restarting=restarting)
    before = _plan(host, merge("m1", "h1"))
    _start(host)
    for _ in range(3):  # the turn, then core's asks again after a lost turn
        merger = host.open(RequestTurn, label="merger")
        if not merger:
            break
        host.run.lose(merger[0])
        if host.run.sleeping_until:
            host.run.advance_to(min(host.run.sleeping_until))
    _settle(host)
    _ask_again(host)
    after = _plan(host, implement("h3"))
    assert after.main_revision == before.main_revision == RevisionRef.of_git_commit("c1")
    _, record = after.merges
    assert record.outcome is MergeOutcome.FAILED
    assert record.failure is not None


@MODES
def test_a_merged_commit_that_measures_worse_leaves_main_and_is_reported_as_a_regression(
    *, restarting: bool
) -> None:
    host = _stale_candidate(
        restarting=restarting, throughputs={"c1": 100.0, "c2": 300.0, "c3": 20.0}
    )
    before = _plan(host, merge("m1", "h1"))
    _merge(host, merged(), "c3")
    after = _plan(host, implement("h3"))
    main = RevisionRef.of_git_commit("c1")  # h2's settlement moved it
    assert after.main_revision == before.main_revision == main != before.base_revision
    assert after.main_measurement is not None
    assert after.main_measurement.revision == main  # the regressed merge did not replace it
    _, record = after.merges
    assert record.outcome is MergeOutcome.FAILED
    assert record.failure is not None
    assert record.failure.kind is FailureKind.MERGE_REGRESSED
    assert record.accuracy_passed
    assert [row.value for row in record.metrics] == [20.0]
    assert [row.value for row in record.main_metrics] == [100.0]  # c1's, main then
    assert [row.hypothesis_id for row in after.mergeable] == ["h1"]  # not merged: still offered
    text = " ".join(render(PromptTemplate.PORTFOLIO, after).split())
    assert "Main regressed with that merge, so main did not move" in text
    _start(host)
    started = turn_about(host, "implementer", "h3").context
    assert isinstance(started, ImplementPrompt)
    assert started.parent_revision == main


@MODES
def test_a_merged_commit_that_fails_a_benchmark_check_main_passed_leaves_main(
    *, restarting: bool
) -> None:
    host = _stale_candidate(restarting=restarting, failing_benchmarks={"c3"})
    _plan(host, merge("m1", "h1"))
    _merge(host, merged(), "c3")
    after = _plan(host, implement("h3"))
    assert after.main_revision == RevisionRef.of_git_commit("c1") != after.base_revision
    _, record = after.merges
    assert record.failure is not None
    assert record.failure.kind is FailureKind.MERGE_REGRESSED
    assert record.benchmark_passed is not True
    assert record.main_benchmark_passed is True


@MODES
def test_a_hypothesis_that_goes_on_after_main_moved_is_asked_to_rebase_onto_main(
    *, restarting: bool
) -> None:
    host = begin(
        config(max_rounds=5, max_in_flight=2), restarting=restarting, throughputs={"c1": 100.0}
    )
    first = _plan(host, implement("h1"), implement("h2"))  # both start on the input
    for asked in host.open(StartAttempt):
        host.answer(asked)
    implement_and_judge(host, "h1", "c1")
    _settle(host)  # c1 beats the input and built on it: main moves
    implement_and_judge(host, "h2", "c2", outcome="inconclusive")  # it may go on
    _settle(host)
    _ask_again(host)
    context = _plan(host, implement("h2", continue_hypothesis=True))
    assert context.main_revision == RevisionRef.of_git_commit("c1")
    assert first.main_revision == first.base_revision == context.base_revision
    _start(host)
    resumed = turn_about(host, "implementer", "h2").context
    assert isinstance(resumed, ImplementPrompt)
    assert resumed.parent_revision == RevisionRef.of_git_commit("c2")  # its own work
    assert resumed.rebase_onto == context.main_revision
    text = " ".join(render(PromptTemplate.IMPLEMENT, resumed).split())
    assert "bring main's change into your tree" in text
    # h2's tree holds the input, main's revision when h2 started: main's change since is h1's.
    assert f"git diff {context.base_revision.revision_id.root} c1" in text
    assert "merge-base" not in text


@MODES
def test_a_hypothesis_started_on_main_is_not_asked_to_rebase(*, restarting: bool) -> None:
    host = _first_candidate(restarting=restarting)  # c1 moved main
    _plan(host, implement("h2"))
    _implemented(host, "h2", "c2", "inconclusive")  # it may go on
    _plan(host, implement("h2", continue_hypothesis=True))
    _start(host)
    resumed = turn_about(host, "implementer", "h2").context
    assert isinstance(resumed, ImplementPrompt)
    assert resumed.rebase_onto is None
