"""Pure winner rules a search calls with every choice spelled out.

Which candidate wins, and what a candidate must beat, are the search's decisions. These
helpers only compute them and hold no default: the search passes the metric space, what to do
when nothing can be ranked and what an unmeasured input means.
"""

from collections.abc import Callable, Sequence
from enum import StrEnum

from vibesys.metrics import Measurement, MetricComparison, MetricSpace


class Unranked(StrEnum):
    """What wins when no candidate has a reading to rank by."""

    LATEST = "latest"
    """The latest candidate (the search has no benchmark to order its rounds)."""
    NONE = "none"
    """No candidate: the search keeps its input."""


def best_by[T](
    items: Sequence[T],
    *,
    space: MetricSpace,
    reading: Callable[[T], Measurement | None],
    order: Callable[[T], int],
    when_unranked: Unranked,
) -> T | None:
    """The item whose reading leads in ``space``; earlier items (by ``order``) win ties."""
    points = sorted(items, key=order)
    best = space.best(points, reading)
    unranked = points and all(reading(point) is None for point in points)
    if best is None and unranked and when_unranked is Unranked.LATEST:
        return points[-1]
    return best


def improves_on(
    candidate: Measurement | None,
    input_reading: Measurement | None,
    *,
    space: MetricSpace,
    when_input_unmeasured: bool,
) -> bool:
    """Whether ``candidate`` beats the input's reading beyond noise.

    An input with no reading leaves nothing to beat; ``when_input_unmeasured`` is the
    search's answer then.
    """
    if input_reading is None:
        return when_input_unmeasured
    return space.compare(candidate, input_reading) is MetricComparison.BETTER
