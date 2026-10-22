"""The implementer's submit call names which trusted gates to run, and in what order."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from vs_evaluation.api import SubmitCall


def test_a_submit_call_without_steps_runs_every_gate() -> None:
    call = SubmitCall.model_validate({"action": "submit", "token": "t"})
    assert call.steps == ()


def test_a_duplicate_step_names_the_field() -> None:
    with pytest.raises(ValidationError) as caught:
        SubmitCall.model_validate(
            {"action": "submit", "token": "t", "steps": ["accuracy", "accuracy"]}
        )
    assert any(error["loc"][:1] == ("steps",) for error in caught.value.errors())


def test_a_step_outside_the_gates_names_the_field() -> None:
    with pytest.raises(ValidationError) as caught:
        SubmitCall.model_validate({"action": "submit", "token": "t", "steps": ["profile"]})
    assert any(tuple(error["loc"][:1]) == ("steps",) for error in caught.value.errors())
