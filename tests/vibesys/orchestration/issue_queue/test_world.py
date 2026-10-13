"""Whole issue-queue runs on the production shell, simulated by vs-dst: the end result only.

The flow stories (`test_stories.py`) pin each decision; these runs check that the same search,
through the real core and shell, ends with what the story says: through a rejection, a lost
board acknowledgement and a crash of the host.
"""

from __future__ import annotations

from functools import cache
from typing import TYPE_CHECKING

from hypothesis import given, settings
from hypothesis import strategies as st
from tests.vibesys.orchestration.issue_queue._replies import evaluated, implemented, judged
from tests.vibesys.orchestration.issue_queue.world import IssueQueueWorld

from vs_core.api import Finish
from vs_dst.api import SimulatedRun, simulate

if TYPE_CHECKING:
    from collections.abc import Mapping

    from vs_issue_tracker.api import FaultPhase


def finish(run: SimulatedRun) -> Finish:
    """The run's conclusion, once it ended cleanly."""
    assert run.finished
    assert not run.liveness_violations
    final = run.decisions[-1]
    assert isinstance(final, Finish)
    return final


def test_a_closed_issue_is_adopted_and_the_run_succeeds() -> None:
    world = IssueQueueWorld()
    final = finish(simulate(world))

    assert final.result.outcome == "success"
    assert final.result.selection is not None
    assert final.result.selection.kind == "retained_candidate"
    assert world.board() == {1: ("closed", 1)}


def test_a_rejected_attempt_is_retried_and_the_second_is_adopted() -> None:
    world = IssueQueueWorld(judge=[judged(passed=False), judged()], implementer=[implemented()] * 2)
    final = finish(simulate(world))

    assert final.result.outcome == "success"
    assert world.board() == {1: ("closed", 2)}


def test_issues_the_evaluator_files_are_worked_and_adopted() -> None:
    world = IssueQueueWorld(
        implementer=[implemented()] * 3,
        judge=[judged()] * 3,
        perf=[evaluated("faster", "leaner"), evaluated()],
    )
    final = finish(simulate(world))

    assert final.result.outcome == "success"
    assert set(world.titles()) >= {"faster", "leaner"}


def clean_world(*, tracker_faults: Mapping[int, FaultPhase] | None = None) -> IssueQueueWorld:
    """One issue whose first attempt the judge rejects, and the second passes.

    The script is kept this small on purpose, and the reason is a known gap in core, not a
    property of the search. After a second host death a run with more issues can stop in
    `closing` with `execute_registered_operation:blocked` open: the restart comes a lease after
    the crash, later than the operation's reconciliation bound, so its record is `BLOCKED`,
    `record_waits` does not count a blocked record as a wait, and `RESOLUTIONS` in
    `vs_core/_waits.py` has no way to end a blocked record the close is waiting for. The
    conformance sweep (`tests/composition/_loop/test_conformance_crash.py`) skips exactly that
    stall (`_lapsed_while_down`). A larger script here stalls the same way and takes minutes.
    """
    return IssueQueueWorld(
        implementer=[implemented(f"turn {n}") for n in range(2)],
        judge=[judged(passed=False), judged()],
        perf=[evaluated()],
        tracker_faults=tracker_faults,
    )


@cache
def normal() -> SimulatedRun:
    """The fault-free reference run."""
    run = simulate(clean_world())
    assert run.finished
    return run


def board_of(run: SimulatedRun) -> list[tuple[object, ...]]:
    """The issues of a finished run, with their statuses, attempts and recorded actions."""
    world = run.world
    assert isinstance(world, IssueQueueWorld)
    return world.history()


@settings(max_examples=12, deadline=None, derandomize=True, database=None)
@given(
    failing=st.dictionaries(
        st.integers(min_value=0, max_value=14), st.sampled_from(["before", "after"]), max_size=3
    )
)
def test_a_tracker_failing_around_any_write_leaves_the_board_of_a_clean_run(
    failing: dict[int, FaultPhase],
) -> None:
    run = simulate(clean_world(tracker_faults=failing))

    assert run.finished
    assert run.halted is None
    assert not run.liveness_violations
    assert run.result == normal().result
    assert board_of(run) == board_of(normal())


def recovers_the_run(recovered: SimulatedRun, label: object) -> None:
    """A run that lost a host finishes with the reference run's result and board."""
    assert recovered.finished, label
    assert recovered.halted is None, label
    assert recovered.result == normal().result, label
    assert board_of(recovered) == board_of(normal()), label
    assert recovered.no_effect_ran_twice(), label


@settings(max_examples=10, deadline=None, derandomize=True, database=None)
@given(data=st.data())
def test_a_host_dying_at_any_durable_boundary_is_replaced_by_one_that_finishes_the_run(
    data: st.DataObject,
) -> None:
    crash = data.draw(st.sampled_from(normal().crash_points()), label="crash")

    recovers_the_run(normal().rerun_with_crash(crash), crash)


@settings(max_examples=6, deadline=None, derandomize=True, database=None)
@given(data=st.data())
def test_a_second_death_during_recovery_is_recovered_too(data: st.DataObject) -> None:
    first = data.draw(st.sampled_from(normal().crash_points()), label="first")
    recovered = normal().rerun_with_crash(first)
    second = data.draw(st.sampled_from(recovered.crash_points()), label="second")

    recovers_the_run(recovered.rerun_with_crash(second), (first, second))


def test_the_work_is_not_adopted_when_the_final_candidate_fails_the_accuracy_gate() -> None:
    world = IssueQueueWorld(accurate=False)
    final = finish(simulate(world))

    assert final.result.outcome == "failure"
    assert final.result.selection is None
    assert final.result.reason.startswith("adoption-failed")
    assert world.board() == {1: ("closed", 1)}


def test_a_final_accuracy_measurement_that_never_reports_ends_the_run_without_an_adoption() -> None:
    final = finish(simulate(IssueQueueWorld(silent_official=True)))

    assert final.result.outcome == "failure"
    assert final.result.selection is None
    assert final.result.reason.startswith("adoption-failed")
