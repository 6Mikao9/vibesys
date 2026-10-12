"""Properties of the search over every state, event and view a whole run reaches.

A run is driven once on the production shell with a search that records the (state, event, view)
triple of every `update`. `update` is then a pure function of that triple (the same triple
always gives the same state and decisions, whatever else ran), every reached state survives the
persisted form, a stop starts no new work from any state, and no event the run delivered is
answered with a failure of the search's own.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

import pytest
from tests.vibesys.orchestration.issue_queue.search.world import (
    IssueQueueWorld,
    evaluated,
    implemented,
    issue_queue_config,
    issue_queue_limits,
    judged,
)

from vibesys.orchestration.issue_queue.core_policy.api import build_search
from vibesys.orchestration.issue_queue.search.api import RenderRoleArtifacts
from vs_core.api import (
    ControlId,
    ControlInput,
    Operation,
    RequestTurn,
    StartAttempt,
    StopRequested,
)
from vs_dst.api import simulate
from vs_runtime.api.testing import Launch

if TYPE_CHECKING:
    from vibesys.orchestration.issue_queue.search.api import IssueQueueConfig
    from vs_core.api import Decision, Event, RunFacts, RunView, Search, SearchDeclaration
    from vs_core.api import StrategyState as State


@dataclass
class Capture:
    """The (state, event, view) triple of every `update` of one run."""

    triples: list[tuple[Any, Event, RunView]] = field(default_factory=list)


class Capturing:
    """The search of a run, recording what it was asked."""

    def __init__(self, inner: Search[Any, Any], capture: Capture) -> None:
        """Wrap ``inner``; every `update` is recorded in ``capture``."""
        self._inner = inner
        self._capture = capture

    @property
    def declaration(self) -> SearchDeclaration:
        """What the wrapped search declares."""
        return self._inner.declaration

    @property
    def state_model(self) -> type[Any]:
        """The class of the wrapped search's state."""
        return self._inner.state_model

    def init(self, config: IssueQueueConfig, facts: RunFacts) -> tuple[State, list[Decision]]:
        """The wrapped search's first state and decisions."""
        return self._inner.init(config, facts)

    def update(self, state: State, event: Event, view: RunView) -> tuple[State, list[Decision]]:
        """Record the call, then answer as the wrapped search does."""
        self._capture.triples.append((state, event, view))
        return self._inner.update(state, event, view)


def _captured() -> tuple[Search[Any, Any], Capture]:
    world = IssueQueueWorld(
        implementer=[implemented(f"turn {n}") for n in range(4)],
        judge=[judged(passed=False), judged(bugs=("a bug",)), judged()],
        perf=[evaluated("more"), evaluated()],
    )
    capture = Capture()
    config = world.config
    world.setup = replace(
        world.setup,
        new_launch=lambda: Launch(
            search=Capturing(build_search(config), capture),
            config=config,
        ),
    )
    run = simulate(world)
    assert run.finished
    assert run.halted is None
    assert not run.liveness_violations
    return build_search(config), capture


SEARCH, CAPTURE = _captured()


def test_a_run_reaches_many_distinct_states() -> None:
    assert len({state.model_dump_json() for state, _, _ in CAPTURE.triples}) > 20


def test_update_is_a_pure_function_of_the_state_the_event_and_the_view() -> None:
    for state, event, view in CAPTURE.triples:
        assert SEARCH.update(state, event, view) == SEARCH.update(state, event, view)


def test_every_reached_state_round_trips_its_persisted_form() -> None:
    model = SEARCH.state_model
    for state, _, _ in CAPTURE.triples:
        assert model.model_validate_json(state.model_dump_json()) == state


@pytest.mark.parametrize("field_name", ["max_rounds", "max_attempts_per_issue"])
def test_the_derived_limits_cover_the_attempts_the_config_allows(field_name: str) -> None:
    config = issue_queue_config(**{field_name: 3})
    assert issue_queue_limits(config).max_attempts == config.max_attempts


def test_after_an_operator_stop_the_search_starts_no_new_work() -> None:
    """From every state a run reaches, a stop leaves only settling, adopting and finishing."""
    stop = StopRequested(control=ControlInput(control_id=ControlId(root="stop"), action="stop"))
    for state, _, view in CAPTURE.triples:
        _, decisions = SEARCH.update(state, stop, view)
        for item in decisions:
            work = getattr(item, "request", item)
            assert not isinstance(work, StartAttempt | RequestTurn), work
            assert not (
                isinstance(work, Operation) and isinstance(work.request, RenderRoleArtifacts)
            ), work
