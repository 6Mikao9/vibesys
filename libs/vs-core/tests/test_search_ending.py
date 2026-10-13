"""A search ends through the real core: stop and deadline controls, then adoption, then finish.

The flows see a stop as an event, wind down and return; the framework adopts the last choice and
finishes. These stories run the whole path on the real kernel, including the deadline, after
which core still takes the closing requests (the proposal of the winner and the finish).
"""

from __future__ import annotations

import pytest

import vs_core.api as core
from vs_core.api.testing import Failed as Refused
from vs_core.api.testing import Harness, Succeeded
from vs_core.flows import search
from vs_core.testing.drive import Script, drive
from vs_core.testing.flows import FAKE_OPERATIONS

from .flow_support import FACTS


class Config(core.Value):
    """No configuration."""


@search("ending", config=Config, operations=FAKE_OPERATIONS)
async def until_stopped(ctx: core.Flow, _config: Config) -> None:
    """Choose the input and wait to be stopped."""
    ctx.choose(core.INPUT)
    await ctx.stopped


def _stop(control: str, at: float) -> core.RunControlEvent:
    return core.RunControlEvent(
        control=core.ControlInput(control_id=core.ControlId(root=control), action="stop"),
        now_at=at,
    )


def _script(_request: core.Request, _state: core.CoreState) -> Succeeded:
    return Succeeded(revision=FACTS.baseline)


@pytest.mark.parametrize(
    ("control", "prefix"), [("operator-stop", "stopped"), ("deadline", "deadline")]
)
def test_a_stop_or_the_deadline_ends_the_run_by_adopting_the_choice(
    control: str, prefix: str
) -> None:
    config = Config()
    harness = Harness(
        registry=FAKE_OPERATIONS.registry(),
        facts=FACTS,
        deadline_at=5.0,
        events=(_stop(control, 6.0),),
    )
    trace = drive(until_stopped.driver(config, harness.registry), config, _script, harness)
    assert trace.finished
    result = trace.core.run.result
    assert result is not None
    assert result.outcome == "cancelled"
    assert result.reason.startswith(prefix)
    assert isinstance(result.selection, core.TrustedBaseline)


@search("returning", config=Config, operations=FAKE_OPERATIONS)
async def returns(_ctx: core.Flow, _config: Config) -> None:
    """Return at once: nothing is chosen, so the input stands."""


@search("hopeless", config=Config, operations=FAKE_OPERATIONS)
async def hopeless(_ctx: core.Flow, _config: Config) -> core.NoWinner:
    """Return that there is no winner."""
    return core.NoWinner(reason="none beat the input")


def _drive(
    definition: core.SearchDef[Config],
    script: Script = _script,
    *,
    empty_success: bool = True,
) -> core.CoreState:
    config = Config()
    harness = Harness(
        registry=FAKE_OPERATIONS.registry(),
        facts=FACTS,
        requirements=core.EvidenceRequirements(allow_empty_queue_success=empty_success),
    )
    trace = drive(definition.driver(config, harness.registry), config, script, harness)
    assert trace.finished
    return trace.core


def test_a_root_that_returns_keeps_the_input_and_the_run_succeeds() -> None:
    result = _drive(returns).run.result
    assert result is not None
    assert (result.outcome, result.selection) == (
        "success",
        core.TrustedBaseline(revision=FACTS.baseline),
    )
    assert result.reason.startswith("kept-input")


def test_no_winner_fails_the_run_without_asking_core_to_adopt() -> None:
    config = Config()
    harness = Harness(registry=FAKE_OPERATIONS.registry(), facts=FACTS)
    trace = drive(hopeless.driver(config, harness.registry), config, _script, harness)
    assert trace.finished
    assert trace.core.run.result == core.RunResultProposal(
        outcome="failure", reason="no-winner: none beat the input"
    )
    assert not any(isinstance(request, core.AdoptRevision) for request in trace.requests)


def test_an_adoption_the_executors_refuse_fails_the_run_with_a_typed_reason() -> None:
    result = _drive(returns, lambda _request, _state: Refused()).run.result
    assert result is not None
    assert result.outcome == "failure"
    assert result.reason.startswith("adoption-failed: ")


def test_a_run_that_did_no_work_cannot_claim_success_so_it_ends_failed_not_hung() -> None:
    """Core refuses a success with zero completed work; the framework proposes the plain failure."""
    result = _drive(returns, empty_success=False).run.result
    assert result is not None
    assert result.outcome == "failure"
    assert result.reason.startswith("finish-refused")
