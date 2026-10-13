"""Whole dynamic runs on the production shell, simulated by vs-dst: the end result only.

The flow stories (`test_stories.py`, `test_workstream_stories.py`) pin each decision; these runs
check that the same search, through the real core and shell, ends with what the story says.
"""

from __future__ import annotations

from tests.support.dynamic_world import DynamicWorld
from tests.vibesys.orchestration.dynamic._replies import (
    implement,
    implemented,
    plan_reply,
    reviewed,
)

from vs_core.api import Finish
from vs_dst.api import SimulatedRun, simulate


def adopted(run: SimulatedRun) -> str | None:
    """The commit the run adopted; None when the input stood."""
    assert run.finished
    assert not run.liveness_violations
    final = run.decisions[-1]
    assert isinstance(final, Finish)
    chosen = final.result.selection
    assert chosen is not None
    return chosen.revision.git_commit if chosen.kind == "retained_candidate" else None


def test_a_candidate_that_beats_the_input_is_adopted_with_the_run_a_success() -> None:
    run = simulate(DynamicWorld(throughputs=(120.0,)))

    assert adopted(run) is not None
    final = run.decisions[-1]
    assert isinstance(final, Finish)
    assert final.result.outcome == "success"


def test_the_fastest_of_two_candidates_is_the_one_adopted() -> None:
    world = DynamicWorld(
        planner=[plan_reply(implement("h1"), implement("h2"))],
        implementer=[implemented(), implemented()],
        judge=[reviewed(), reviewed()],
        throughputs=(120.0, 150.0),
        max_in_flight=2,
    )
    commit = adopted(simulate(world))

    assert commit is not None
    assert world.headline(commit) == 150.0


def test_a_candidate_the_judge_rejects_leaves_the_input_standing() -> None:
    run = simulate(DynamicWorld(judge=[reviewed(passed=False)] * 2, throughputs=(500.0,)))

    assert adopted(run) is None
