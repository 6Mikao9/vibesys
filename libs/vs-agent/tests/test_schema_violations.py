"""A reply that breaks its schema reports each broken field, by path, as typed data."""

import json

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import BaseModel, ConfigDict, Field

from vs_agent.api import AgentOutputSchemaError, SchemaViolation, parse_typed_response


class Item(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[a-z]+$")


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: tuple[Item, ...]


@given(good=st.integers(min_value=0, max_value=4))
def test_a_rejected_field_is_reported_at_its_path_with_its_message(good: int) -> None:
    items = [{"name": "ok"}] * good + [{"name": "Not OK"}]
    with pytest.raises(AgentOutputSchemaError) as raised:
        parse_typed_response(json.dumps({"items": items}), Plan)
    (violation,) = raised.value.violations
    assert violation.path == ("items", good, "name")
    assert "pattern" in violation.message


def test_an_error_without_fields_is_one_violation_holding_its_text() -> None:
    error = AgentOutputSchemaError("no JSON object")
    assert error.violations == (SchemaViolation(path=(), message="no JSON object"),)
