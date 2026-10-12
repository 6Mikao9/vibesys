"""Accepting a designer's reply as a plan the ledger can start, and the revision it builds on.

A plan the ledger cannot accept is reported as a typed `PlanProblem`, never as prose: the search
that asked sends it back to the designer as the data of a correction, and a template words it.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Literal

from pydantic import model_validator

from vibesys.hypothesis import (
    InvalidPlanError,
    OrchestratorPlan,
    PlanFault,
    StartedHypothesis,
    normalize_hypothesis_title,
    rounds,
)
from vs_core.api.search import (
    Correction,
    FailureReason,
    ReplyError,
    TurnFailureKind,
    Value,
)

if TYPE_CHECKING:
    from vibesys.hypothesis import HypothesisConfig


class ProblemKind(StrEnum):
    """What was wrong with a designer's turn as a whole. Closed."""

    UNPARSEABLE = "unparseable"
    """The reply broke its schema; ``errors`` say where."""

    REJECTED = "rejected"
    """The reply parsed but the ledger cannot accept the plan; ``fault`` says why."""

    NO_REPLY = "no-reply"
    """The turn ended without a reply; ``ended`` or ``turn_failure`` say how."""


class PlanProblem(Value):
    """Why the designer's last turn produced no plan, as facts a correction template words.

    ``hypothesis_id`` is the hypothesis a rejected plan named (the new one, or the one it
    updated), as the designer wrote it.
    """

    kind: ProblemKind
    errors: tuple[ReplyError, ...] = ()
    fault: PlanFault | None = None
    hypothesis_id: str = ""
    ended: FailureReason | None = None
    turn_failure: TurnFailureKind | None = None

    @model_validator(mode="after")
    def fields_match_kind(self) -> PlanProblem:
        """Refuse facts that do not belong to the kind, or a kind missing its facts."""
        belongs = {
            "errors": (bool(self.errors), self.kind is ProblemKind.UNPARSEABLE),
            "fault": (self.fault is not None, self.kind is ProblemKind.REJECTED),
            "hypothesis_id": (bool(self.hypothesis_id), self.kind is ProblemKind.REJECTED),
            "ended": (self.ended is not None, self.kind is ProblemKind.NO_REPLY),
            "turn_failure": (self.turn_failure is not None, self.kind is ProblemKind.NO_REPLY),
        }
        for name, (present, allowed) in belongs.items():
            if present and not allowed:
                message = f"{name} does not belong to {self.kind.value}"
                raise ValueError(message)
        if self.kind is ProblemKind.REJECTED and self.fault is None:
            message = "a rejected plan needs its fault"
            raise ValueError(message)
        if self.kind is ProblemKind.UNPARSEABLE and not self.errors:
            message = "an unparseable reply needs its errors"
            raise ValueError(message)
        return self


class SerialTemplate(StrEnum):
    """The templates the serial searches share (a search lists them as a fallback root)."""

    PLAN_CORRECTION = "plan_correction"


class PlanCorrection(Correction):
    """The designer is asked to fix its last plan; ``of`` is the turn whose plan it corrects."""

    template: Literal[SerialTemplate.PLAN_CORRECTION] = SerialTemplate.PLAN_CORRECTION
    problem: PlanProblem


def accept_plan(
    config: HypothesisConfig, ledger: rounds.Ledger, plan: OrchestratorPlan, *, round_number: int
) -> OrchestratorPlan | PlanProblem:
    """The designer's parsed reply as a plan the ledger can start, else what is wrong with it.

    The id and title are normalized first; a plan that updates a hypothesis twice or itself,
    reuses an existing id or updates what the ledger cannot accept is refused with the fault.
    Skill recommendations are not resolved on the core path, so the plan never carries any.
    """
    plan = plan.model_copy(
        update={
            "hypothesis_id": plan.hypothesis_id.strip() or f"hypothesis-{round_number:04d}",
            "title": normalize_hypothesis_title(plan.title),
            "recommended_skills": [],
        }
    )
    try:
        _check(plan, config, ledger)
    except InvalidPlanError as fault:
        return PlanProblem(
            kind=ProblemKind.REJECTED, fault=fault.fault, hypothesis_id=fault.hypothesis_id
        )
    return plan


def _check(plan: OrchestratorPlan, config: HypothesisConfig, ledger: rounds.Ledger) -> None:
    """Raise `InvalidPlanError` for a plan the ledger cannot accept."""
    updates = [item.hypothesis_id for item in plan.hypothesis_updates]
    repeated = next((name for name in updates if updates.count(name) > 1), None)
    if repeated is not None:
        raise InvalidPlanError.duplicate_updates(repeated)
    if plan.hypothesis_id in updates:
        raise InvalidPlanError.self_reference(plan.hypothesis_id)
    if ledger.search.by_id(plan.hypothesis_id) is not None:
        raise InvalidPlanError.reused_id(plan.hypothesis_id)
    rounds.validate_plan(config, ledger, plan.hypothesis_updates)


def base_commit(started: StartedHypothesis) -> str | None:
    """The commit a started hypothesis builds on: its rollback target when one resolved."""
    rollback = started.rollback
    if rollback is not None and rollback.resolved:
        return rollback.commit
    return started.hypothesis.parent_commit
