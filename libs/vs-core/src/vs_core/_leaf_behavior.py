"""Pure operations on generated shared values; validation stays in the proto."""

from __future__ import annotations

from typing import Protocol, Self

from vs_core._corevalue import CoreValue


class BackoffPolicy:
    """Exponential retry delay bounded by the caller's cap."""

    __slots__ = ()
    first: float
    cap: float

    def delay(self, drops: int) -> float:
        """Seconds before asking again after this many losses."""
        return min(self.cap, self.first * 2**drops)


class MeasurementFailurePolicy:
    """Retry classification of a conclusive submission failure."""

    @property
    def retryable(self) -> bool:
        """Retry only failures attributable to infrastructure or ambiguous evidence."""
        return self in ("infrastructure", "ambiguous")


class ObservationFacts:
    """Derived retry and descendant facts of an external observation."""

    __slots__ = ()
    terminal: bool
    status: str
    children: tuple
    child_manifest: _ChildMembers | None

    @property
    def retryable(self) -> bool:
        """Nonterminal nonacceptance permits another dispatch."""
        return not self.terminal and self.status in ("failed", "rejected", "cancelled")

    @property
    def descendants(self) -> tuple:
        """Reported children and manifest members, in discovery order, without duplicates."""
        manifest = () if self.child_manifest is None else self.child_manifest.members
        return tuple(dict.fromkeys((*self.children, *manifest)))


class WaitDuration(CoreValue):
    """Compose a bounded wait with additional lifetime watches."""

    __slots__ = ()
    watches: tuple

    def __or__(self, watch: CoreValue) -> Self:
        """Append a watch to this duration."""
        return self.model_copy(update={"watches": (*self.watches, watch)})

    def __ror__(self, watch: CoreValue) -> Self:
        """Prepend a watch to this duration."""
        return self.model_copy(update={"watches": (watch, *self.watches)})


class _ChildMembers(Protocol):
    @property
    def members(self) -> tuple: ...
