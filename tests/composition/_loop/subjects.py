"""The searches registered with the conformance suite.

A ported search adds one `Subject` here: its fresh-launch factory, its harness, its reply
schemas and a generator of scripted worlds. The properties themselves live in
`conformance.py` and are not edited per search.

The dynamic and evolve searches are registered. The other searches (issue queue, multi, single)
join as they are written as flows.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from hypothesis import strategies as st
from tests.composition._loop.conformance import Subject, World
from tests.composition._loop.dynamic_world import DynamicWorld
from tests.composition._loop.evolve_world import EvolveWorld
from tests.support.evolve_script import mutated
from tests.support.evolve_script import reviewed as evolve_reviewed
from tests.vibesys.orchestration.dynamic._replies import (
    implement,
    implemented,
    plan_reply,
    reviewed,
)

from vibesys.orchestration.dynamic.search.api import RunSnapshot
from vibesys.orchestration.evolve.search.api import EvolveSnapshot

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping


_THROUGHPUTS = (None, 30.0, 80.0, 120.0, 160.0)

_ISSUED = frozenset(
    {
        "adopt_revision",
        "close_attempt_scope",
        "close_session",
        "discard_workspace",
        "dispatch_turn",
        "ensure_session",
        "ensure_workspace",
        "execute_registered_operation",
        "observe_owned_job",
        "retain_revision",
        "snapshot_and_retain",
        "submit_measurement",
        "verify_adoption",
    }
)
"""Request kinds a dynamic run with no fault causes."""


def _dynamic(
    hypotheses: int,
    throughputs: tuple[float | None, ...],
    passes: tuple[bool, ...],
    settings: Mapping[str, Any],
) -> DynamicWorld:
    """The dynamic search's world, with each candidate's throughput and verdict drawn."""
    return DynamicWorld(
        planner=[plan_reply(*(implement(f"h{n}") for n in range(hypotheses)))],
        implementer=[implemented() for _ in range(8)],
        judge=[reviewed(passed=passes[n % len(passes)]) for n in range(8)],
        throughputs=throughputs,
        **settings,
    )


@st.composite
def _dynamic_world(draw: st.DrawFn, settings: Mapping[str, Any]) -> Callable[[], World]:
    hypotheses = draw(st.integers(min_value=1, max_value=2))
    throughputs = tuple(draw(st.lists(st.sampled_from(_THROUGHPUTS), min_size=1, max_size=4)))
    passes = tuple(draw(st.lists(st.booleans(), min_size=1, max_size=3)))
    return lambda: _dynamic(hypotheses, throughputs, passes, settings)


def dynamic_subject(name: str, settings: Mapping[str, Any], *, sweeps_crashes: bool) -> Subject:
    """The dynamic search under the config ``settings``."""
    setup = DynamicWorld(**settings).setup
    return Subject(
        name=name,
        launch=setup.new_launch,
        harness=setup.harness,
        schemas=setup.schemas,
        worlds=_dynamic_world(settings),
        default_world=lambda: _dynamic(2, (120.0, 80.0), (True,), settings),
        smallest_world=lambda: _dynamic(1, (120.0,), (True,), settings),
        snapshot=RunSnapshot,
        sweeps_crashes=sweeps_crashes,
        issued=_ISSUED,
        start_budget=DynamicWorld(**settings).config.start_budget,
    )


def _evolve(
    throughputs: tuple[float | None, ...], passes: tuple[bool, ...], settings: Mapping[str, Any]
) -> EvolveWorld:
    """The evolve search's world, with each candidate's throughput and verdict drawn."""
    return EvolveWorld(
        mutator=[mutated() for _ in range(8)],
        judge=[evolve_reviewed(passed=passes[n % len(passes)]) for n in range(8)],
        throughputs=throughputs,
        **settings,
    )


@st.composite
def _evolve_world(draw: st.DrawFn, settings: Mapping[str, Any]) -> Callable[[], World]:
    throughputs = tuple(draw(st.lists(st.sampled_from(_THROUGHPUTS), min_size=1, max_size=4)))
    passes = tuple(draw(st.lists(st.booleans(), min_size=1, max_size=3)))
    return lambda: _evolve(throughputs, passes, settings)


def evolve_subject(name: str, settings: Mapping[str, Any], *, sweeps_crashes: bool) -> Subject:
    """The evolve search under the config ``settings``."""
    setup = EvolveWorld(**settings).setup
    return Subject(
        name=name,
        launch=setup.new_launch,
        harness=setup.harness,
        schemas=setup.schemas,
        worlds=_evolve_world(settings),
        default_world=lambda: _evolve((120.0, 80.0), (True,), settings),
        smallest_world=lambda: _evolve((120.0,), (True,), settings),
        snapshot=EvolveSnapshot,
        sweeps_crashes=sweeps_crashes,
        issued=_ISSUED,
        start_budget=EvolveWorld(**settings).config.total_attempts,
    )


SUBJECTS = {
    subject.name: subject
    for subject in (
        dynamic_subject("dynamic", {"max_in_flight": 2}, sweeps_crashes=False),
        dynamic_subject("dynamic-serial", {"max_in_flight": 1}, sweeps_crashes=True),
        evolve_subject(
            "evolve", {"max_parallelism": 2, "children_per_generation": 2}, sweeps_crashes=False
        ),
        # No crash sweep: a restart is a lease late, and a judge turn that lapsed while no host
        # was up comes back timed out, which fails that child by design (a review is not retried
        # by the search). Core cannot tell a lapse from silence there (`RESOLUTIONS` in
        # `vs_core/_waits.py`); the flow stories restart after every step instead.
        evolve_subject("evolve-serial", {"bootstrap_max_attempts": 1}, sweeps_crashes=False),
    )
}
