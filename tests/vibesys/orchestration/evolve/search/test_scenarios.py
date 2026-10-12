"""Whole evolve runs on the production shell: cold start, generations, measure and adopt."""

from __future__ import annotations

from tests.vibesys.orchestration.evolve.search._decisions import works
from tests.vibesys.orchestration.evolve.search.world import EvolveWorld

from vs_core.api import Finish, ProposeWinner, StartAttempt
from vs_dst.api import simulate


def test_a_run_adopts_its_best_individual() -> None:
    run = simulate(EvolveWorld(max_generations=2, children_per_generation=2))

    assert run.finished
    assert not run.liveness_violations
    # one cold start and two generations of two children
    assert sum(isinstance(item, StartAttempt) for item in works(run.decisions)) == 5
    final = works(run.decisions)[-1]
    assert isinstance(final, Finish)
    assert final.result.outcome == "success"
    proposal = next(item for item in works(run.decisions) if isinstance(item, ProposeWinner))
    assert proposal.selection.kind == "retained_candidate"
