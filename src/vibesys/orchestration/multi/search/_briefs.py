"""The prompt data of each multi request, from the ledger, the plan and the round's facts.

Everything here is a pure function of its arguments. Prompt contexts are typed values; the search
never writes prompt text.
"""

from vibesys.hypothesis import ArchiveConflict, Hypothesis, OrchestratorPlan, PlanningContext
from vibesys.orchestration.multi.models import ImplementerResponse
from vibesys.orchestration.multi.search._config import MultiConfig
from vibesys.orchestration.multi.search._prompts import (
    Claim,
    ContinuePrompt,
    GateFacts,
    HypothesisPlan,
    ImplementerClaims,
    ImplementPrompt,
    JudgePrompt,
    PlanPrompt,
    PreRoundPrompt,
    ProfilerPrompt,
)
from vibesys.orchestration.multi.search._values import (
    ArchiveConflictView,
    ConflictMetric,
    Dominator,
    Repair,
)
from vibesys.profile_focus import FocusView
from vibesys.serial_search.api import LastProfile, NoReply, OfficialReason, PlanEvidence
from vs_core.api.search import EvidenceRef, RevisionRef

PROFILE_DIRECTORY = "profile"


def hypothesis_plan(plan: OrchestratorPlan) -> HypothesisPlan:
    """The plan fields every round prompt shows."""
    return HypothesisPlan(
        hypothesis_id=plan.hypothesis_id,
        title=plan.title,
        hypothesis=plan.hypothesis,
        task=plan.task,
        pass_criteria=plan.pass_criteria,
        invariants=plan.invariants,
        activation_evidence=plan.activation_evidence,
        falsification_criteria=plan.falsification_criteria,
        minimum_acceptance_criteria=plan.minimum_acceptance_criteria,
    )


def gate_facts(hypothesis: Hypothesis | None) -> GateFacts:
    """What the framework already established for the active hypothesis."""
    if hypothesis is None:
        return GateFacts()
    return GateFacts(
        revert_applied=hypothesis.revert_applied,
        revert_round=hypothesis.parent_round,
        revert_commit=hypothesis.revert_commit,
        revalidation_pending=hypothesis.gate_revalidation_pending,
        approved_evaluation_artifact=hypothesis.gate_approved_evaluation_artifact,
        approved_perf_metric=hypothesis.gate_approved_perf_metric,
        approved_perf_unit=hypothesis.gate_approved_perf_unit,
    )


def _claims(values: dict[str, float]) -> tuple[Claim, ...]:
    return tuple(Claim(name=name, value=value) for name, value in values.items())


def claims_of(reply: ImplementerResponse) -> ImplementerClaims:
    """The implementer's reply as untrusted data for the judge."""
    return ImplementerClaims(
        summary=reply.summary,
        expected_behavior=reply.expected_behavior,
        hypothesis_outcome=reply.hypothesis_outcome.value,
        evidence=reply.evidence,
        next_step=reply.next_step,
        perf_metric=reply.perf_metric,
        perf_unit=reply.perf_unit,
        metrics=_claims(reply.metrics),
        evaluation_artifact=reply.evaluation_artifact,
        candidate_disposition=reply.candidate_disposition.value,
        candidate_metrics=_claims(reply.candidate_metrics),
        candidate_evaluation_artifact=reply.candidate_evaluation_artifact,
        candidate_operating_point=reply.candidate_operating_point,
        candidate_retention_reason=reply.candidate_retention_reason,
        validation_recipe_artifact=reply.validation_recipe_artifact,
    )


def conflict_view(conflict: ArchiveConflict) -> ArchiveConflictView:
    """The archive conflict as prompt data."""
    return ArchiveConflictView(
        dominators=tuple(
            Dominator(
                round_number=item.round_number,
                metrics=tuple(
                    ConflictMetric(name=m.name, value=m.value, direction=m.direction)
                    for m in item.metrics
                ),
            )
            for item in conflict.dominators
        )
    )


def plan_prompt(
    evidence: PlanEvidence,
    config: MultiConfig,
    profile: LastProfile | None,
    base: RevisionRef,
) -> PlanPrompt:
    """The new-hypothesis request: history, the frontier, carried notices and the fresh profile."""
    full = evidence.model_copy(
        update={"benchmark_enabled": config.benchmark_configured, "last_profile": profile}
    )
    return PlanPrompt(
        **{name: getattr(full, name) for name in PlanEvidence.model_fields}, base_revision=base
    )


def pre_round_prompt(
    evidence: PlanEvidence, config: MultiConfig, planning: PlanningContext
) -> PreRoundPrompt:
    """The routing request: is fresh profile evidence wanted before this plan?"""
    return PreRoundPrompt(
        round_number=planning.round_number,
        has_history=bool(evidence.history),
        profiler_kind=config.profiler_id,
        profile_execution=config.profile_execution,
        regression_kind=evidence.regression_kind,
        regression_round=evidence.regression_round,
        exhaustion_feedback=evidence.exhaustion_feedback,
        exhaustion_round=evidence.exhaustion_round,
    )


def profiler_prompt(number: int, config: MultiConfig, focus: str | None) -> ProfilerPrompt:
    """The advisory profiling request for the focus the routing turn chose."""
    return ProfilerPrompt(
        round_number=number,
        profiler_kind=config.profiler_id,
        profile_focus=focus or "general steady-state benchmark hotspots",
        benchmark_command=config.benchmark_command,
        profile_execution=config.profile_execution,
        profiler_support_name=config.profiler_support_name,
        profiler_mcp_name=config.profiler_mcp_name,
        evidence_directory=f"{PROFILE_DIRECTORY}/round-{number:04d}/",
    )


def implement_prompt(  # noqa: PLR0913  # LW-220510 [PLR0913]; the request reads independent plain facts of the round, the ledger and the attempt.
    *,
    number: int,
    retry: int,
    plan: OrchestratorPlan,
    base: RevisionRef,
    config: MultiConfig,
    hypothesis: Hypothesis | None,
    focus: FocusView | None,
    repair: Repair | None,
    no_reply: NoReply | None,
    official_reason: OfficialReason | None,
) -> ImplementPrompt | ContinuePrompt:
    """The implementation request of the open round: first brief, or a bounded continuation."""
    gates = gate_facts(hypothesis)
    step = None if hypothesis is None else hypothesis.next_step
    if step:
        return ContinuePrompt(
            round_number=number,
            retry=retry,
            plan=hypothesis_plan(plan),
            base_revision=base,
            continuation_step=step,
            framework=gates,
            repair=repair,
            no_reply=no_reply,
        )
    return ImplementPrompt(
        round_number=number,
        retry=retry,
        plan=hypothesis_plan(plan),
        base_revision=base,
        framework=gates,
        repair=repair,
        no_reply=no_reply,
        official_reason=official_reason,
        benchmark_enabled=config.benchmark_configured,
        active_component=None if focus is None else (focus.active_component or None),
        reference_path=config.reference_path,
        interface=config.interface,
    )


def judge_prompt(  # noqa: PLR0913  # LW-220511 [PLR0913]; the request reads independent plain facts of the round, the ledger and the attempt.
    *,
    number: int,
    retry: int,
    plan: OrchestratorPlan,
    config: MultiConfig,
    hypothesis: Hypothesis | None,
    reply: ImplementerResponse,
    conflict: ArchiveConflictView | None,
    official_reason: OfficialReason | None,
    cited_evidence: tuple[EvidenceRef, ...],
) -> JudgePrompt:
    """The independent review request for the implementer's latest reply."""
    return JudgePrompt(
        round_number=number,
        retry=retry,
        plan=hypothesis_plan(plan),
        claims=claims_of(reply),
        framework=gate_facts(hypothesis),
        conflict=conflict,
        official_reason=official_reason,
        benchmark_enabled=config.benchmark_configured,
        interface=config.interface,
        cited_evidence=cited_evidence,
    )
