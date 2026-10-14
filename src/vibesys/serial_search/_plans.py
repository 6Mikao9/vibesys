"""Accepting a designer's reply as a plan the ledger can start, and the revision it builds on."""

from __future__ import annotations

from typing import TYPE_CHECKING

from vibesys.hypothesis import (
    InvalidPlanError,
    OrchestratorPlan,
    StartedHypothesis,
    normalize_hypothesis_title,
    rounds,
)
from vs_core.api.search import RevisionRef

if TYPE_CHECKING:
    from vibesys.hypothesis import HypothesisConfig


def accept_plan(
    config: HypothesisConfig, ledger: rounds.Ledger, reply: OrchestratorPlan, *, round_number: int
) -> OrchestratorPlan | str:
    """The designer's reply as a plan the ledger can start, else what is wrong with it.

    The reply already parsed against its model. The id and title are normalized; a plan that
    updates a hypothesis twice or itself, reuses an existing id or updates what the ledger
    cannot accept is refused with the reason the correction turn shows the designer. Skill
    recommendations are not resolved on the core path, so the plan never carries any.
    """
    try:
        plan = reply.model_copy(
            update={
                "hypothesis_id": reply.hypothesis_id.strip() or f"hypothesis-{round_number:04d}",
                "title": normalize_hypothesis_title(reply.title),
                "recommended_skills": [],
            }
        )
        _check(plan, config, ledger)
    except ValueError as problem:
        return str(problem)
    return plan


def _check(plan: OrchestratorPlan, config: HypothesisConfig, ledger: rounds.Ledger) -> None:
    """Raise `ValueError` for a plan the ledger cannot accept."""
    updates = [item.hypothesis_id for item in plan.hypothesis_updates]
    if len(updates) != len(set(updates)):
        raise InvalidPlanError.duplicate_updates()
    if plan.hypothesis_id in updates:
        raise InvalidPlanError.self_reference()
    if ledger.search.by_id(plan.hypothesis_id) is not None:
        raise InvalidPlanError.reused_id(plan.hypothesis_id)
    rounds.validate_plan(config, ledger, plan.hypothesis_updates)


def base_commit(started: StartedHypothesis) -> str | None:
    """The commit a started hypothesis builds on: its rollback target when one resolved."""
    rollback = started.rollback
    if rollback is not None and rollback.resolved:
        return rollback.commit
    return started.hypothesis.parent_commit


def revision_of(commit: str | None, fallback: RevisionRef) -> RevisionRef:
    """The revision a recorded commit names, else ``fallback``."""
    return fallback if not commit else RevisionRef.of_git_commit(commit)
