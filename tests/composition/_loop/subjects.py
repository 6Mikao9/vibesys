"""The searches registered with the conformance suite.

A ported search adds one `Subject` here: its fresh-launch factory, its harness, its reply
schemas and a generator of scripted worlds. The properties themselves live in
`conformance.py` and are not edited per search.

Only the dynamic search is registered. The other searches (evolve, issue queue, multi, single)
join as they are written as flows.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from hypothesis import strategies as st
from tests.composition._loop.conformance import Subject, World
from tests.composition._loop.dynamic_world import DynamicWorld
from tests.vibesys.orchestration.dynamic._replies import (
    implement,
    implemented,
    plan_reply,
    reviewed,
)

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
        sweeps_crashes=sweeps_crashes,
        issued=_ISSUED,
        start_budget=DynamicWorld(**settings).config.start_budget,
    )


SUBJECTS = {
    subject.name: subject
    for subject in (
        dynamic_subject("dynamic", {"max_in_flight": 2}, sweeps_crashes=False),
        dynamic_subject("dynamic-serial", {"max_in_flight": 1}, sweeps_crashes=True),
    )
}
