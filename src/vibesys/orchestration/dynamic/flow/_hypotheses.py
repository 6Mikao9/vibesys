"""How the hypothesis records change as plans are scheduled and workstreams finish: pure values."""

from vibesys.orchestration.dynamic.flow._outcomes import WorkstreamDone, round_of
from vibesys.orchestration.dynamic.search.api import (
    HypothesisRecord,
    PlanUpdate,
    WorkPlan,
)


def scheduled(known: HypothesisRecord | None, plan: WorkPlan, sequence: int) -> HypothesisRecord:
    """The record of the hypothesis a plan to implement works on, new or with its latest task."""
    if known is not None:
        return known.model_copy(update={"last_task": plan.task})
    return HypothesisRecord(
        hypothesis_id=plan.work_id,
        title=plan.title,
        hypothesis=plan.hypothesis,
        first_sequence=sequence,
        lineage_parent_id=plan.parent_hypothesis_id,
        last_task=plan.task,
    )


def with_round(current: HypothesisRecord, done: WorkstreamDone) -> HypothesisRecord:
    """The record with the round the finished implement workstream added."""
    continued = done.start.plan.continue_hypothesis
    return current.model_copy(
        update={
            "rounds": (*current.rounds, round_of(done)),
            "continuation_rounds": current.continuation_rounds + (1 if continued else 0),
        }
    )


def with_disposition(current: HypothesisRecord, change: PlanUpdate) -> HypothesisRecord:
    """The record after the planner parked or abandoned the hypothesis."""
    return current.model_copy(
        update={
            "strategy": change.disposition,
            "reason": change.reason,
            "reason_kind": change.reason_kind,
        }
    )
