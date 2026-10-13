"""The parents a workstream can build on: offered to the planner, resolved when a plan starts.

A parent must be a candidate the run settled and core can prove; which of them are on offer
depends on the run's view, so these helpers read it through `ctx`. The choices that need no
view (a continued hypothesis builds on its own last round) are pure.
"""

from functools import partial

from pydantic import TypeAdapter

from vibesys.orchestration.dynamic.flow._outcomes import WorkstreamDone, snapshot_of
from vibesys.orchestration.dynamic.flow._run_state import RunRecord
from vibesys.orchestration.dynamic.search.api import (
    ParentConflictError,
    ParentOption,
    ParentSnapshot,
    WorkKind,
    WorkPlan,
    ingest,
    offered_parents,
    resolve,
)
from vs_core.api.search import Reads, RevisionRef, RunView

_OFFERS = TypeAdapter(tuple[ParentOption, ...])
_RESOLVED = TypeAdapter(ParentSnapshot | None)
_CATALOG = TypeAdapter(tuple[ParentSnapshot, ...] | None)


def offered(ctx: Reads, run: RunRecord) -> tuple[ParentOption, ...]:
    """The parents the planner may choose from now."""
    if not run.parents:
        return ()  # nothing to offer, and the first plan is asked before a view exists
    return ctx.read(partial(offered_parents, run.parents, run.withheld), _OFFERS)


def parent_of(ctx: Reads, run: RunRecord, plan: WorkPlan) -> tuple[RevisionRef, str | None]:
    """The revision a plan builds on and the hypothesis it descends from, frozen at scheduling.

    A parent other than the input is resolved only from what was offered.
    """
    continued = _own_last_round(run, plan)
    if continued is not None:
        return continued
    if plan.parent_hypothesis_id is not None:
        found = ctx.read(partial(_resolve, run.parents, plan), _RESOLVED)
        if found is not None:
            return found.revision, plan.parent_hypothesis_id
    return run.base, None


def offer_as_parent(ctx: Reads, run: RunRecord, done: WorkstreamDone) -> None:
    """Offer what a settled workstream made as a parent, if core can prove it."""
    snapshot = snapshot_of(done)
    if snapshot is None:
        return
    catalog = ctx.read(partial(_ingested, run.parents, snapshot), _CATALOG)
    if catalog is not None:
        run.parents = catalog


def _own_last_round(run: RunRecord, plan: WorkPlan) -> tuple[RevisionRef, str | None] | None:
    """A hypothesis that goes on builds on its last candidate, else on the last parent it had."""
    prior = run.hypotheses.get(plan.work_id)
    if plan.kind is not WorkKind.IMPLEMENT or not plan.continue_hypothesis or prior is None:
        return None
    last = prior.rounds[-1] if prior.rounds else None
    if last is not None and last.candidate is not None:
        return last.candidate, prior.lineage_parent_id
    earlier = [item for item in run.finished_work if item.plan.work_id == plan.work_id]
    return (earlier[-1].parent, earlier[-1].parent_hypothesis_id) if earlier else None


def _resolve(
    parents: tuple[ParentSnapshot, ...], plan: WorkPlan, view: RunView
) -> ParentSnapshot | None:
    assert plan.parent_hypothesis_id is not None  # noqa: S101  # LW-220427 [S101]; the caller checked it.
    return resolve(parents, view, plan.parent_hypothesis_id, plan.parent_revision)


def _ingested(
    catalog: tuple[ParentSnapshot, ...], snapshot: ParentSnapshot, view: RunView
) -> tuple[ParentSnapshot, ...] | None:
    """The catalog with ``snapshot`` published, or None when it conflicts with a published one."""
    try:
        return ingest(catalog, snapshot, view)
    except ParentConflictError:
        return None
