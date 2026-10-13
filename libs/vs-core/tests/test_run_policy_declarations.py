"""The run's policy is declared, not defaulted: budget, backend retries, stop mode and trust.

Core holds the hard caps and no defaults, so every declaration is validated at the edge, each
field means one thing, and an unknown key is refused by name.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

import vs_core.api as core
from vs_core.api.testing import sample_limits, sample_retries

from .test_measurement_review_regressions import BENCHMARK, facts_for, observe_job
from .test_measurements import evidence, observation, plan, submitted

S = core.ObservationStatus
_COUNTS = st.integers(min_value=0, max_value=core.MAX_REQUEST_RETRIES)
_BUDGET_FIELDS = (
    "max_attempts",
    "max_turns",
    "max_parallel",
    "max_refunds",
    "max_adoption_rounds",
    "max_reask_chain",
)


def test_limits_have_no_defaults_so_a_missing_field_is_named() -> None:
    complete = sample_limits().model_dump()
    for name in complete:
        partial = {key: value for key, value in complete.items() if key != name}
        with pytest.raises(ValidationError, match=name):
            core.Limits.model_validate(partial)


@given(st.sampled_from(["max_retries", "max_measurement_submissions", "queue_allowance", "x"]))
def test_limits_reject_unknown_and_retired_keys_by_name(key: str) -> None:
    with pytest.raises(ValidationError, match=key):
        core.Limits.model_validate({**sample_limits().model_dump(), key: 1})


@given(
    name=st.sampled_from((*_BUDGET_FIELDS, "request_retries")),
    value=st.integers(min_value=2, max_value=9),
)
def test_each_limit_governs_only_its_own_meaning(name: str, value: int) -> None:
    """Changing one field changes that field and no other."""
    base = sample_limits()
    changed = sample_limits(**{name: sample_retries(value) if name == "request_retries" else value})
    differing = {
        key for key in core.Limits.model_fields if getattr(base, key) != getattr(changed, key)
    }
    assert differing <= {name}


@given(limit=st.integers(min_value=core.MAX_REQUEST_RETRIES + 1, max_value=1000))
def test_no_declaration_lifts_the_request_retry_cap(limit: int) -> None:
    with pytest.raises(ValidationError, match="limit"):
        core.RequestRetries(limit=limit, backoff=1.0, backoff_cap=30.0)


@given(
    limit=_COUNTS,
    backoff=st.floats(min_value=0.001, max_value=10),
    growth=st.floats(min_value=1, max_value=100),
    retries=st.integers(min_value=0, max_value=200),
)
def test_the_wait_between_requests_doubles_and_never_exceeds_the_declared_cap(
    limit: int, backoff: float, growth: float, retries: int
) -> None:
    declared = core.RequestRetries(limit=limit, backoff=backoff, backoff_cap=backoff * growth)
    wait = declared.delay(retries)
    assert backoff <= wait <= declared.backoff_cap
    assert declared.delay(retries + 1) >= wait


def test_a_cap_below_the_first_wait_and_unknown_keys_are_refused() -> None:
    with pytest.raises(ValidationError, match="backoff_cap"):
        core.RequestRetries(limit=1, backoff=5.0, backoff_cap=1.0)
    with pytest.raises(ValidationError, match="surprise"):
        core.RequestRetries.model_validate(
            {"limit": 1, "backoff": 1.0, "backoff_cap": 2.0, "surprise": 1}
        )


@given(action=st.sampled_from(["pause", "resume", "steer"]), mode=st.sampled_from(core.StopMode))
def test_only_a_stop_carries_a_mode_and_a_stop_always_does(
    action: str, mode: core.StopMode
) -> None:
    ident = core.ControlId(root="c")
    assert core.ControlInput(control_id=ident, action="stop", mode=mode).mode is mode
    with pytest.raises(ValidationError, match="mode"):
        core.ControlInput(control_id=ident, action="stop")
    with pytest.raises(ValidationError, match="mode"):
        core.ControlInput.model_validate({"control_id": ident, "action": action, "mode": mode})


@given(
    gated=st.sampled_from(
        [
            (),
            (core.EvidenceKind.CORRECTNESS,),
            (core.EvidenceKind.BENCHMARK,),
            (core.EvidenceKind.CORRECTNESS, core.EvidenceKind.BENCHMARK),
        ]
    ),
    kind=st.sampled_from((core.EvidenceKind.CORRECTNESS, core.EvidenceKind.BENCHMARK)),
)
def test_failed_accuracy_discards_exactly_the_evidence_the_task_gates(
    gated: tuple[core.EvidenceKind, ...], kind: core.EvidenceKind
) -> None:
    """The task's trust rule changes the classification of evidence only as it declares."""
    state, request = submitted(measurement=plan(accuracy_gated=gated))
    observed = observation(request, 2, terminal=True, released=True, status=S.SUCCEEDED)
    claim = evidence(request, observed, identity="proof", kind=kind, status=S.SUCCEEDED)
    passed = core.EvaluationStageOutcome.PASSED
    facts = facts_for(passed, passed, accuracy_passed=False)
    result = observe_job(state, observed, facts=facts, evidence=(claim,))
    assert bool(core.project(result.state).measurements) == (kind not in gated)


def test_a_gate_needs_an_accuracy_stage_to_name() -> None:
    with pytest.raises(ValueError, match="accuracy"):
        plan(accuracy_stage=None, accuracy_gated=(core.EvidenceKind.BENCHMARK,))
    assert BENCHMARK  # the shared stage names the gate refers to
