"""The planner's bounds parse, ride the handoff, and a resumed note without them does not crash.

A bound is a limit the planner states before any measurement. Notes written before the field
existed omit it and load as empty; the prompt tells the next turn to supply them, and a new
reply that does not is rejected, naming ``bounds``.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError
from tests.vibesys.orchestration.dynamic._host import PLANNER_LABEL, begin, config
from tests.vibesys.orchestration.dynamic._replies import expectation, implement, plan_reply
from tests.vibesys.orchestration.dynamic.core_policy._contexts import render, revision

from vibesys.constants import DomainName
from vibesys.domains.base import DomainRole
from vibesys.domains.registry import resolve_domain
from vibesys.domains.rendering import render_domain_section
from vibesys.dynamic_handoff import render_handoff
from vibesys.orchestration.dynamic.models import (
    BoundEvidence,
    BoundKind,
    BoundRevision,
    PerformanceBound,
    PortfolioPlan,
    hardware_limit_bound,
)
from vibesys.orchestration.dynamic.search.api import (
    ImplementPrompt,
    PlannerPrompt,
    PromptTemplate,
    ReviewPrompt,
    WriteHandoff,
)

_SECTION = {
    "modality": None,
    "interface": "service",
    "reference_path": ".",
    "benchmark_command": None,
    "accuracy_command": None,
    "runtime_notes": "",
    "profile_execution": "local",
    "profiler_id": "none",
    "workspace_sources": (),
}


def _planner() -> PlannerPrompt:
    base = revision("base")
    return PlannerPrompt(
        capacity=1,
        in_flight=0,
        remaining=1,
        base_revision=base,
        base_accuracy_passed=None,
        offer_snapshot="snapshot",
        main_revision=base,
    )


def _reply(**handoff: object) -> dict[str, object]:
    return {
        "reasoning": "why",
        "workstreams": [implement("h1")],
        "handoff": {
            "expectations": [expectation("h1")],
            "doubts": [],
            **handoff,
        },
    }


def test_a_planner_reply_with_bounds_round_trips_through_the_handoff() -> None:
    bound = hardware_limit_bound(
        metric="tpot",
        low=3.0,
        high=5.0,
        unit="ms",
        statement="active experts stream once per token",
    )
    host = begin(config(max_rounds=1), restarting=False)
    host.drain_until_turn(PLANNER_LABEL)
    host.run.reply_raw(
        host.run.expect_turn(role=PLANNER_LABEL),
        plan_reply(
            implement("h1"),
            handoff={
                "expectations": [expectation("h1")],
                "doubts": ["the batch regime"],
                "bounds": [bound.model_dump(mode="json")],
            },
        ),
    )
    (written,) = host.handoffs.values()
    assert written.handoff.bounds == (bound,)
    text = render_handoff(written)
    assert "tpot (hardware_limit)" in text
    assert "active experts stream once per token (stated_assumption)" in text
    restored = WriteHandoff.model_validate_json(written.model_dump_json())
    assert restored.handoff.bounds == written.handoff.bounds


def test_a_resumed_handoff_without_bounds_loads_and_the_next_reply_must_supply_them() -> None:
    """Earlier handoff notes have no bounds field. Resume loads them; the new turn must fill it."""
    resumed = WriteHandoff.model_validate_json(
        json.dumps(
            {
                "call": 1,
                "reasoning": "scheduled before bounds existed",
                "scheduled": [],
                "handoff": {"expectations": [], "doubts": ["old"]},
            }
        )
    )
    assert resumed.handoff.bounds == ()
    assert "none recorded; the next planner must supply them" in render_handoff(resumed)

    with pytest.raises(ValidationError, match="bounds"):
        PortfolioPlan.model_validate_json(json.dumps(_reply()))

    supplied = PortfolioPlan.model_validate_json(
        json.dumps(_reply(bounds=[hardware_limit_bound().model_dump(mode="json")]))
    )
    assert supplied.handoff.bounds[0].bound_kind is BoundKind.HARDWARE_LIMIT


def test_a_bound_that_is_not_a_range_or_a_revision_without_evidence_is_rejected() -> None:
    payload = hardware_limit_bound().model_dump(mode="json")
    payload["low"] = 5
    payload["high"] = 1
    with pytest.raises(ValidationError, match=r"bounds\.low"):
        PerformanceBound.model_validate_json(json.dumps(payload))

    revised = hardware_limit_bound().model_dump(mode="json")
    revised["revisions"] = [{"previous_low": 1.0, "previous_high": 4.0, "reason": "narrowed"}]
    with pytest.raises(ValidationError, match=r"bounds\.assumptions"):
        PerformanceBound.model_validate_json(json.dumps(revised))

    revised["assumptions"] = [
        {"statement": "counted from the weight bytes", "evidence_kind": "byte_count"}
    ]
    accepted = PerformanceBound.model_validate_json(json.dumps(revised))
    assert accepted.assumptions[0].evidence_kind is BoundEvidence.BYTE_COUNT
    assert accepted.revisions == (
        BoundRevision(previous_low=1.0, previous_high=4.0, reason="narrowed"),
    )

    ceiling_only = hardware_limit_bound().model_dump(mode="json")
    ceiling_only["bound_kind"] = "design_ceiling"
    with pytest.raises(ValidationError, match="hardware_limit"):
        PortfolioPlan.model_validate_json(json.dumps(_reply(bounds=[ceiling_only])))


def test_the_dynamic_role_prompts_state_the_bounds_method() -> None:
    planner = render(PromptTemplate.PORTFOLIO, _planner())
    implementer = render(PromptTemplate.IMPLEMENT, _implement())
    judge = render(PromptTemplate.REVIEW, _review())
    for text in (planner, implementer, judge):
        assert "never a prediction of the current code" in text
        assert "Never derive a bound from a candidate's own speed" in text
        assert "the next plan must supply" in text
    assert "`bounds`" in planner
    assert "`bound_kind`" in planner
    serving = render_domain_section(
        resolve_domain(DomainName.LLM_SERVING), DomainRole.ORCHESTRATOR, **_SECTION
    )
    database = render_domain_section(
        resolve_domain(DomainName.DATABASE), DomainRole.ORCHESTRATOR, **_SECTION
    )
    assert "before any measurement" in serving
    assert "bytes per token" in serving
    assert "performance-modeling.md" in serving
    assert "bytes per token" not in database


def _implement() -> ImplementPrompt:
    return ImplementPrompt.model_validate(
        {
            "hypothesis_id": "h1",
            "hypothesis": "claim",
            "task": "task",
            "pass_criteria": "the check still passes",
            "parent_revision": revision("base"),
        }
    )


def _review() -> ReviewPrompt:
    return ReviewPrompt.model_validate(
        {
            "hypothesis_id": "h1",
            "hypothesis": "claim",
            "pass_criteria": "the check still passes",
            "candidate": revision("cand"),
            "summary": "did the work",
        }
    )
