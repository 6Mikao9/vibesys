"""The trusted gates of a candidate: measure it with the task's stages and read what they say."""

from __future__ import annotations

from vibesys.metrics import AcceptedReading, accept_readings
from vs_core.api.search import (
    EvidenceRef,
    Failed,
    Flow,
    MeasurementRecipe,
    RevisionRef,
    Value,
    Workspace,
    trusted_keys,
)


class Gated(Value):
    """The gates ran and their readings were decoded; a gate may still have rejected."""

    readings: tuple[AcceptedReading, ...]


class NotMeasured(Value):
    """The machinery could not measure the candidate; nothing is known against it."""

    feedback: str


class Unreadable(Value):
    """The evidence of the gates could not be read back; the round fails with this reason."""

    reason: str


type GateResult = Gated | NotMeasured | Unreadable


async def measure_gates(ctx: Flow, ws: Workspace, revision: RevisionRef) -> GateResult:
    """Measure ``revision`` with the task's stages, then have the evidence owner decode them.

    The backend measures again after an infrastructure failure, so what reaches here is the
    candidate's own result or a measurement that ended without one.
    """
    terms = ctx.task.terms
    names = terms.measured
    recipe = MeasurementRecipe.of(ctx.facts)
    plan = recipe.plan(revision, purpose="official", now_at=ctx.clock.now(), stages=names)
    found = await ctx.evaluation.measure(plan, workspace=ws, within=recipe.budget(names))
    if isinstance(found, Failed):
        return NotMeasured(feedback=f"the benchmark could not be measured ({found.reason.value})")
    keys = trusted_keys(found, scope=found.scope, candidate=revision, purpose="official")
    if not keys:
        reason = f"the benchmark could not be measured ({found.status.value})"
        return NotMeasured(feedback=f"{reason}: {found.diagnostic}" if found.diagnostic else reason)
    refs = tuple(item for item in found.evidence if item.key in keys)
    return await _read(ctx, ws, refs)


async def _read(ctx: Flow, ws: Workspace, refs: tuple[EvidenceRef, ...]) -> Gated | Unreadable:
    """Have the evidence owner decode the gates' evidence into readings."""
    outcome = await ctx.evaluation.readings(
        refs, workspace=ws, within=ctx.task.terms.operation_seconds
    )
    if isinstance(outcome, Failed):
        return Unreadable(reason=f"evidence readings ended ({outcome.reason.value})")
    if outcome is None:
        return Unreadable(reason="evidence could not be decoded")
    accepted = accept_readings(outcome.readings, refs)
    if isinstance(accepted, str):
        return Unreadable(reason=f"evidence readings refused: {accepted}")
    if outcome.status != "succeeded" or not accepted:
        return Unreadable(reason=f"evidence could not be decoded ({outcome.status})")
    return Gated(readings=accepted)
