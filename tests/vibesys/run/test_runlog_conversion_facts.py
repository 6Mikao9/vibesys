"""A converted run shows the facts the v1 reader projects from the same journal.

The run page and Usage read a converted run's start, end, duration, objective metric, best value,
cost and turns from its typed records. The property generates legacy journals (wall-clock span
times, an objective whose direction only the gate readings reveal, operations that carry readings
in ``op:`` spans, failed or absent terminal events) and compares each fact with the one the v1
projection (``RunSummaryFold`` and the journal's own events) gives.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import vibesys.events as e
import vs_proto.api as p
from server.host_api import RUN_EVENTS_FILE, RunDigest
from server.host_helper_os import FileProjects
from server.run_summary import RunSummaryFold
from vibesys.api.transport import ConversionStatus, convert_run
from vibesys.run import EventJournal
from vibesys.run.runlog_tee import RunLogTee
from vs_project.api import Project
from vs_project.api.testing import FakeGitRepositories
from vs_runlog.api import (
    FakeFileSync,
    FileRunContent,
    FileRunLogWriter,
    RunLogReader,
    fold_skeleton,
)
from vs_sim.api.testing import ManualClock, SimThreads, examples

_WALL = 1_760_000_000.0
_TOLERANCE = 1e-3


@dataclass(frozen=True)
class Reading:
    """One gate measurement in a legacy journal."""

    value: float
    direction: str


@dataclass(frozen=True)
class Turn:
    """One finished agent turn."""

    cost: float | None
    gap: float


@dataclass(frozen=True)
class Legacy:
    """A journal of an older checkout: the objective carries no direction of its own."""

    metric: str | None
    declared_direction: Literal["max", "min"] | None
    readings: tuple[Reading, ...]
    gate_span_prefix: str
    gate_events: bool
    input_reading: float
    turns: tuple[Turn, ...]
    gaps: tuple[float, ...]
    terminal: e.CoreEventType | None


_SECONDS = st.floats(min_value=0.5, max_value=5000, allow_nan=False, allow_infinity=False)
_DIRECTION: st.SearchStrategy[Literal["max", "min"]] = st.sampled_from(["max", "min"])


@st.composite
def legacy_journals(draw: st.DrawFn) -> Legacy:
    direction = draw(_DIRECTION)
    readings = draw(
        st.lists(
            st.builds(
                Reading,
                st.floats(min_value=0.25, max_value=1000, allow_nan=False, allow_infinity=False),
                st.just(direction),
            ),
            max_size=4,
        )
    )
    turns = draw(
        st.lists(
            st.builds(
                Turn,
                st.one_of(st.none(), st.floats(min_value=0, max_value=50, allow_nan=False)),
                _SECONDS,
            ),
            max_size=3,
        )
    )
    return Legacy(
        metric=draw(st.one_of(st.none(), st.sampled_from(["speedup", "latency_ms"]))),
        declared_direction=draw(st.one_of(st.none(), st.just(direction))),
        readings=tuple(readings),
        gate_span_prefix=draw(st.sampled_from(["op:gate", "op:measure"])),
        # The oldest journals report a reading by a gate event, with no gate span and no direction.
        gate_events=draw(st.booleans()),
        input_reading=draw(
            st.floats(min_value=0.25, max_value=1000, allow_nan=False, allow_infinity=False)
        ),
        turns=tuple(turns),
        gaps=tuple(
            draw(st.lists(_SECONDS, min_size=len(readings) + 1, max_size=len(readings) + 1))
        ),
        terminal=draw(
            st.sampled_from([e.CoreEventType.RUN_FINISHED, e.CoreEventType.RUN_FAILED, None])
        ),
    )


def _span(
    span_id: str, kind: str, started: float, attrs: dict[str, object], *, ended: float | None
) -> e.SpanStartedData:
    return e.SpanStartedData.model_validate(
        {
            "span": {
                "id": span_id,
                "parent": None,
                "type": "operation",
                "name": kind,
                "status": "done" if ended is not None else "running",
                "started_at": started,
                "ended_at": ended,
                "label": {"kind": kind, "attrs_json": json.dumps(attrs)},
            }
        }
    )


def _gate_event(journal: EventJournal, metric: str, value: float, *, execution: str | None) -> None:
    journal.emit(
        e.CoreEventType.GATE_FINISHED,
        data=e.GateFinishedData(gate="benchmark", metric=metric, value=value, unit="ops/s"),
        **({} if execution is None else {"execution_id": execution}),
    )


def _write(journal: EventJournal, clock: ManualClock, legacy: Legacy) -> None:
    overview = (
        None
        if legacy.metric is None
        else e.RunOverview(
            objective=e.ObjectiveOverview(
                text="optimize", metric=legacy.metric, direction=legacy.declared_direction
            )
        )
    )
    journal.emit(
        e.CoreEventType.RUN_STARTED,
        data=e.RunStartedData(outer_loop="single-agent", input="input", overview=overview),
    )
    journal.emit(
        e.CoreEventType.SPAN_STARTED,
        data=_span("flow:run#1", "flow", clock.now(), {}, ended=None),
    )
    if legacy.gate_events and legacy.metric is not None:
        # The input's gate names no span and no execution; a candidate's names its execution.
        _gate_event(journal, legacy.metric, legacy.input_reading, execution=None)
    for index, reading in enumerate(legacy.readings):
        clock.advance(legacy.gaps[index])
        started = clock.now()
        name = f"{legacy.gate_span_prefix}:{index}"
        if legacy.gate_events and legacy.metric is not None:
            _gate_event(journal, legacy.metric, reading.value, execution=f"vs-{index}")
        rows = (
            []
            if legacy.metric is None
            else [{"name": legacy.metric, "value": reading.value, "direction": reading.direction}]
        )
        attrs: dict[str, object] = {"stage": "benchmark", "metrics": rows}
        journal.emit(
            e.CoreEventType.SPAN_STARTED, data=_span(name, "gate", started, attrs, ended=None)
        )
        clock.advance(1)
        journal.emit(
            e.CoreEventType.SPAN_ENDED,
            data=e.SpanEndedData(span=_span(name, "gate", started, attrs, ended=clock.now()).span),
        )
    for index, turn in enumerate(legacy.turns):
        clock.advance(turn.gap)
        started = clock.now()
        name = f"op:turn-{index}"
        journal.emit(
            e.CoreEventType.SPAN_STARTED,
            data=_span(
                name, "agent_turn", started, {"agent": "agent-a", "role": "implementer"}, ended=None
            ),
            execution_id=f"turn-{index}",
        )
        clock.advance(1)
        journal.emit(
            e.CoreEventType.TURN_USAGE_RECORDED,
            data=e.TurnUsageData(input_tokens=10, total_cost_usd=turn.cost, span_id=name),
            execution_id=f"turn-{index}",
        )
        journal.emit(
            e.CoreEventType.SPAN_ENDED,
            data=e.SpanEndedData(
                span=_span(
                    name,
                    "agent_turn",
                    started,
                    {"agent": "agent-a", "role": "implementer"},
                    ended=clock.now(),
                ).span
            ),
            execution_id=f"turn-{index}",
        )
    clock.advance(legacy.gaps[-1])
    if legacy.terminal is not None:
        journal.emit(legacy.terminal)


def _best(legacy: Legacy) -> float | None:
    """The best reading of the run, the input's own (when a gate event reported it) included."""
    if legacy.metric is None or not legacy.readings:
        return None
    values = [reading.value for reading in legacy.readings]
    if legacy.gate_events:
        values.append(legacy.input_reading)
    return max(values) if legacy.readings[0].direction == "max" else min(values)


def _produce(root: Path, path: str, source: Path) -> Path:
    """Write ``run.log`` from the journal, by conversion or as the live mirror does."""
    project = Project.open(root)
    destination = Project.run_log_path_for(root, "run-1")
    if path == "conversion":
        os.utime(source, (100, 100))
        result = convert_run(
            project,
            "run-1",
            clock=ManualClock(start=1000),
            threads=SimThreads(),
            install_sync=FakeFileSync(),
        )
        assert result.status is ConversionStatus.CONVERTED
        return destination
    # The live mirror maps the same events one by one, as the running core writes them.
    writer = FileRunLogWriter(
        destination, clock=ManualClock(), threads=SimThreads(), sync=FakeFileSync()
    )
    content = FileRunContent(
        Project.run_blobs_directory_for(root, "run-1"),
        Project.log_streams_directory_for(root, "run-1"),
        sync=FakeFileSync(),
    )
    with RunLogTee(writer, content, "run-1", checkout=("", False)) as tee:
        for line in source.read_bytes().splitlines():
            tee.append(line)
        tee.commit()
    return destination


def _span_and_reading_clocks(state: p.Keyframe) -> list[float]:
    spans = [
        value
        for history in state.spans.values()
        for version in history.versions
        if version.span is not None
        for value in (version.span.started_at, version.span.ended_at)
        if value is not None
    ]
    readings = [
        version.point.clock
        for series in state.metrics
        for version in series.points
        if version.point is not None
    ]
    return spans + readings


def _check_input(head: p.RunHead, state: p.Keyframe, legacy: Legacy) -> None:
    """The input's reading is the objective's input value, and each gate event names its subject."""
    if not legacy.gate_events or legacy.metric is None:
        return
    subjects = [
        p.GateAttrs.from_binary(version.span.label.attrs.value).subject
        for history in state.spans.values()
        for version in history.versions[-1:]
        if version.span is not None
        and version.span.label is not None
        and version.span.label.kind == "gate"
        and version.span.label.attrs is not None
        and version.span.id.startswith("gate:")
    ]
    assert subjects.count(p.GateSubject.INPUT) == 1
    assert subjects.count(p.GateSubject.CANDIDATE) == len(legacy.readings)
    assert head.objective is not None
    assert head.objective.input_value == pytest.approx(legacy.input_reading)


def _check_row_reads_the_log(root: Path, head: p.RunHead) -> None:
    """The list row and the run page read one headline: the best, and its ratio to the input."""
    server_journal = Project.log_directory_for(root, "run-1") / RUN_EVENTS_FILE
    server_journal.write_bytes(Project.event_journal_path_for(root, "run-1").read_bytes())
    source = FileProjects(FakeGitRepositories().repository, SimThreads(), clock=ManualClock())
    digest = source.run_digest(root, "run-1")
    assert isinstance(digest, RunDigest)
    objective = head.objective
    if objective is None or not objective.has_field("best_value"):
        return
    assert digest.dashboard is not None
    assert digest.dashboard.best is not None
    assert digest.dashboard.best.value == objective.best_value
    if objective.has_field("input_value") and objective.input_value and objective.best_value:
        minimize = objective.series is not None and objective.series.direction == p.Direction.MIN
        want = (
            objective.input_value / objective.best_value
            if minimize
            else objective.best_value / objective.input_value
        )
        assert digest.adopted_ratio == pytest.approx(want)
    else:
        assert digest.adopted_ratio is None


def _check_objective(head: p.RunHead, legacy: Legacy) -> None:
    best = _best(legacy)
    if legacy.metric is None:
        assert head.objective is None
    elif best is not None:
        assert head.objective is not None
        series = head.objective.series
        assert series is not None
        assert series.name == legacy.metric
        assert series.direction == (
            p.Direction.MAX if legacy.readings[0].direction == "max" else p.Direction.MIN
        )
        assert head.objective.best_value == pytest.approx(best)


@pytest.mark.parametrize("path", ["conversion", "live-mirror"])
@settings(max_examples=examples(pr=4, nightly=100))
@given(legacy=legacy_journals())
def test_run_facts_equal_the_v1_projection_of_the_same_journal(path: str, legacy: Legacy) -> None:
    with TemporaryDirectory() as temporary, pytest.MonkeyPatch.context() as patch:
        patch.setenv("VIBESYS_STATE_HOME", str(Path(temporary) / "home"))
        root = Path(temporary) / "project"
        root.mkdir()
        clock = ManualClock(start=_WALL)
        journal = EventJournal(clock, SimThreads())
        journal.attach(Project.log_directory_for(root, "run-1"), "run-1")
        _write(journal, clock, legacy)
        source = Project.event_journal_path_for(root, "run-1")

        # The v1 projection: the shipped summary fold plus the journal's own events.
        summary = RunSummaryFold()
        for line in source.read_bytes().splitlines():
            assert summary.consume(line)
        v1 = summary.state()
        events = journal.read()
        started, ended = events[0].timestamp.timestamp(), events[-1].timestamp.timestamp()

        destination = _produce(root, path, source)
        with destination.open("rb") as stream:
            records = list(RunLogReader(stream))
        head = p.RunHead.from_binary((destination.parent / "run.head").read_bytes())
        state = fold_skeleton(records, log_offset=destination.stat().st_size)

        first, last = records[0].at, records[-1].at
        assert first is not None
        assert last is not None
        assert first.to_seconds() == pytest.approx(started, abs=_TOLERANCE)
        assert last.to_seconds() == pytest.approx(ended, abs=_TOLERANCE)
        assert records[-1].clock - records[0].clock == pytest.approx(
            ended - started, abs=_TOLERANCE
        )
        # No span or reading carries a wall time as its clock.
        clocks = _span_and_reading_clocks(state)
        assert all(0 <= value <= records[-1].clock + _TOLERANCE for value in clocks)

        costs = [record.cost_usd for record in v1.records if record.cost_usd is not None]
        assert len(v1.records) == len(legacy.turns)
        assert head.span_counts.get("agent_turn", 0) == len(v1.records)
        if costs:
            assert head.usage is not None
            assert head.usage.cost_usd == pytest.approx(sum(costs))
        _check_objective(head, legacy)
        _check_input(head, state, legacy)
        _check_row_reads_the_log(root, head)
