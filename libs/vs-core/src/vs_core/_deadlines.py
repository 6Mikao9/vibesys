"""The one rule for how a ``Within`` and the run deadline become a record's ``Until``.

A wait states how long the work may take. The run may have less time left than that,
which is not malformed: core caps the stored time at the run deadline. A run deadline
that is not after the run clock has no time left; only then is new work refused, and
core's own cleanup requests still get their bounded time: they carry an explicit
``Within(limits.cleanup_bound)`` (see ``cleanup``), never "until the run ends", so a
stopped run can always close and no internal request waits without a bound.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .types.records import Outbound, within
from .types.until import Until

if TYPE_CHECKING:
    from .types.kernel import RunState
    from .types.records import Request
    from .types.until import Within


def resolve_until(run: RunState, within: Within) -> Until:
    """The absolute time and watches core stores for ``within`` accepted now.

    While the run has time left the stored time is capped at the run deadline. Once the
    run deadline has passed, nothing caps it: the request keeps the bounded ``limit`` it
    was given (core's cleanup budget, for its own requests), so cleanup and reconciliation
    can still run. A limit of zero or less is refused before this is reached; the time
    never precedes the clock.
    """
    at = run.now_at + max(within.limit, 0.0)
    if run.deadline_at > run.now_at:
        at = min(at, run.deadline_at)
    return Until(at=at, watches=within.watches)


def limit_to(run: RunState, at: float) -> float:
    """The duration from the run clock to the absolute time ``at`` (never negative)."""
    return max(at - run.now_at, 0.0)


def bound_to_run(run: RunState, requested: float) -> float:
    """A stated time (an agent's wait deadline), no later than the run deadline."""
    return min(requested, run.deadline_at)


def time_remains(run: RunState, requested: float) -> bool:
    """Whether any time is left between the run clock and the bounded time."""
    return bound_to_run(run, requested) > run.now_at


def cleanup(run: RunState, request: Request) -> Outbound:
    """``request`` as core's own cleanup or inspection: its own bounded time, run over or not."""
    return within(request, run.limits.cleanup_bound)
