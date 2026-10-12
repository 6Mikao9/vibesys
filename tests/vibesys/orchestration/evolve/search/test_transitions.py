"""The search's first moves and its stop, driven directly through `init` and `update`."""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st
from tests.vibesys.orchestration.evolve.search._decisions import works
from tests.vibesys.orchestration.evolve.search.world import FACTS, evolve_config

from vibesys.orchestration.evolve.search.api import ColdStart, EvolveSearch, bootstrap_key
from vs_core.api import (
    ControlId,
    ControlInput,
    StartAttempt,
    StopRequested,
    run_view,
)
from vs_core.testing.builders import initial_state


@settings(max_examples=10, deadline=None, derandomize=True, database=None)
@given(parallelism=st.integers(1, 4), children=st.integers(1, 4))
def test_a_run_begins_with_one_cold_start_attempt_whatever_the_parallelism(
    parallelism: int, children: int
) -> None:
    config = evolve_config(max_parallelism=parallelism, children_per_generation=children)
    state, decisions = EvolveSearch(config).init(config, FACTS)

    starts = [item for item in works(decisions) if isinstance(item, StartAttempt)]
    assert len(starts) == 1
    assert state.run == ColdStart(spent=0)
    assert [key for key, _ in state.slots.in_order()] == [bootstrap_key(1)]


def test_a_stop_is_remembered_and_the_search_starts_nothing_new() -> None:
    config = evolve_config()
    search = EvolveSearch(config)
    state, _ = search.init(config, FACTS)
    control = ControlInput(control_id=ControlId(root="stop"), action="stop")

    state, decisions = search.update(
        state, StopRequested(control=control), run_view(initial_state())
    )

    assert state.stopping
    assert not [item for item in works(decisions) if isinstance(item, StartAttempt)]
