"""Concurrent work as child machines keyed by ``K``, with no author-written tags.

A parent search keeps its children in a ``Children[K, M]``. It routes an event to the child
it belongs to with ``route`` (or ``route_event``, which finds the child from the event's
decision). Decisions a child returns are stamped with its key on the way out, and the
results of those decisions come back to that child with the stamp removed, so a child
never knows it has siblings and the parent never builds a tag.

Children nest: an inner ``Children`` stamps first, the outer one second, and routing
removes them in the opposite order.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._registry import ContractError
from .types.common import DecisionId, Value
from .types.strategy import DecisionBase, Finish, Wait

if TYPE_CHECKING:
    from collections.abc import Callable

    from .types.search import Event
    from .types.strategy import Decision

_SEPARATOR = "/"


def _text(key: object) -> str:
    """The text a key is stamped as: its id value, enum value or string form."""
    text = str(getattr(key, "root", getattr(key, "value", key)))
    if _SEPARATOR in text or not text:
        raise ContractError(("children", "key"), "a child key must be non-empty and contain no '/'")
    return text


class Child[K, M](Value):
    """One child machine and the key it is routed by."""

    key: K
    machine: M


class Children[K, M](Value):
    """Child machines keyed by ``K``; the parent folds them in key order."""

    members: tuple[Child[K, M], ...] = ()

    def keys(self) -> tuple[K, ...]:
        """The keys of the children, in key order."""
        return tuple(member.key for member in self._ordered())

    def get(self, key: K) -> M:
        """The child machine for ``key``."""
        for member in self.members:
            if member.key == key:
                return member.machine
        raise ContractError(("children", _text(key)), "no such child")

    def put(self, key: K, machine: M) -> Children[K, M]:
        """The children with ``key`` added, or its machine replaced."""
        _text(key)
        kept = tuple(member for member in self.members if member.key != key)
        return self.model_copy(update={"members": (*kept, Child[K, M](key=key, machine=machine))})

    def remove(self, key: K) -> Children[K, M]:
        """The children without ``key``."""
        return self.model_copy(
            update={"members": tuple(member for member in self.members if member.key != key)}
        )

    def in_order(self) -> tuple[tuple[K, M], ...]:
        """Every child in key order, for folding finished children deterministically."""
        return tuple((member.key, member.machine) for member in self._ordered())

    def _ordered(self) -> tuple[Child[K, M], ...]:
        return tuple(sorted(self.members, key=lambda member: _text(member.key)))

    def route(
        self,
        key: K,
        event: Event,
        update: Callable[[M, Event], tuple[M, list[Decision]]],
    ) -> tuple[Children[K, M], list[Decision]]:
        """Give ``event`` to the child at ``key``; stamp the decisions it returns."""
        machine, decisions = update(self.get(key), _unstamped(event, _text(key)))
        return self.put(key, machine), [_stamped(decision, _text(key)) for decision in decisions]

    def route_event(
        self,
        event: Event,
        update: Callable[[M, Event], tuple[M, list[Decision]]],
    ) -> tuple[Children[K, M], list[Decision]]:
        """Give ``event`` to the child whose decision it answers."""
        key = self.key_of(event)
        if key is None:
            raise ContractError(("event",), "no child owns this event")
        return self.route(key, event, update)

    def key_of(self, event: Event) -> K | None:
        """The child that made the decision ``event`` answers, if any."""
        decision = getattr(event, "decision", None)
        if decision is None:
            return None
        prefix = decision.root.partition(_SEPARATOR)[0]
        return next((m.key for m in self.members if _text(m.key) == prefix), None)


def _stamped(decision: Decision, prefix: str) -> Decision:
    """``decision`` with its id prefixed by the child's key (a wake-up has no id)."""
    if isinstance(decision, Wait):
        return decision.model_copy(update={"request": _restamp(decision.request, prefix)})
    if isinstance(decision, Finish):
        return _restamp(decision, prefix)
    return decision


def _restamp[T: DecisionBase](work: T, prefix: str) -> T:
    identity = work.decision_id
    return work.model_copy(
        update={"decision_id": DecisionId(root=f"{prefix}{_SEPARATOR}{identity.root}")}
    )


def _unstamped(event: Event, prefix: str) -> Event:
    """``event`` with the child's key removed from the decision it answers."""
    decision = getattr(event, "decision", None)
    if decision is None:
        return event
    head, separator, rest = decision.root.partition(_SEPARATOR)
    if head != prefix or not separator:
        raise ContractError(("event", "decision"), "event does not belong to this child")
    return event.model_copy(update={"decision": DecisionId(root=rest)})
