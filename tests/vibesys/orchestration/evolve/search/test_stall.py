"""A frame completes, or the run ends typed, whichever way its slots reach done.

A search stalls when an event it needs never comes, or comes in a phase that has no way on. The
classes here are the ones that left evolve runs open before: a start core refuses, a settlement
core rejects, and a stop that lands in the middle of any wait. Each is driven on the real core
with scripted agents, and the run must reach its terminal state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from hypothesis import given, settings
from hypothesis import strategies as st
from tests.vibesys.orchestration.evolve.search.world import EvolveWorld, run_to_end

from vs_core.api import AttemptPhase, RejectionCode

if TYPE_CHECKING:
    from vs_core.api import DecisionId
    from vs_dst.api import SimulatedRun

RUNS = settings(max_examples=40, deadline=None, derandomize=True, database=None)
CODES = st.sampled_from(list(RejectionCode))
FIRST_ASKS = ("start", "settle", "propose", "finish")
"""The decisions core may refuse once; the search asks again or ends without them."""


def assert_closed(run: SimulatedRun) -> None:
    """Every attempt the run opened was brought to its terminal phase."""
    for attempt in run.core.attempts.attempts:
        assert attempt.phase == AttemptPhase.TERMINAL


def test_a_child_core_refuses_to_start_does_not_stall_the_run() -> None:
    # The cold start takes the only attempt core grants; g1.c1's start comes back rejected.
    run_to_end(EvolveWorld(children_per_generation=1, max_generations=1, attempt_cap=1))


@RUNS
@given(
    children=st.integers(1, 3),
    parallelism=st.integers(1, 3),
    generations=st.integers(1, 2),
    cap=st.none() | st.integers(1, 6),
    stop_at=st.none() | st.integers(0, 80),
)
def test_every_way_a_slot_ends_leads_to_a_typed_terminal_state(
    children: int, parallelism: int, generations: int, cap: int | None, stop_at: int | None
) -> None:
    """Starts core refuses and a stop at any request never leave the run waiting on a slot."""
    run = run_to_end(
        EvolveWorld(
            children_per_generation=children,
            max_parallelism=parallelism,
            max_generations=generations,
            attempt_cap=cap,
            stop_at=stop_at,
        )
    )
    assert_closed(run)


@dataclass(frozen=True)
class RefuseFirst:
    """Core refuses the first ask of each named decision with ``code``."""

    names: frozenset[str]
    code: RejectionCode

    def __call__(self, identity: DecisionId) -> RejectionCode | None:
        """The code for a first ask of a named decision, else None."""
        name = identity.root.rsplit("/", 1)[-1]
        return self.code if name in self.names else None


@RUNS
@given(names=st.sets(st.sampled_from(FIRST_ASKS), min_size=1).map(frozenset), code=CODES)
def test_a_run_ends_whichever_first_asks_core_refuses(
    names: frozenset[str], code: RejectionCode
) -> None:
    run = run_to_end(
        EvolveWorld(children_per_generation=2, max_generations=1, refuses=RefuseFirst(names, code))
    )
    assert_closed(run)


@RUNS
@given(code=CODES)
def test_a_refused_settlement_is_asked_again_plainly_and_the_slot_is_admitted(
    code: RejectionCode,
) -> None:
    """The plain close that follows a refused one settles the attempt as failed, never strands it.

    Every settlement is refused once, so no child ever passes: the cold starts run out and the
    run ends in failure with every attempt closed.
    """
    run = run_to_end(
        EvolveWorld(
            children_per_generation=2,
            max_generations=1,
            refuses=RefuseFirst(frozenset({"settle"}), code),
        )
    )
    assert_closed(run)
    result = run.core.run.result
    assert result is not None
    assert result.outcome == "failure", result.reason
