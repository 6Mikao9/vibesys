"""The sentences the hypothesis ledger stores about a failed round.

The ledger (`vibesys.hypothesis`) keeps a round's feedback as one string it later shows a
designer, so the typed `Failure` of a round is worded here, once, at the boundary where it enters
the ledger. Nothing else in a search builds a sentence from a failure.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from vibesys.serial_search._failure import Failure, FailureKind, Unmeasured

if TYPE_CHECKING:
    from collections.abc import Callable


def _role(failure: Failure) -> str:
    return "" if failure.role is None else f"{failure.role.value} "


def _word(value: object) -> str:
    return str(getattr(value, "value", "unknown"))


_WORDS: dict[FailureKind, Callable[[Failure], str]] = {
    FailureKind.STOPPED: lambda _: "stopped by the operator",
    FailureKind.REFUSED: lambda f: f"decision rejected: {_word(f.cause)}",
    FailureKind.ENDED: lambda f: f"{_role(f)}turn ended: {_word(f.ended)}",
    FailureKind.OUT_OF_BUDGET: lambda _: "attempt budget exhausted",
    FailureKind.TURN_FAILED: lambda f: f"{_role(f)}turn did not complete ({_word(f.turn_failure)})",
    FailureKind.INVALID_REPLY: lambda f: f"{_role(f)}reply was invalid",
    FailureKind.RENDER_FAILED: lambda f: f"{_role(f)}prompt render failed",
    FailureKind.WAIT_REFUSED: lambda f: (
        f"the {_role(f)}turn asked to wait and the run could not honor it ({_word(f.refusal)})"
    ),
    FailureKind.CHECKPOINT_DECLINED: lambda f: (
        f"implementer checkpoint declined ({_word(f.declined)})"
    ),
    FailureKind.NO_CHANGED_CANDIDATE: lambda _: "retained no changed candidate",
    FailureKind.EVIDENCE_REFUSED: lambda _: "evidence readings refused",
    FailureKind.EVIDENCE_UNDECODABLE: lambda _: "evidence could not be decoded",
}


def describe(failure: Failure) -> str:
    """The ledger's sentence for a failed round."""
    return _WORDS[failure.kind](failure)


def describe_unmeasured(unmeasured: Unmeasured) -> str:
    """The ledger's sentence for a candidate the machinery could not measure."""
    reason = f"the benchmark could not be measured ({unmeasured.status.value})"
    return f"{reason}: {unmeasured.output}" if unmeasured.output else reason
