"""How the search stories restart: at sampled steps on a pull request, at every step nightly.

Restarting round-trips the whole flow state through JSON and replays the search, so its cost
grows with every restart; vs-core proves restart equivalence for any flow once. A search story
only has to show that *its* decisions survive a restart, so a pull request restarts at a few
points (``PINNED`` plus one drawn from the session's randomness) and the nightly job at each
step. ``across_restarts`` runs a story live and restarting; ``start_flow`` is the one place the
search hosts start a ``FlowRun``.
"""

from __future__ import annotations

import functools
import secrets
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypedDict, Unpack

import pytest

from vs_core.api import StopRequested
from vs_core.api.testing import FlowRun
from vs_sim.api.testing import Depth, depth

if TYPE_CHECKING:
    from collections.abc import Callable

    from vs_core.api import Event, RunFacts
    from vs_core.api.testing import FakeView
    from vs_core.testing.flows import SearchDef

PINNED = (0, 1, 2, 5)
"""Steps every pull request restarts after: the first few, where a restart is cheap and a stop or a late task meets a fresh driver."""
DRAW_UP_TO = 60
"""A drawn restart step is in ``[0, DRAW_UP_TO)``; a story with fewer steps never reaches it."""


@dataclass(frozen=True)
class Restarts:
    """After which steps a story restarts: ``every`` step, or only those in ``steps``."""

    steps: frozenset[int] = frozenset()
    every: bool = False
    after_stop: bool = False
    """Also restart after every step once the run was told to stop (a drain is short)."""

    def __bool__(self) -> bool:
        """Whether this restarts at all."""
        return self.every or self.after_stop or bool(self.steps)

    @classmethod
    def sampled(cls, drawn: int) -> Restarts:
        """The restarts of a session: every step nightly; ``PINNED`` and ``drawn`` otherwise."""
        if depth() is Depth.NIGHTLY:
            return cls(every=True)
        return cls(steps=frozenset({*PINNED, drawn}), after_stop=True)

    def after(self, step: int, *, stopped: bool) -> bool:
        """Whether the run restarts after its ``step``-th event, once told to stop or not."""
        return self.every or step in self.steps or (self.after_stop and stopped)


class _SampledFlowRun[C](FlowRun[C]):
    """A ``FlowRun`` that restarts after the steps its ``Restarts`` names."""

    plan: Restarts = Restarts()

    def _pump(self) -> None:
        while self._queue:
            event: Event = self._queue.pop(0)
            self.state, issued = self.driver.update(self.state, event, self.view.current())
            self._take(issued)
            self._steps = getattr(self, "_steps", -1) + 1
            self._stopped = getattr(self, "_stopped", False) or isinstance(event, StopRequested)
            if self.plan.after(self._steps, stopped=self._stopped):
                self.restart()


class FlowOptions(TypedDict, total=False):
    """What a story may set when it starts a run, besides how it restarts."""

    view: FakeView | None
    adopting: bool
    facts: RunFacts


def start_flow[C](
    search: SearchDef[C], config: C, *, restarting: bool | Restarts, **options: Unpack[FlowOptions]
) -> FlowRun[C]:
    """Start ``search`` under a ``FlowRun`` that restarts as ``restarting`` says."""
    if isinstance(restarting, Restarts):
        run_type = type("_Run", (_SampledFlowRun,), {"plan": restarting})
        return run_type.start(search, config, restarting=False, **options)
    return FlowRun.start(search, config, restarting=restarting, **options)


def across_restarts[F: Callable[..., None]](story: F) -> F:
    """Run a story restarting as the session's depth says (nightly: live as well).

    A restarting run shows everything a live run does (restart equivalence is vs-core's), so a
    pull request runs the story once, restarted at a few steps; the nightly job also runs it
    live, to tell a broken search from a broken restart, and restarts after every step.

    The story takes ``restarting`` (False, or a ``Restarts``) and passes it on to its host. A
    failure names the steps the restarting run restarted after, so it can be replayed.
    """

    @functools.wraps(story)
    def run(*args: object, **kwargs: object) -> None:
        if not kwargs["restarting"]:
            story(*args, **kwargs)
            return
        kwargs["restarting"] = plan = Restarts.sampled(_draw())
        try:
            story(*args, **kwargs)
        except BaseException as error:
            error.add_note(
                f"restarted after steps {'all' if plan.every else sorted(plan.steps)} and after a stop"
            )
            raise

    if depth() is Depth.NIGHTLY:
        modes, ids = [False, True], ["live", "restarting"]
    else:
        modes, ids = [True], ["restarting"]
    return pytest.mark.parametrize("restarting", modes, ids=ids)(run)  # ty: ignore[invalid-return-type]


def _draw() -> int:
    """A step to restart after, different on every run (a failure reports it)."""
    return secrets.randbelow(DRAW_UP_TO)
