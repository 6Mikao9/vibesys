"""The search state persists through the core envelope codec unchanged."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from tests.vibesys.orchestration.dynamic.search._harness import envelope, round_trip
from tests.vibesys.orchestration.dynamic.search._views import snapshot

from vibesys.orchestration.dynamic.search.api import (
    STATE_SCHEMA,
    DynamicState,
    dynamic_operation_registry,
)


def test_initial_state_round_trips_through_envelope() -> None:
    codec, saved = envelope()
    assert round_trip(codec, saved) == saved


def test_populated_state_round_trips_through_envelope() -> None:
    state = DynamicState(parents=(snapshot(79.835, 1), snapshot(72.564, 2)))
    codec, saved = envelope(state)
    assert round_trip(codec, saved).strategy == state


def test_state_schema_version_matches_declaration() -> None:
    assert DynamicState().schema_version == STATE_SCHEMA.version


def test_registry_declares_the_three_dynamic_operations() -> None:
    kinds = {item.kind for item in dynamic_operation_registry().descriptors}
    assert kinds == {
        "dynamic.render_role_artifacts",
        "dynamic.verify_parent_revision",
        "dynamic.interpret_evidence",
    }


_V1_STATE = {
    "schema_version": 1,
    "phase": "searching",
    "stopping": False,
    "hypotheses": [],
    "attempts": [],
    "parents": [],
    "winner": None,
    "refunded": 0,
    "withheld": [],
    "unreachable": [],
}


def test_state_saved_by_the_flag_based_strategy_is_refused_naming_its_version() -> None:
    with pytest.raises(ValidationError, match="schema_version"):
        DynamicState.model_validate(_V1_STATE)


def test_state_with_an_unknown_key_is_refused_naming_the_key() -> None:
    with pytest.raises(ValidationError, match="surprise"):
        DynamicState.model_validate({"schema_version": 2, "surprise": 1})
