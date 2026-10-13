"""``ctx.workspaces.export_patch``: a search that ranks by code asks for a revision's patch.

The search declares the operation beside the required ones; the export is a read, answered with
the patch or with the typed failure of the wait, and a search that never declared it cannot ask.
"""

from __future__ import annotations

from typing import ClassVar, Literal

import pytest

import vs_core.api as core
from vs_core.api.search import search
from vs_core.api.testing import FAKE_OPERATIONS, FlowRun, Interpret, Render, Verify

from .flow_support import FACTS, Config

PATCH = "--- a/f\n+++ b/f\n@@\n-old\n+new\n"


class ExportPatch(core.ExportPatchBase):
    """Export a retained revision's patch."""

    kind: Literal["fake.export_patch"] = "fake.export_patch"
    outcome_model: ClassVar[type[core.PatchExport]] = core.PatchExport


EXPORTING = core.StrategyOperations("fake", Render, Verify, Interpret, export_patch=ExportPatch)


class Exported(core.Value):
    """What the flow publishes: the export it got, or why it got none."""

    patch: str | None
    reason: str | None = None


async def exports(ctx: core.Flow, _config: Config) -> core.NoWinner | None:
    outcome = await ctx.workspaces.export_patch(ctx.facts.baseline, within=30.0)
    if isinstance(outcome, core.Failed):
        ctx.publish(Exported(patch=None, reason=str(outcome.reason)))
    else:
        ctx.publish(Exported(patch=outcome.patch))
    return core.NoWinner(reason="done")


def begin(*, restarting: bool, declared: core.StrategyOperations = EXPORTING) -> FlowRun[Config]:
    definition = search("fake", config=Config, operations=declared, roles=())(exports)
    return FlowRun.start(definition, Config(), restarting=restarting)


@pytest.mark.parametrize("restarting", [False, True], ids=["live", "restart-every-step"])
def test_the_export_asks_core_for_the_revisions_patch_and_returns_it(*, restarting: bool) -> None:
    run = begin(restarting=restarting)
    asked = run.expect(core.Operation)
    request = asked.request
    assert isinstance(request, core.Operation)
    assert isinstance(request.request, ExportPatch)
    assert request.request.revision == FACTS.baseline
    assert asked.until == core.Within(limit=30.0)

    run.finish_operation(asked, core.PatchExport(status="succeeded", patch=PATCH))

    assert run.published(Exported) == Exported(patch=PATCH)


@pytest.mark.parametrize("restarting", [False, True], ids=["live", "restart-every-step"])
def test_an_export_that_cannot_be_made_is_the_typed_failure_not_a_raise(
    *, restarting: bool
) -> None:
    run = begin(restarting=restarting)
    asked = run.expect(core.Operation)

    run.fail(asked, core.FailureReason.REFUSED, cause=core.RejectionCode.BUDGET)

    published = run.published(Exported)
    assert published is not None
    assert published.patch is None
    assert published.reason


def test_a_search_that_declared_no_export_cannot_ask_for_one() -> None:
    with pytest.raises(core.ContractError, match="export_patch"):
        begin(restarting=False, declared=FAKE_OPERATIONS)
