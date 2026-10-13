"""Concurrent agent turns on a virtual clock: they overlap, each is followed up at its own end.

Run with every fake answering at once, a shell that runs one request at a time, one that waits
for the slowest turn before deciding, and one that lets a lease lapse during a long turn all
pass. Here each turn lasts a drawn number of virtual seconds (longer than the lease for some),
so the same properties are read off the timeline (``tests/support/skeleton_timing.py``).

Past bugs these guard, and the commits before which they fail:

- the shell ran one request at a time (#1374), and took its cap from a flag instead of the
  run's ``max_parallel`` (#1375): the overlap and critical-path checks;
- a finished turn's follow-up waited for every other running turn (#1375): the follow-up check;
- one dispatch longer than the lease lost the lease (#1370): the durations longer than the lease
  (``LEASE``) end the run only if the lease was renewed beside the turns; the renewal floor
  (#1370) is checked in ``libs/vs-runtime/tests/test_core_shell.py``.
"""

from __future__ import annotations

from hypothesis import example, given, settings
from hypothesis import strategies as st
from tests.support.skeleton_timing import (
    Plan,
    StaggeredConfig,
    StaggeredStrategy,
    Timed,
    run_timed,
)
from tests.support.skeleton_world import LEASE

#: The loop wakes at most one observe interval after the last job ends (``Plan.observe_s``),
#: plus the cost of the cluster's own polls.
_SLACK_S = 6.0

# Whole seconds, all different: the scripted agent keeps one reply for all its turns, so two turns
# that end at the same instant would name the same commit.
_DURATIONS = st.lists(
    st.integers(min_value=20, max_value=250).map(float), min_size=2, max_size=3, unique=True
)


def _run(durations: list[float], cap: int) -> Timed:
    workers = len(durations)
    return run_timed(
        StaggeredStrategy(config=StaggeredConfig(workers=workers)),
        Plan(
            duration=lambda worker, _ordinal: durations[worker],
            max_parallel=cap,
            # The loop reads its controls less often than the lease lasts, so only the
            # heartbeat beside the turns keeps the lease.
            control_poll_s=4 * LEASE,
        ),
    )


@settings(max_examples=1, deadline=None, derandomize=True, database=None)
@example(durations=[30.0, 70.0])
@example(durations=[LEASE + 5, LEASE + 8, 30.0])
@given(durations=_DURATIONS)
def test_turns_run_at_once_and_each_is_followed_up_when_it_ends(durations: list[float]) -> None:
    """With a cap of the search's width, every turn is in flight at once, a finished turn is
    followed up (its change is submitted for evaluation) at the instant it ends while its peers
    still run, and the run lasts as long as its slowest turn, not the sum of them.
    """
    timed = _run(durations, cap=len(durations))

    assert timed.outcome is not None, "the run did not end by itself"
    assert timed.outcome.result is not None
    assert timed.outcome.result.outcome == "success"
    assert timed.timeline.peak_overlap() == len(durations)
    ends = sorted(span.end for span in timed.timeline.spans)
    assert len(ends) == len(durations)
    # Each turn's follow-up is submitted the moment the turn ends, not when the slowest ends.
    assert timed.cluster.submitted_at == ends
    assert timed.wall_s <= max(durations) + _SLACK_S, (timed.wall_s, durations)
    # A turn that outlasts the lease must not cost the run its lease.
    assert timed.holds_lease


@settings(max_examples=1, deadline=None, derandomize=True, database=None)
@example(durations=[30.0, 70.0, 50.0], width=2)
@given(durations=_DURATIONS, width=st.integers(min_value=1, max_value=3))
def test_turns_in_flight_never_exceed_the_runs_parallelism(
    durations: list[float], width: int
) -> None:
    """The shell's cap is the run's ``max_parallel``: a smaller cap holds turns back."""
    cap = min(width, len(durations))
    timed = _run(durations, cap=cap)

    assert timed.outcome is not None
    assert timed.timeline.peak_overlap() == cap
    assert len(timed.timeline.spans) == len(durations)
