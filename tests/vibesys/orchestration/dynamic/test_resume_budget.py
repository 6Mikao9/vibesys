"""A dynamic run resumes with a larger ``--max-rounds`` and with no other option changed.

The resume policy decides what the operator may change; the plan built from the accepted
descriptor is what core's admission limit becomes (the runtime raises the stored limits to it,
`libs/vs-runtime/tests/test_core_shell.py`). Both are asserted through the registration and
the policy's plan, the surfaces the host uses.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st
from tests.vibesys.orchestration.dynamic.core_policy._inputs import plan_of, resolved

from vibesys.errors import ConfigurationError
from vibesys.orchestration.dynamic import REGISTRATION
from vs_project.api import OrchestrationDescriptor
from vs_sim.api.testing import examples

if TYPE_CHECKING:
    from pathlib import Path

    from pydantic import JsonValue

    from vs_runtime.api import OrchestrationResumeDecision

_BASE: dict[str, JsonValue] = {"interface": "inprocess", "max_rounds": 2, "max_in_flight": 2}
_ROUNDS = st.integers(1, 200)
_WIDTH = st.integers(1, 8)


def _descriptor(**changes: JsonValue) -> OrchestrationDescriptor:
    options: dict[str, JsonValue] = {**_BASE, **changes}
    return OrchestrationDescriptor(id="dynamic", config_version=1, options=options)


def _decide(
    recorded: OrchestrationDescriptor, requested: OrchestrationDescriptor
) -> OrchestrationResumeDecision:
    policy = REGISTRATION.resume_policy
    assert policy is not None, "a dynamic run needs a resume policy to raise its budget"
    return policy(recorded, requested)


@pytest.mark.regression
@settings(
    max_examples=examples(pr=10, nightly=50),
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@example(recorded=24, extra=18, width=2)
@given(recorded=_ROUNDS, extra=st.integers(0, 100), width=_WIDTH)
def test_a_resume_may_raise_max_rounds_and_core_admits_the_new_rounds_times_the_width(
    tmp_path: Path, recorded: int, extra: int, width: int
) -> None:
    """The reported run: ``--max-rounds 42`` over 24 recorded was refused at resolution."""
    requested = _descriptor(max_rounds=recorded + extra, max_in_flight=width)

    decision = _decide(_descriptor(max_rounds=recorded, max_in_flight=width), requested)

    assert decision.descriptor == (requested if extra else None)
    plan = plan_of(resolved(tmp_path), dict(requested.options))
    assert plan.limits.max_attempts == (recorded + extra) * width


@pytest.mark.regression
@settings(max_examples=examples(pr=10, nightly=50), deadline=None)
@example(recorded=24, lowered=1, width=2)
@given(recorded=st.integers(2, 200), lowered=st.integers(1, 199), width=_WIDTH)
def test_a_resume_cannot_lower_max_rounds_and_the_error_names_the_option_and_both_values(
    recorded: int, lowered: int, width: int
) -> None:
    lowered = min(lowered, recorded - 1)

    with pytest.raises(ConfigurationError) as refused:
        _decide(
            _descriptor(max_rounds=recorded, max_in_flight=width),
            _descriptor(max_rounds=lowered, max_in_flight=width),
        )

    message = str(refused.value)
    assert "max_rounds" in message
    assert f"recorded {recorded}" in message
    assert f"requested {lowered}" in message


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("max_in_flight", 3),
        ("judge_every", 7),
        ("max_unmeasured_turns", 5),
        ("max_backlog", 4),
        ("evaluation_slots", 2),
        ("interface", "another interface"),
    ],
)
@pytest.mark.parametrize("rounds", [2, 5])
def test_every_other_option_stays_exact_even_beside_a_raised_max_rounds(
    option: str, value: JsonValue, rounds: int
) -> None:
    recorded = _descriptor(max_rounds=2)

    with pytest.raises(ConfigurationError, match="cannot change its descriptor"):
        _decide(recorded, _descriptor(max_rounds=rounds, **{option: value}))


def test_an_option_added_or_dropped_on_resume_is_a_change() -> None:
    recorded = _descriptor(max_rounds=2)
    dropped = OrchestrationDescriptor(
        id="dynamic", config_version=1, options={"interface": "inprocess", "max_rounds": 2}
    )

    with pytest.raises(ConfigurationError, match="cannot change its descriptor"):
        _decide(recorded, dropped)
    with pytest.raises(ConfigurationError, match="cannot change its descriptor"):
        _decide(dropped, recorded)
