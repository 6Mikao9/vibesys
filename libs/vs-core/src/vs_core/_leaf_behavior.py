"""Pure operations on generated shared values; validation stays in the proto."""

from __future__ import annotations

from typing import Protocol, Self

from vs_core._corevalue import CoreValue
from vs_core._generated.vibesys.core.v1.vocabulary_cv import ObservationStatus


class BackoffPolicy:
    """Exponential retry delay bounded by the caller's cap."""

    __slots__ = ()
    first: float
    cap: float

    def delay(self, drops: int) -> float:
        """Seconds before asking again after this many losses."""
        return min(self.cap, self.first * 2**drops)


class ObservationFacts:
    """Derived retry and descendant facts of an external observation."""

    __slots__ = ()
    terminal: bool
    status: ObservationStatus
    children: tuple
    child_manifest: _ChildMembers | None

    @property
    def retryable(self) -> bool:
        """Nonterminal nonacceptance permits another dispatch."""
        return not self.terminal and self.status in (
            ObservationStatus.FAILED,
            ObservationStatus.REJECTED,
            ObservationStatus.CANCELLED,
        )

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


class TaskRequirements:
    """Derive adoption requirements from declared task stages."""

    __slots__ = ()
    accuracy_stage: str | None
    measured: tuple[str, ...]

    @property
    def requires_accuracy_proof(self) -> bool:
        """Only a measured accuracy gate requires an accepted proof."""
        return self.accuracy_stage is not None and self.accuracy_stage in self.measured
