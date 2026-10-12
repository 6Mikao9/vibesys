"""The flow search's state persists through the core envelope codec unchanged."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from tests.vibesys.orchestration.dynamic.search._harness import envelope, round_trip

from vibesys.orchestration.dynamic.core_policy.api import (
    dynamic_operation_registry,
)
from vibesys.orchestration.dynamic.flow.api import FLOW_STATE_SCHEMA
from vs_core.api import FlowState


def test_initial_state_round_trips_through_envelope() -> None:
    codec, saved = envelope()
    assert round_trip(codec, saved) == saved


def test_state_schema_version_matches_declaration() -> None:
    _, saved = envelope()
    assert saved.strategy.schema_version == FLOW_STATE_SCHEMA.version


def test_registry_declares_the_three_dynamic_operations() -> None:
    kinds = {item.kind for item in dynamic_operation_registry().descriptors}
    assert kinds == {
        "dynamic.render_role_artifacts",
        "dynamic.verify_parent_revision",
        "dynamic.interpret_evidence",
    }


def test_state_with_an_unknown_key_is_refused_naming_the_key() -> None:
    _, saved = envelope()
    document = saved.strategy.model_dump(mode="json") | {"surprise": 1}
    with pytest.raises(ValidationError, match="surprise"):
        FlowState.model_validate(document)
