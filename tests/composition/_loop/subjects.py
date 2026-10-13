"""The searches registered with the conformance suite.

A ported search adds one `Subject` here: its fresh-launch factory, its harness, its reply
schemas and a generator of scripted worlds. The properties themselves live in
`conformance.py` and are not edited per search.

The dynamic, evolve, issue-queue, single and multi searches are registered.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from hypothesis import strategies as st
from tests.composition._loop.conformance import Subject, World
from tests.composition._loop.dynamic_world import DynamicWorld
from tests.composition._loop.evolve_world import EvolveWorld
from tests.composition._loop.serial_world import (
    SerialWorld,
    multi_implemented,
    multi_reviewed,
    single_implemented,
)
from tests.support.evolve_script import mutated
from tests.support.evolve_script import reviewed as evolve_reviewed
from tests.vibesys.orchestration.dynamic._replies import (
    implement,
    implemented,
    plan_reply,
    reviewed,
)
from tests.vibesys.orchestration.issue_queue._replies import evaluated, judged
from tests.vibesys.orchestration.issue_queue._replies import implemented as issue_implemented
from tests.vibesys.orchestration.issue_queue.world import IssueQueueWorld
from tests.vibesys.serial_search._replies import plan_reply as serial_plan

from vibesys.hypothesis.state import HypothesisState
from vibesys.orchestration.dynamic.search.api import RunSnapshot
from vibesys.orchestration.evolve.search.api import EvolveSnapshot
from vibesys.orchestration.issue_queue.search.api import QueueSnapshot

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


def _issue_queue(
    verdicts: tuple[bool, ...], filed: tuple[int, ...], settings: Mapping[str, Any]
) -> IssueQueueWorld:
    """The issue queue's world: each review passes or fails as drawn, each evaluation files some issues."""
    return IssueQueueWorld(
        implementer=[issue_implemented(f"turn {n}") for n in range(12)],
        judge=[judged(passed=verdicts[n % len(verdicts)]) for n in range(12)],
        perf=[
            evaluated(*(f"issue {n}.{i}" for i in range(filed[n % len(filed)]))) for n in range(4)
        ],
        **settings,
    )


@st.composite
def _issue_queue_world(draw: st.DrawFn, settings: Mapping[str, Any]) -> Callable[[], World]:
    verdicts = tuple(draw(st.lists(st.booleans(), min_size=1, max_size=3)))
    filed = tuple(draw(st.lists(st.integers(min_value=0, max_value=2), min_size=1, max_size=3)))
    return lambda: _issue_queue(verdicts, filed, settings)


def issue_queue_subject(name: str, settings: Mapping[str, Any]) -> Subject:
    """The issue-queue search under the config ``settings``."""
    setup = IssueQueueWorld(**settings).setup
    return Subject(
        name=name,
        launch=setup.new_launch,
        harness=setup.harness,
        schemas=setup.schemas,
        worlds=_issue_queue_world(settings),
        default_world=lambda: _issue_queue((False, True), (1, 0), settings),
        smallest_world=lambda: IssueQueueWorld(**settings),
        sweeps_crashes=True,
        issued=_ISSUED,
        start_budget=IssueQueueWorld(**settings).config.max_jobs,
        snapshot=QueueSnapshot,
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
def _serial(
    search: str,
    designs: int,
    throughputs: tuple[float | None, ...],
    passes: tuple[bool, ...],
    settings: Mapping[str, Any],
) -> SerialWorld:
    """A serial search's world, with each candidate's throughput and verdict drawn."""
    rounds = settings["max_rounds"]
    if search == "single":
        implementer = [single_implemented(passed=passes[n % len(passes)]) for n in range(8)]
        judge = None
    else:
        implementer = [multi_implemented() for _ in range(8)]
        judge = [multi_reviewed(passed=passes[n % len(passes)]) for n in range(8)]
    return SerialWorld(
        search,
        designer=[serial_plan(f"h{n}") for n in range(max(designs, rounds))],
        implementer=implementer,
        judge=judge,
        throughputs=throughputs,
        **settings,
    )


@st.composite
def _evolve_world(draw: st.DrawFn, settings: Mapping[str, Any]) -> Callable[[], World]:
    throughputs = tuple(draw(st.lists(st.sampled_from(_THROUGHPUTS), min_size=1, max_size=4)))
    passes = tuple(draw(st.lists(st.booleans(), min_size=1, max_size=3)))
    return lambda: _evolve(throughputs, passes, settings)


def _children_lapsed(record: EvolveSnapshot) -> bool:
    """Whether a child of the published record ended because a turn lapsed while no host was up."""
    return any("timed-out" in item.feedback for item in record.population.individuals)


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
        lapsed=_children_lapsed,
        sweeps_crashes=sweeps_crashes,
        issued=_ISSUED,
        start_budget=EvolveWorld(**settings).config.total_attempts,
    )


@st.composite
def _serial_world(draw: st.DrawFn, search: str, settings: Mapping[str, Any]) -> Callable[[], World]:
    designs = draw(st.integers(min_value=1, max_value=2))
    throughputs = tuple(draw(st.lists(st.sampled_from(_THROUGHPUTS), min_size=1, max_size=4)))
    passes = tuple(draw(st.lists(st.booleans(), min_size=1, max_size=3)))
    return lambda: _serial(search, designs, throughputs, passes, settings)


def serial_subject(search: str, settings: Mapping[str, Any]) -> Subject:
    """The single or multi search under the config ``settings``."""
    world = SerialWorld(search, **settings)
    return Subject(
        name=search,
        launch=world.setup.new_launch,
        harness=world.setup.harness,
        schemas=world.setup.schemas,
        worlds=_serial_world(search, settings),
        default_world=lambda: _serial(search, 2, (120.0, 80.0), (True,), settings),
        smallest_world=lambda: _serial(search, 1, (120.0,), (True,), settings),
        snapshot=HypothesisState,
        sweeps_crashes=True,
        issued=_ISSUED,
        start_budget=world.config.max_rounds,
    )


SUBJECTS = {
    subject.name: subject
    for subject in (
        dynamic_subject("dynamic", {"max_in_flight": 2}, sweeps_crashes=False),
        dynamic_subject("dynamic-serial", {"max_in_flight": 1}, sweeps_crashes=True),
        evolve_subject(
            "evolve", {"max_parallelism": 2, "children_per_generation": 2}, sweeps_crashes=True
        ),
        evolve_subject("evolve-serial", {"bootstrap_max_attempts": 1}, sweeps_crashes=True),
        issue_queue_subject("plain", {}),
        serial_subject("single", {"max_rounds": 2}),
        serial_subject("multi", {"max_rounds": 2}),
    )
}
