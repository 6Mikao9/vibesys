"""A search whose chosen decisions core refuses, so a test reaches every refusal.

Core refuses a decision for reasons a scripted world cannot produce (a start over the attempt
budget, a settlement whose proof does not hold). From the search's side a refusal is one thing:
the decision never started and its own result arrives as ``Failed(REFUSED, cause)``. `Refusing`
makes exactly that happen for the decisions a predicate names: it holds the decision back from
core, which therefore stays consistent, and tells the search it was refused.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from vs_core.api import Failed, FailureReason, Wait

if TYPE_CHECKING:
    from collections.abc import Callable

    from vs_core.api import (
        Decision,
        DecisionId,
        Event,
        RejectionCode,
        RunFacts,
        RunView,
        Search,
        SearchDeclaration,
        StrategyState,
    )

type Refuses = Callable[[DecisionId], RejectionCode | None]
"""The rejection code core answers a decision with, or None to let it through."""

_LIMIT = 64
"""How many refusals one event may chain; a search that keeps asking again past it is stuck."""


def decision_id(decision: Decision) -> DecisionId | None:
    """The identity a decision's result carries."""
    work = decision.request if isinstance(decision, Wait) else decision
    return getattr(work, "decision_id", None)


class Refusing[S: StrategyState, C]:
    """``inner`` with the decisions ``refuses`` names turned into refusals."""

    def __init__(self, inner: Search[S, C], refuses: Refuses) -> None:
        """Wrap ``inner``; ``refuses`` must be a pure function of the decision's identity."""
        self._inner = inner
        self._refuses = refuses

    @property
    def declaration(self) -> SearchDeclaration:
        """What the wrapped search declares."""
        return self._inner.declaration

    @property
    def state_model(self) -> type[S]:
        """The class of the wrapped search's state."""
        return self._inner.state_model

    def init(self, config: C, facts: RunFacts) -> tuple[S, list[Decision]]:
        """The wrapped search's first state; the first decisions are never refused."""
        return self._inner.init(config, facts)

    def update(self, state: S, event: Event, view: RunView) -> tuple[S, list[Decision]]:
        """The wrapped search's answer, with refused decisions replaced by their refusals."""
        state, decisions = self._inner.update(state, event, view)
        sent: list[Decision] = []
        for _ in range(_LIMIT):
            refused = [(item, self._code(item)) for item in decisions]
            sent.extend(item for item, code in refused if code is None)
            held = [(item, code) for item, code in refused if code is not None]
            if not held:
                return state, sent
            decisions = []
            for item, code in held:
                state, more = self._inner.update(
                    state,
                    Failed(reason=FailureReason.REFUSED, decision=decision_id(item), cause=code),
                    view,
                )
                decisions.extend(more)
        message = "the search keeps asking again after every refusal"
        raise AssertionError(message)

    def _code(self, decision: Decision) -> RejectionCode | None:
        identity = decision_id(decision)
        return None if identity is None else self._refuses(identity)


def as_search(search: Refusing[Any, Any]) -> Search[Any, Any]:
    """``search`` as the protocol a launch takes (it has every member of it)."""
    return search
