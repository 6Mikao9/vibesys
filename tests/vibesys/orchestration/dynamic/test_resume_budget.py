"""A dynamic run resumes with a larger ``--max-rounds`` and its operator's widths, and no other option changed.

The resume policy decides what the operator may change: how much the run may spend
(``--max-rounds``, only upward) and how wide it works (``--max-in-flight``, ``--max-backlog``),
as the operator moves a running run's admission limit with ``set_max_parallel``. The plan built
from the accepted descriptor is what core's admission limit becomes (the runtime raises the
stored budget to it and replaces the stored admission limit with it,
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
from vibesys.orchestration.dynamic.launch import DynamicLaunch
from vibesys.orchestration.dynamic.search.api import DynamicConfig
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


_BACKLOGS = st.none() | st.integers(0, 8)


@pytest.mark.regression
@settings(
    max_examples=examples(pr=10, nightly=60),
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@example(rounds=42, widths=(2, 2), backlogs=(None, None))
@example(rounds=60, widths=(4, 2), backlogs=(None, None))
@given(rounds=_ROUNDS, widths=st.tuples(_WIDTH, _WIDTH), backlogs=st.tuples(_BACKLOGS, _BACKLOGS))
def test_a_resume_may_set_the_width_and_the_backlog_and_core_plans_the_limit_they_derive(
    tmp_path: Path, rounds: int, widths: tuple[int, int], backlogs: tuple[int | None, int | None]
) -> None:
    """``--resume --max-in-flight 2`` over a run recorded at another width was refused."""
    recorded = _descriptor(max_rounds=rounds, max_in_flight=widths[0], max_backlog=backlogs[0])
    requested = _descriptor(max_rounds=rounds, max_in_flight=widths[1], max_backlog=backlogs[1])

    decision = _decide(recorded, requested)

    assert decision.descriptor == (requested if requested != recorded else None)
    plan = plan_of(resolved(tmp_path), dict(requested.options))
    now, backlog = widths[1], backlogs[1]
    held = now if backlog is None else backlog
    assert plan.limits.max_parallel == min(now + held, rounds * now)
    assert plan.limits.max_attempts == rounds * now


@pytest.mark.regression
@settings(max_examples=examples(pr=10, nightly=50), deadline=None)
@example(recorded=24, lowered=1, was=2, now=4)
@given(recorded=st.integers(2, 200), lowered=st.integers(1, 199), was=_WIDTH, now=_WIDTH)
def test_a_new_width_does_not_let_a_resume_lower_max_rounds(
    recorded: int, lowered: int, was: int, now: int
) -> None:
    lowered = min(lowered, recorded - 1)

    with pytest.raises(ConfigurationError, match="cannot lower 'max_rounds'"):
        _decide(
            _descriptor(max_rounds=recorded, max_in_flight=was),
            _descriptor(max_rounds=lowered, max_in_flight=now),
        )


_OTHER_OPTIONS = sorted(set(DynamicLaunch.model_fields) - set(DynamicConfig.ADJUSTABLE_ON_RESUME))


@pytest.mark.parametrize("option", _OTHER_OPTIONS)
@pytest.mark.parametrize("rounds", [2, 5])
def test_every_option_the_operator_does_not_adjust_stays_exact_beside_a_new_width(
    option: str, rounds: int
) -> None:
    """The adjustable set is a closed list: each other launch option is refused when it changes,
    named in the error, however the widths and the budget move beside it."""
    recorded = _descriptor(max_rounds=2, **{option: "recorded"})
    changed = _descriptor(max_rounds=rounds, max_in_flight=3, max_backlog=1, **{option: "changed"})

    with pytest.raises(ConfigurationError, match="cannot change its descriptor") as refused:
        _decide(recorded, changed)
    assert repr(option) in str(refused.value)


def test_an_option_added_or_dropped_on_resume_is_a_change() -> None:
    bare = _descriptor(max_rounds=2)
    added = _descriptor(max_rounds=2, judge_every=3)

    with pytest.raises(ConfigurationError, match="cannot change its descriptor"):
        _decide(bare, added)
    with pytest.raises(ConfigurationError, match="cannot change its descriptor"):
        _decide(added, bare)
