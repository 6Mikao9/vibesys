"""Pure retry timing and classification for generated retry declarations."""

from __future__ import annotations

from typing import ClassVar


class RequestRetryPolicy:
    """Behavior of a bounded exponential retry declaration."""

    __slots__ = ()

    backoff: float
    backoff_cap: float

    def delay(self, retries: int) -> float:
        """Core seconds before attempt number retries + 1, doubling to the cap."""
        return min(self.backoff * 2 ** max(retries - 1, 0), self.backoff_cap)


class MeasurementFailurePolicy:
    """Retry classification of a conclusive submission failure."""

    __slots__ = ()
    INFRASTRUCTURE: ClassVar[MeasurementFailurePolicy]
    AMBIGUOUS: ClassVar[MeasurementFailurePolicy]

    @property
    def retryable(self) -> bool:
        """Retry failures attributable to infrastructure or ambiguous evidence."""
        return self in (self.INFRASTRUCTURE, self.AMBIGUOUS)
