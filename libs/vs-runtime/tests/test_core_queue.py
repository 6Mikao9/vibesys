"""Mixed public inputs share the same durable order and search callback cursor."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from hypothesis import given
from hypothesis import strategies as st

from vs_core.api import (
    ArtifactId,
    ArtifactRef,
    ClockAdvanced,
    ControlId,
    ControlInput,
    Decision,
    Event,
    EventKind,
    InputId,
    RunControlEvent,
    RunFacts,
    RunView,
    Scope,
    ScopeInputTarget,
    SessionInput,
    SessionInputReceived,
    TimeReached,
    WakeAt,
    WorkKind,
    initial_state,
)
from vs_project.api import FakeStateStore, Project, StoredEnvelope
from vs_runtime.api.core import CoreRuntime, CoreRuntimeBindings, RuntimeRecord
from vs_runtime.api.core_shell_fixtures import CounterSearch, CounterState, ShellTraceTransitions

if TYPE_CHECKING:
    from pathlib import Path

    from vs_project.api import StateStore


class QueueInput(StrEnum):
    CLOCK = "clock"
    CONTROL = "control"
    OCCURRENCE = "occurrence"
    TICK = "tick"


class _TickingSearch(CounterSearch):
    """A search that, whenever it is woken at time t, asks to be woken again at t + 1."""

    def __init__(self) -> None:
        super().__init__()
        self.declaration = self.declaration.model_copy(
            update={
                "uses": frozenset({WorkKind.WAKE_AT}),
                "handles": self.declaration.handles | {EventKind.TIME_REACHED},
            }
        )

    def init(self, config: object, facts: RunFacts) -> tuple[CounterState, list[Decision]]:
        state, _ = super().init(config, facts)
        return state, [WakeAt(time=1)]

    def update(
        self, state: CounterState, event: Event, view: RunView
    ) -> tuple[CounterState, list[Decision]]:
        state, _ = super().update(state, event, view)
        if isinstance(event, TimeReached):
            return state, [WakeAt(time=event.time + 1)]
        return state, []


def ticking_runtime(store: StateStore) -> CoreRuntime[CounterState]:
    core = initial_state()
    declared = core.model_copy(
        update={"run": core.run.model_copy(update={"declaration": _TickingSearch().declaration})}
    )
    return CoreRuntime(
        store,
        _TickingSearch(),
        declared,
        config=None,
        bindings=CoreRuntimeBindings(transitions=ShellTraceTransitions()),
    )


def occurrence(index: int) -> SessionInputReceived:
    return SessionInputReceived(
        input=SessionInput(
            input_id=InputId(root=f"input-{index}"),
            target=ScopeInputTarget(scope=Scope(owner=initial_state().run.run_id, generation=0)),
            artifact=ArtifactRef(
                artifact_id=ArtifactId(root="shared-content"), digest="same-content"
            ),
            received_at=index,
            sequence=index,
        )
    )


@given(kinds=st.lists(st.sampled_from(list(QueueInput)), min_size=1, max_size=20))
def test_mixed_queue_commits_input_occurrences_controls_and_wakes_in_order(
    kinds: list[QueueInput],
) -> None:
    store = FakeStateStore()
    shell = ticking_runtime(store)
    shell.start("first", now_at=0, lease_duration=100)
    initial = shell.record
    for index, kind in enumerate(kinds, start=1):
        match kind:
            case QueueInput.CLOCK:
                shell.submit(ClockAdvanced(now_at=index), now_at=index)
            case QueueInput.CONTROL:
                shell.submit(
                    RunControlEvent(
                        control=ControlInput(
                            control_id=ControlId(root=f"control-{index}"), action="steer"
                        ),
                        now_at=index,
                    ),
                    now_at=index,
                )
            case QueueInput.OCCURRENCE:
                shell.submit(occurrence(index), now_at=index)
            case QueueInput.TICK:
                shell.flush(now_at=index)
    assert shell.record == initial
    callbacks = controls = inputs = ticks = events = 0
    for index, kind in enumerate(kinds, start=1):
        assert shell.advance()
        callbacks += kind in (QueueInput.CLOCK, QueueInput.CONTROL, QueueInput.TICK)
        events += kind in (QueueInput.CLOCK, QueueInput.CONTROL)
        controls += kind == QueueInput.CONTROL
        inputs += kind == QueueInput.OCCURRENCE
        ticks += kind == QueueInput.TICK
        assert shell.storage_revision == index
        assert shell.record.envelope.core.revision == index + 1 - ticks
        assert shell.record.envelope.strategy.callbacks == callbacks
        assert len(shell.record.envelope.core.run.controls) == controls
        assert len(shell.record.envelope.core.sessions.inputs) == inputs
        assert shell.record.envelope.event_cursor.sequence == events
        stored = store.load()
        assert isinstance(stored, StoredEnvelope)
        assert RuntimeRecord[CounterState].model_validate_json(stored.payload) == shell.record
    assert not shell.advance()
    before_inputs = shell.record.envelope.core.sessions.inputs
    restarted = ticking_runtime(store)
    restarted.start("second", now_at=100, lease_duration=100)
    assert restarted.record.envelope.core.sessions.inputs == before_inputs
    for row in before_inputs:
        restarted.submit(SessionInputReceived(input=row.input), now_at=100)
        assert restarted.advance()
    assert restarted.record.envelope.core.sessions.inputs == before_inputs


def test_disk_reopen_loads_identical_complete_runtime_record(tmp_path: Path) -> None:
    project = Project.open(tmp_path)
    store = project.state_store("run")
    shell = ticking_runtime(store)
    shell.start("first", now_at=0, lease_duration=10)
    shell.submit(ClockAdvanced(now_at=1), now_at=1)
    shell.submit(occurrence(2), now_at=2)
    shell.flush(now_at=3)
    while shell.advance():
        pass
    reopened = Project.open(tmp_path).state_store("run")
    stored = reopened.load()
    assert isinstance(stored, StoredEnvelope)
    assert RuntimeRecord[CounterState].model_validate_json(stored.payload) == shell.record
    next_shell = ticking_runtime(reopened)
    next_shell.start("second", now_at=10, lease_duration=10)
    assert next_shell.record.envelope.strategy == shell.record.envelope.strategy
    assert (
        next_shell.record.envelope.core.sessions.inputs
        == shell.record.envelope.core.sessions.inputs
    )
