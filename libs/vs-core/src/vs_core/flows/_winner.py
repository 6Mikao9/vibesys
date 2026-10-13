"""The winner: what a flow names with ``ctx.choose``, and how the framework ends the run with it.

A flow may name its current winner any time. When the root flow returns, or the operator stops
the run, or the deadline passes (the host stops the run at the deadline), the framework takes
the last choice, has core adopt it, and finishes the run with the result. There is one path
for all three; a flow never finishes the run itself. Nothing chosen means the input stands.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Final, Literal

from vs_core._registry import ContractError
from vs_core.types.common import THIS_RUN, DecisionId, ObservationStatus, RevisionRef, Value
from vs_core.types.search import AdoptionFinished
from vs_core.types.settlement import (
    AdoptionFailed,
    AdoptionFailureReason,
    AdoptionResult,
    RetainedCandidate,
    RunResultProposal,
    Selection,
    Settlement,
    TrustedBaseline,
)
from vs_core.types.strategy import Finish, ProposeWinner
from vs_core.types.until import Failed, FailureReason

if TYPE_CHECKING:
    from ._kit import Kit


class Input:
    """The run's input, named as the winner: the baseline stands, nothing changed."""

    def __repr__(self) -> str:
        """Show the sentinel by name."""
        return "INPUT"


INPUT: Final = Input()
"""``ctx.choose(INPUT)``: no candidate beat the input."""


class NoWinner(Value):
    """What a root flow returns when the run ends without a winner, and why.

    The run fails with this reason; nothing is adopted.
    """

    reason: str


class Kept(Value):
    """A candidate a closed workspace retained: what ``ctx.choose`` can name."""

    settlement: Settlement

    @property
    def revision(self) -> RevisionRef:
        """The retained revision."""
        if self.settlement.candidate is None:
            message = "a kept candidate has a revision"
            raise ContractError(("kept",), message)
        return self.settlement.candidate

    @classmethod
    def of(cls, settlement: Settlement) -> Kept | None:
        """The candidate ``settlement`` kept, or None when it kept none."""
        if settlement.candidate is None or settlement.retention != "candidate":
            return None
        return cls(settlement=settlement)


class EndReason(StrEnum):
    """Why the framework ended the run as it did. Closed: the result's reason starts with it."""

    ADOPTED = "adopted"
    """The chosen candidate was adopted."""
    KEPT_INPUT = "kept-input"
    """Nothing beat the input, and the input was adopted as it stood."""
    NO_WINNER = "no-winner"
    """The root flow returned ``NoWinner``."""
    ADOPTION_FAILED = "adoption-failed"
    """Core would not adopt the chosen winner."""
    STOPPED = "stopped"
    """The operator stopped the run (the result is cancelled)."""
    DEADLINE = "deadline"
    """The run deadline passed (the result is cancelled)."""
    FINISH_REFUSED = "finish-refused"
    """Core refused the result; the run ended with the plain failure."""


class Adopted(Value):
    """The adoption of the chosen winner succeeded."""

    selection: Selection


class NotAdopted(Value):
    """The adoption of the chosen winner did not succeed, and why (a closed reason)."""

    selection: Selection
    reason: AdoptionFailureReason | FailureReason


def reason_text(ending: EndReason, detail: str = "") -> str:
    """The reason a run result carries: the ending, then what it was about."""
    return f"{ending.value}: {detail}" if detail else ending.value


async def adopt(kit: Kit, selection: Selection) -> Adopted | NotAdopted:
    """Ask core to adopt ``selection`` and report how it went."""
    request = ProposeWinner(
        decision_id=DecisionId(root="adopt"), scope=THIS_RUN, selection=selection
    )
    event = await kit.wait(request, kit.cleanup_within())
    match event:
        case AdoptionFinished(result=AdoptionResult(observation=seen)):
            if seen.status is ObservationStatus.SUCCEEDED:
                return Adopted(selection=selection)
            return NotAdopted(selection=selection, reason=AdoptionFailureReason.VERIFY_FAILED)
        case AdoptionFinished(result=AdoptionFailed(reason=reason)):
            return NotAdopted(selection=selection, reason=reason)
        case Failed(reason=reason):
            return NotAdopted(selection=selection, reason=reason)
        case unexpected:
            message = f"adoption ended with {unexpected.kind}"
            raise ContractError(("adopt",), message)


def _stop_ending(kit: Kit) -> EndReason | None:
    """How the run was stopped, if it was: the operator, or the host's deadline control."""
    stop = kit.run.stop
    if stop is None:
        return None
    return EndReason.DEADLINE if stop.control.control_id.root == "deadline" else EndReason.STOPPED


def _finish_mode(kit: Kit) -> Literal["drain", "cancel"]:
    """What the stop asked for; a run that ends by itself has nothing in flight to cancel."""
    stop = kit.run.stop
    if stop is None or stop.control.mode is None:
        return "drain"
    return stop.control.mode.value


def _selection_name(selection: Selection) -> str:
    if isinstance(selection, RetainedCandidate):
        return selection.settlement_id.root
    return "the input"


async def conclude(kit: Kit, returned: NoWinner | None) -> None:
    """End the run: adopt the last choice (unless there is no winner), then finish.

    A stop or a deadline makes the result ``cancelled`` (a proposal core refuses because the run
    is closing included); a failed adoption or a ``NoWinner`` is a ``failure`` with a typed reason.
    """
    stopped = _stop_ending(kit)
    result = await _result(kit, returned, stopped)
    mode = _finish_mode(kit)
    refused = await kit.finish(
        Finish(decision_id=DecisionId(root="finish"), scope=THIS_RUN, mode=mode, result=result)
    )
    # Core refused the result. A run that nobody ends would hang, so the plain failure is
    # proposed once.
    plain = RunResultProposal(
        outcome="failure", reason=reason_text(EndReason.FINISH_REFUSED, refused.reason.value)
    )
    again = await kit.finish(
        Finish(decision_id=DecisionId(root="finish"), scope=THIS_RUN, mode=mode, result=plain)
    )
    message = f"core refused the plain failure of the run: {again.reason.value}"
    raise ContractError(("finish",), message)


async def _result(
    kit: Kit, returned: NoWinner | None, stopped: EndReason | None
) -> RunResultProposal:
    if isinstance(returned, NoWinner):
        return RunResultProposal(
            outcome="cancelled" if stopped else "failure",
            reason=reason_text(stopped or EndReason.NO_WINNER, returned.reason),
        )
    selection = kit.run.choice or TrustedBaseline(revision=kit.run.facts.baseline)
    adopted = await adopt(kit, selection)
    if isinstance(adopted, NotAdopted):
        # Core may refuse the proposal because the stop closed the run to decisions: the operator
        # ended it and nothing failed. An adoption that was tried and failed still fails the run.
        detail = reason_text(EndReason.ADOPTION_FAILED, adopted.reason.value)
        if stopped is not None and adopted.reason is FailureReason.REFUSED:
            return RunResultProposal(outcome="cancelled", reason=reason_text(stopped, detail))
        return RunResultProposal(outcome="failure", reason=detail)
    kind = EndReason.KEPT_INPUT if isinstance(selection, TrustedBaseline) else EndReason.ADOPTED
    detail = reason_text(kind, _selection_name(selection))
    if stopped is not None:
        return RunResultProposal(
            outcome="cancelled", reason=reason_text(stopped, detail), selection=selection
        )
    return RunResultProposal(outcome="success", reason=detail, selection=selection)
