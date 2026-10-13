"""A multi run's public projection is its hypothesis ledger, read from the published record."""

from __future__ import annotations

from tests.vibesys.orchestration.test_projection import _search_state

from vibesys.hypothesis import HypothesisState
from vibesys.hypothesis.readmodel import project_hypothesis_state
from vibesys.orchestration.multi.core_policy.api import project_run_snapshot


def test_the_projection_of_the_published_record_is_the_projection_of_its_ledger() -> None:
    search = _search_state()

    assert project_run_snapshot(search, experiment_revision=3) == project_hypothesis_state(search)


def test_a_run_that_has_not_planned_yet_projects_an_empty_ledger() -> None:
    projection = project_run_snapshot(HypothesisState(), experiment_revision=0)

    assert projection.rounds == ()
