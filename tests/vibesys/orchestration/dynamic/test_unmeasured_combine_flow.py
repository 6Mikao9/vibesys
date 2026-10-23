"""Speculative combinations under the simulated evaluator and workspace owners."""

from __future__ import annotations

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st
from tests.vibesys.orchestration.dynamic._host import PLANNER_LABEL, Host, begin
from tests.vibesys.orchestration.dynamic._replies import (
    combine,
    implement,
    implemented,
    merge,
    merged,
    plan_reply,
)
from tests.vibesys.orchestration.dynamic.core_policy._contexts import render
from tests.vibesys.search_faults import assert_flow_closed

from vibesys.orchestration.dynamic.models import ImplementerResult, MergeResult
from vibesys.orchestration.dynamic.search.api import (
    DynamicConfig,
    ImplementPrompt,
    PlannerCorrection,
    PlannerPrompt,
    PromptTemplate,
)
from vs_core.api import Finish, Measure, RequestTurn, Retained, RevisionRef
from vs_core.api.testing import FakeView, RestartPolicy
from vs_sim.api.testing import examples

_STAGES = {
    "none": ("accuracy", "benchmark"),
    "accuracy": ("accuracy",),
    "benchmark": ("benchmark",),
    "both": ("accuracy", "benchmark"),
}


def _shell(host: Host) -> None:
    """Answer workspace, verification and decoding requests, leaving agents and measures open."""
    for _ in range(100):
        pending = [a for a in host.run.open if not isinstance(a.request, RequestTurn | Measure)]
        if not pending:
            return
        host.answer(pending[0])
    raise AssertionError(host.run.timeline())


def _fresh(host: Host) -> PlannerPrompt | PlannerCorrection:
    """Refresh a planner already thinking when the latest facts arrived."""
    host.run.reply_raw(host.run.expect_turn(role=PLANNER_LABEL), "not json")
    context = host.run.expect_turn(role=PLANNER_LABEL).context
    assert isinstance(context, PlannerPrompt | PlannerCorrection), context
    return context


def _plan(host: Host, entry: dict[str, object]) -> None:
    host.run.reply_raw(host.run.expect_turn(role=PLANNER_LABEL), plan_reply(entry))
    _shell(host)


def _work(host: Host, name: str, revision: str, facts: str) -> ImplementPrompt:
    asked = host.run.expect_turn(role="implementer")
    context = asked.context
    assert isinstance(context, ImplementPrompt)
    assert context.hypothesis_id == name
    _request_stages(host, asked.request, revision, facts)
    host.run.reply(
        asked,
        ImplementerResult.model_validate_json(implemented()),
        checkpoint=Retained(revision=RevisionRef.of_git_commit(revision)),
    )
    _shell(host)
    host.answer(host.run.expect_turn(role="judge"))
    _shell(host)
    return context


def _request_stages(host: Host, request: object, revision: str, facts: str) -> None:
    assert isinstance(request, RequestTurn)
    current = host.run.view.current()
    updated = type(current).model_validate(
        {
            **current.model_dump(),
            "measured_plans": (
                *(plan.model_dump() for plan in current.measured_plans),
                *(
                    {
                        "scope": request.scope.model_dump(),
                        "candidate": RevisionRef.of_git_commit(revision).model_dump(),
                        "purpose": "local-validation",
                        "stage_ids": (stage,),
                    }
                    for stage in _STAGES[facts]
                ),
            ),
        }
    )
    host.run.view.changes["measured_plans"] = updated.measured_plans


def _measure(host: Host, revision: str) -> None:
    leases = []
    for _ in range(4):
        pending = [
            a
            for a in host.open(Measure)
            if isinstance(a.request, Measure)
            and not a.request.reuse_only
            and a.request.plan.candidate == RevisionRef.of_git_commit(revision)
        ]
        if not pending:
            break
        (asked,) = pending
        assert isinstance(asked.request, Measure)
        leases.append(asked.request.plan.evaluation_lease)
        host.answer(asked)
        _shell(host)
    else:
        raise AssertionError(host.run.timeline())
    assert leases
    if len(leases) == 2:
        first, last = leases
        assert first is not None
        assert last is not None
        assert first.key == last.key
        assert first.retain
        assert not last.retain


LOST = 25.0
"""A part measured before the combination: below the input, so it leaves main where it was."""
LATE = 60.0
"""A part measured after the combination: above the input (50), so the first to settle moves main."""


def _coverage(context: PlannerPrompt | PlannerCorrection) -> dict[str, str]:
    return {row.hypothesis_id: row.facts.value for row in context.combinable}


@pytest.mark.regression
@settings(max_examples=examples(pr=12, nightly=50))
@example(states=("both", "none"), own="both", merge_facts="benchmark", throughput=100)
@example(
    states=("accuracy", "benchmark", "none"), own="accuracy", merge_facts="both", throughput=60
)
@example(states=("none", "none"), own="both", merge_facts="both", throughput=60)
@example(states=("both", "none"), own="both", merge_facts="both", throughput=25)
@given(
    states=st.lists(st.sampled_from(tuple(_STAGES)), min_size=2, max_size=4)
    .filter(lambda rows: "none" in rows)
    .map(tuple),
    own=st.sampled_from(("accuracy", "benchmark", "both")),
    merge_facts=st.sampled_from(("accuracy", "benchmark", "both")),
    throughput=st.sampled_from((25, int(LATE), 100)),
)
def test_combining_while_evaluation_is_busy_keeps_each_revisions_facts_local(
    states: tuple[str, ...], own: str, merge_facts: str, throughput: int
) -> None:
    # Repeat across durable boundaries, including after the submissions and their measurements.
    host = begin(
        DynamicConfig(max_rounds=len(states) + 4, max_in_flight=1, max_backlog=len(states) + 4),
        restarting=RestartPolicy(steps=frozenset({5, 20, 40, 70, 100})),
        view=FakeView(),
        throughputs={
            "composite": 100.0,
            "merged": float(throughput),
            **{f"part{i}": LATE if facts == "none" else LOST for i, facts in enumerate(states)},
        },
    )
    initial_main = host.record.main if host.record is not None else None
    parts = [(f"h{i}", f"part{i}", facts) for i, facts in enumerate(states)]
    # Measured candidates finish first; the remaining candidates occupy/wait for the one slot.
    ordered = sorted(parts, key=lambda part: part[2] == "none")
    unmeasured = [revision for _, revision, facts in ordered if facts == "none"]
    for name, revision, facts in ordered:
        _plan(host, implement(name))
        _work(host, name, revision, facts)
        if facts != "none":
            _measure(host, revision)

    shown = _fresh(host)
    assert _coverage(shown) == {name: facts for name, _, facts in parts}
    text = render(PromptTemplate.PORTFOLIO_CORRECTION, shown)
    for name, revision, facts in parts:
        assert f"hypothesis `{name}`, revision `{revision}`, facts {facts}" in text

    # The generated order includes unmeasured parts and differs from their implementation order.
    _plan(host, combine("k", *((name, revision) for name, revision, _ in parts)))
    asked = _work(host, "k", "composite", own)
    assert [(p.hypothesis_id, p.revision.revision_id.root) for p in asked.combine] == [
        (name, revision) for name, revision, _ in parts
    ]
    assert all(p.built_on == initial_main for p in asked.combine)
    assert _coverage(_fresh(host))["k"] == "none"
    assert host.record is not None
    assert host.record.main == initial_main

    # Release the occupied evaluation slot. The combination earns only its requested facts.
    for _ in range(20):
        if not host.open(Measure):
            break
        host.answer(host.open(Measure)[0])
        _shell(host)
    else:
        raise AssertionError(host.run.timeline())
    after = _fresh(host)
    assert _coverage(after)["k"] == own
    assert all(
        _coverage(after)[name] == ("both" if facts == "none" else facts) for name, _, facts in parts
    )
    assert host.record is not None
    # The first unmeasured part to settle beats the input and, built on main, moves it; the
    # composite settles after it, so it was built on a main it has left and is merged.
    assert host.record.main == RevisionRef.of_git_commit(unmeasured[0])

    if own == "both":
        _plan(host, merge("m", "k"))
        merger = host.run.expect_turn(role="merger")
        _request_stages(host, merger.request, "merged", merge_facts)
        host.run.reply(
            merger,
            MergeResult.model_validate_json(merged()),
            checkpoint=Retained(revision=RevisionRef.of_git_commit("merged")),
        )
        _shell(host)
        _measure(host, "merged")
        assert host.record is not None
        assert (host.record.main == RevisionRef.of_git_commit("merged")) is (
            merge_facts == "both" and throughput >= LATE
        )

    host.run.stop()
    host.drain()
    assert isinstance(host.run.finished, Finish)
    assert_flow_closed(host)
