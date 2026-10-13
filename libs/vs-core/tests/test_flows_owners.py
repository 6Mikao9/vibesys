"""A flow asks the owner of an operation the search registered: ``ctx.owners.perform``.

The search names such operations in ``@search(owned=...)``. The declaration the host checks
lists them as required, the codec registers them, and a flow may ask only for those: the
outcome comes back typed, a wait that ends without one comes back as the ``Failed``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Literal

import pytest
from hypothesis import given
from hypothesis import strategies as st

import vs_core.api as core
from vs_core.api.testing import FlowRun
from vs_core.flows import search
from vs_core.testing.flows import FAKE_OPERATIONS, Verify

from .flow_support import ROLES, Config

if TYPE_CHECKING:
    from pydantic import BaseModel

KIND = "notes.add"


class Noted(core.Value):
    """What the notes owner answers."""

    count: int


class AddNote(core.OperationRequest):
    """Append a note."""

    kind: Literal["notes.add"] = "notes.add"
    lifecycle: Literal[core.LifecycleClass.IDEMPOTENT_WRITE] = core.LifecycleClass.IDEMPOTENT_WRITE
    outcome_model: ClassVar[type[BaseModel]] = Noted
    text: str


NOTES = core.OperationRegistration(
    descriptor=core.OperationDescriptor(
        kind=KIND,
        request_schema=core.SchemaRef(name="notes.add", version=1),
        outcome_schema=core.SchemaRef(name="notes.add-outcome", version=1),
        lifecycle=core.LifecycleClass.IDEMPOTENT_WRITE,
        inspect=True,
    ),
    request_model=AddNote,
    outcome_model=Noted,
)


class Heard(core.Value):
    """What the flow made of the owner's answer."""

    count: int | None = None
    failed: core.FailureReason | None = None


owning = search("owners", config=Config, operations=FAKE_OPERATIONS, roles=ROLES, owned=(NOTES,))


@owning
async def note(ctx: core.Flow, _config: Config) -> None:
    got = await ctx.owners.perform(AddNote(text="hello"), within=5.0)
    if isinstance(got, core.Failed):
        ctx.publish(Heard(failed=got.reason))
    elif isinstance(got, Noted):
        ctx.publish(Heard(count=got.count))


@owning
async def asks_for_a_kind_it_does_not_own(ctx: core.Flow, _config: Config) -> None:
    await ctx.owners.perform(Verify(parent=ctx.facts.baseline), within=5.0)


MODES = pytest.mark.parametrize("restarting", [False, True], ids=["live", "restart-every-step"])


def test_the_declaration_requires_the_owned_operations() -> None:
    required = {item.kind for item in note.declaration.required_operations}
    assert KIND in required


@MODES
@given(count=st.integers(0, 50))
def test_the_owners_outcome_reaches_the_flow_typed(count: int, *, restarting: bool) -> None:
    run = FlowRun.start(note, Config(), restarting=restarting)
    asked = run.expect(core.Operation)
    assert isinstance(asked.request, core.Operation)
    assert asked.request.request == AddNote(text="hello")
    assert asked.until is not None
    assert asked.until.limit == 5.0
    run.finish_operation(asked, Noted(count=count))
    assert run.published(Heard) == Heard(count=count)


@MODES
@pytest.mark.parametrize(
    "reason", [core.FailureReason.TIMED_OUT, core.FailureReason.LOST, core.FailureReason.REFUSED]
)
def test_a_wait_that_ends_without_an_outcome_is_a_failure_value(
    reason: core.FailureReason, *, restarting: bool
) -> None:
    run = FlowRun.start(note, Config(), restarting=restarting)
    run.fail(run.expect(core.Operation), reason)
    assert run.published(Heard) == Heard(failed=reason)


def test_a_flow_cannot_ask_for_an_operation_the_search_does_not_own() -> None:
    with pytest.raises(core.ContractError, match="not an operation this search owns"):
        FlowRun.start(asks_for_a_kind_it_does_not_own, Config())
