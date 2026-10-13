"""The dynamic search's config rejects what it cannot run, naming the key, and its operations are fixed.

The config is the one input of the search: it is checkpointed with the run and read again on
every restart, so a value outside its bounds or a key nothing reads must be refused when the run
is configured, never later. The operations are the persisted kind names the executors serve.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from vibesys.orchestration.dynamic.search.api import (
    INTERPRET_KIND,
    OPERATIONS,
    RENDER_KIND,
    VERIFY_KIND,
    DynamicConfig,
)

VALID = {"max_rounds": 3}
BOUNDS = {
    "max_rounds": (1, 1000),
    "max_in_flight": (1, 32),
    "judge_every": (1, 1000),
    "max_unmeasured_turns": (1, 32),
    "max_continuation_rounds": (0, 1000),
}


@given(
    key=st.text("abcdefghijklmnopqrstuvwxyz_", min_size=1, max_size=24).filter(
        lambda name: name not in DynamicConfig.model_fields
    )
)
def test_an_unknown_option_is_rejected_by_name(key: str) -> None:
    with pytest.raises(ValidationError, match=key):
        DynamicConfig.model_validate({**VALID, key: 2})


@pytest.mark.parametrize("name", sorted(BOUNDS))
@given(data=st.data())
def test_a_value_outside_its_bounds_is_rejected_naming_the_option_and_one_inside_is_kept(
    name: str, data: st.DataObject
) -> None:
    low, high = BOUNDS[name]
    outside = data.draw(st.integers(max_value=low - 1) | st.integers(min_value=high + 1))
    with pytest.raises(ValidationError, match=name):
        DynamicConfig.model_validate({**VALID, name: outside})
    inside = data.draw(st.integers(low, high))
    assert getattr(DynamicConfig.model_validate({**VALID, name: inside}), name) == inside


@given(
    values=st.fixed_dictionaries(
        {name: st.integers(low, high) for name, (low, high) in BOUNDS.items()}
    ),
    profiling=st.booleans(),
)
def test_a_config_round_trips_through_json_and_its_budget_is_rounds_times_width(
    values: dict[str, int], *, profiling: bool
) -> None:
    config = DynamicConfig.model_validate({**values, "profiling": profiling})
    assert DynamicConfig.model_validate_json(config.model_dump_json()) == config
    assert config.start_budget == config.max_rounds * config.max_in_flight


def test_the_search_declares_exactly_the_operations_its_executors_serve() -> None:
    kinds = {item.kind for item in OPERATIONS.registry().descriptors}
    assert kinds == {RENDER_KIND, VERIFY_KIND, INTERPRET_KIND}
    assert kinds == {
        "dynamic.render_role_artifacts",
        "dynamic.verify_parent_revision",
        "dynamic.interpret_evidence",
    }
