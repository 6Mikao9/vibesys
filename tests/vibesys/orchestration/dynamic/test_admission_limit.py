"""A dynamic run's concurrency comes from the operator's flags, never from its budget.

Agents work ``--max-in-flight`` wide and up to ``--max-backlog`` more attempts wait for their
trusted measurement beside them, by default as many as the width so that no agent is blocked on
the evaluation queue. The two facts are derived in one place each (`DynamicConfig.admission_limit`
and `DynamicConfig.start_budget`), so the width core admits at, and the width the shell executes
at, can never follow the number of attempts the run may spend: the budget is
``max_rounds * max_in_flight`` and only caps the limit.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st
from tests.vibesys.orchestration.dynamic.core_policy._inputs import plan_of, resolved

from vibesys.orchestration.dynamic import REGISTRATION
from vibesys.orchestration.dynamic.launch import DynamicLaunch
from vibesys.orchestration.dynamic.search.api import budget
from vs_project.api import OrchestrationDescriptor
from vs_sim.api.testing import examples

if TYPE_CHECKING:
    from pathlib import Path

    from pydantic import JsonValue

_BASE: dict[str, JsonValue] = {"interface": "inprocess", "max_in_flight": 2}
_ROUNDS = st.integers(1, 1000)
_WIDTH = st.integers(1, 32)
_BACKLOG = st.integers(0, 32)


def _options(rounds: int, width: int, backlog: int | None) -> dict[str, JsonValue]:
    options: dict[str, JsonValue] = {**_BASE, "max_rounds": rounds, "max_in_flight": width}
    if backlog is not None:
        options["max_backlog"] = backlog
    return options


@pytest.mark.regression
@settings(max_examples=examples(pr=20, nightly=200), deadline=None)
@example(rounds=42, width=2, backlog=None)
@example(rounds=42, width=2, backlog=0)
@example(rounds=1, width=3, backlog=None)
@given(rounds=_ROUNDS, width=_WIDTH, backlog=st.none() | _BACKLOG)
def test_the_admission_limit_is_the_width_plus_the_backlog_and_the_budget_is_rounds_times_width(
    rounds: int, width: int, backlog: int | None
) -> None:
    """The reported run: ``--max-in-flight 2 --max-rounds 42`` admitted 84 attempts at once."""
    config = DynamicLaunch.model_validate(_options(rounds, width, backlog))

    plan = budget(config)

    assert plan.attempts == rounds * width
    held_beside = width if backlog is None else backlog
    assert plan.parallel == min(width + held_beside, rounds * width)
    if backlog == 0:
        assert plan.parallel == width


@settings(max_examples=examples(pr=10, nightly=100), deadline=None)
@given(
    rounds=st.integers(1, 500),
    more=st.integers(0, 500),
    width=_WIDTH,
    backlog=st.none() | _BACKLOG,
)
def test_raising_max_rounds_never_raises_the_admission_limit_past_what_the_operator_set(
    rounds: int, more: int, width: int, backlog: int | None
) -> None:
    before = budget(DynamicLaunch.model_validate(_options(rounds, width, backlog)))
    after = budget(DynamicLaunch.model_validate(_options(rounds + more, width, backlog)))

    assert after.attempts >= before.attempts
    assert before.parallel <= after.parallel <= width + (width if backlog is None else backlog)


@pytest.mark.regression
@settings(
    max_examples=examples(pr=6, nightly=40),
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@example(rounds=42, width=2)
@given(rounds=_ROUNDS, width=_WIDTH)
def test_the_run_core_starts_under_is_planned_at_the_operators_width(
    tmp_path: Path, rounds: int, width: int
) -> None:
    """The limits core stores come from the production plan, the same on a resume's raise."""
    options = _options(rounds, width, None)

    plan = plan_of(resolved(tmp_path), dict(options))

    assert plan.limits.max_attempts == rounds * width
    assert plan.limits.max_parallel == min(2 * width, rounds * width)


@settings(max_examples=examples(pr=6, nightly=40), deadline=None)
@given(rounds=st.integers(1, 500), more=st.integers(0, 100), width=_WIDTH)
def test_a_run_recorded_with_a_null_backlog_resumes_with_a_larger_max_rounds_at_the_derived_backlog(
    rounds: int, more: int, width: int
) -> None:
    """A descriptor that spells the default ``null`` and one that omits it plan the same limit."""
    policy = REGISTRATION.resume_policy
    assert policy is not None
    recorded = OrchestrationDescriptor(
        id="dynamic",
        config_version=1,
        options={**_options(rounds, width, None), "max_backlog": None},
    )
    requested = OrchestrationDescriptor(
        id="dynamic",
        config_version=1,
        options=_options(rounds + more, width, None),
    )

    decision = policy(recorded, requested)

    assert decision.descriptor == requested
    null = DynamicLaunch.model_validate(recorded.options)
    omitted = DynamicLaunch.model_validate(_options(rounds, width, None))
    assert null.backlog == omitted.backlog == width
    assert budget(null).parallel == budget(omitted).parallel
