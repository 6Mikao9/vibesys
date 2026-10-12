"""Whole single runs on the production shell: baseline, design, implement, measure, adopt."""

from __future__ import annotations

from tests.vibesys.orchestration.single.search._decisions import works
from tests.vibesys.orchestration.single.search.world import SingleWorld

from vs_core.api import Finish, ProposeWinner, RequestTurn, StartAttempt
from vs_dst.api import simulate


def test_two_rounds_run_to_adoption() -> None:
    run = simulate(SingleWorld())

    assert run.finished
    assert not run.liveness_violations
    assert sum(isinstance(item, StartAttempt) for item in works(run.decisions)) == 2
    final = works(run.decisions)[-1]
    assert isinstance(final, Finish)
    assert final.result.outcome == "success"
    proposal = next(item for item in works(run.decisions) if isinstance(item, ProposeWinner))
    assert proposal.selection.kind == "retained_candidate"
    assert any(isinstance(item, RequestTurn) for item in works(run.decisions))
