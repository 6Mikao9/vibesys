"""The one rule for how a ``Within`` and the run deadline become a record's ``Until``.

A wait states how long the work may take. The run may have less time left than that,
which is not malformed: core caps the stored time at the run deadline. A run deadline
that is not after the run clock has no time left; only then is new work refused, and
core's own cleanup requests still get their bounded time (they are never capped by an
expired run, so a stopped run can always close).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .types.until import Until

if TYPE_CHECKING:
    from .types.kernel import RunState
    from .types.until import Within


def resolve_until(run: RunState, within: Within) -> Until:
    """The absolute time and watches core stores for ``within`` accepted now.

    Work is capped before the run deadline; after it, requests keep their own bounded
    time so reconciliation and cleanup can still run. The time never precedes the clock.
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
