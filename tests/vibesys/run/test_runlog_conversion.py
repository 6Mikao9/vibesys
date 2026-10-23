"""Conversion is atomic and idempotent over a real journal and temporary home."""

from __future__ import annotations

import gzip
import json
import os
import runpy
import shutil
from contextlib import closing
from dataclasses import dataclass
from functools import partial
from importlib.metadata import version
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from vibesys.api.transport import (
    ConversionOptions,
    ConversionStatus,
    convert_run,
    convert_runs,
    iter_conversions,
    prelock_writer_is_live,
)
from vibesys.events import (
    AgentOutputChunkData,
    CoreEvent,
    CoreEventType,
    RunStartedData,
    SpanStartedData,
)
from vibesys.records.api import MappingState, map_event
from vibesys.run import EventJournal
from vibesys.run.runlog_tee import RunLogTee
from vs_core.api import ENVELOPE_SCHEMA_VERSION, FlowState, RunEnvelope, Span, SpanStatus, SpanType
from vs_core.api.testing import FAKE_REGISTRY
from vs_faults.api import Boundary, FaultGate, FaultPlan, FaultRule, HostCrashError, HostFault
from vs_project.api import Project, StoredEnvelope
from vs_proto.api import (
    API_SCHEMA_HASH,
    RUN_LOG_MAGIC,
    STORE_SCHEMA_HASH,
    EpochStarted,
    Keyframe,
    LegacyEvent,
    Oneof,
    Record,
    RunConversionReceipt,
    RunHead,
)
from vs_runlog.api import (
    FakeFileSync,
    FileRunContent,
    FileRunLogWriter,
    RunLock,
    RunLogReader,
    encode_frame,
    fold_skeleton,
)
from vs_sim.api.testing import ManualClock, SimThreads, examples


def journal_at(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clock_start: float = 100
) -> tuple[Project, EventJournal]:
    """Build a JSONL-only run, as an older checkout would have written."""
    monkeypatch.setenv("VIBESYS_STATE_HOME", str(tmp_path / "home"))
    root = tmp_path / "project"
    root.mkdir()
    project = Project.open(root)
    journal = EventJournal(ManualClock(start=clock_start), SimThreads())
    journal.attach(Project.log_directory_for(root, "run-1"), "run-1")
    (Project.log_directory_for(root, "run-1") / "run.lock").touch()
    return project, journal


def record_trace(journal: EventJournal) -> None:
    """Record a real flow span rather than a placeholder trace event."""
    journal.emit(
        CoreEventType.SPAN_STARTED,
        data=SpanStartedData(
            span=Span(
                id="flow:run#1",
                parent=None,
                type=SpanType.FLOW,
                name="run",
                status=SpanStatus.RUNNING,
                started_at=0.0,
            )
        ),
    )


def test_conversion_cannot_replace_a_live_writers_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, journal = journal_at(tmp_path, monkeypatch)
    record_trace(journal)
    source = Project.event_journal_path_for(project.root, "run-1")
    journal.emit(
        CoreEventType.AGENT_OUTPUT_CHUNK,
        data=AgentOutputChunkData(channel="assistant", content="recorded output"),
    )
    original = source.read_bytes()
    destination = Project.run_log_path_for(project.root, "run-1")
    with closing(RunLock(destination.parent / "run.lock")):
        result = convert_run(
            project, "run-1", clock=ManualClock(), threads=SimThreads(), install_sync=FakeFileSync()
        )
    assert result.status is ConversionStatus.SKIPPED
    assert "live" in result.reason
    assert not destination.exists()
    assert source.read_bytes() == original
    assert not (destination.parent / "streams").exists()


@pytest.mark.parametrize("clock_start", [-0.5, 0.0, 100.25])
def test_conversion_preserves_source_and_timestamps_and_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clock_start: float
) -> None:
    project, journal = journal_at(tmp_path, monkeypatch, clock_start)
    journal.emit(
        CoreEventType.RUN_STARTED,
        "start",
        data=RunStartedData(outer_loop="single-agent", input="input"),
    )
    record_trace(journal)
    journal.emit(CoreEventType.RUN_FINISHED, "finish")
    source = Project.event_journal_path_for(project.root, "run-1")
    original = source.read_bytes()
    assert (
        convert_run(
            project, "run-1", clock=ManualClock(), threads=SimThreads(), install_sync=FakeFileSync()
        ).status
        is ConversionStatus.CONVERTED
    )
    destination = Project.run_log_path_for(project.root, "run-1")
    converted = destination.read_bytes()
    head = RunHead.from_binary((destination.parent / "run.head").read_bytes())
    keyframe = Keyframe.from_binary(
        (destination.parent / "keyframes" / f"{head.committed_seq}.pb").read_bytes()
    )
    assert keyframe.through_seq == head.committed_seq
    assert keyframe.log_offset == len(converted)
    with destination.open("rb") as stream:
        records = list(RunLogReader(stream))
    assert [record.seq for record in records] == [1, 2, 3, 4, 5]
    assert records[0].body is not None
    assert records[0].body.field == "epoch_started"
    assert all(
        record.at is not None and record.at.to_datetime() == journal.read()[0].timestamp
        for record in records
    )
    assert [record.body.field for record in records if record.body is not None] == [
        "epoch_started",
        "lifecycle",
        "span",
        "span",
        "lifecycle",
    ]
    assert (
        convert_run(
            project, "run-1", clock=ManualClock(), threads=SimThreads(), install_sync=FakeFileSync()
        ).status
        is ConversionStatus.EXISTS
    )
    assert destination.read_bytes() == converted
    assert source.read_bytes() == original
    assert sorted(path.name for path in destination.parent.iterdir()) == [
        "blobs",
        "convert.lock",
        "core-events.jsonl",
        "keyframes",
        "run.conversion-source.pb",
        "run.head",
        "run.lock",
        "run.log",
    ]


@pytest.mark.parametrize("with_old_event", [False, True])
def test_empty_and_pretrace_journals_are_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, with_old_event: bool
) -> None:
    project, journal = journal_at(tmp_path, monkeypatch)
    if with_old_event:
        journal.emit(CoreEventType.RUN_STARTED)
    else:
        Project.event_journal_path_for(project.root, "run-1").touch()
    result = convert_run(
        project, "run-1", clock=ManualClock(), threads=SimThreads(), install_sync=FakeFileSync()
    )
    assert result.status is ConversionStatus.SKIPPED
    assert ("predates" if with_old_event else "no events") in result.reason
    assert not Project.run_log_path_for(project.root, "run-1").exists()


def test_conversion_replaces_torn_destination_only_after_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, journal = journal_at(tmp_path, monkeypatch)
    record_trace(journal)
    destination = Project.run_log_path_for(project.root, "run-1")
    torn = RUN_LOG_MAGIC + b"\x80"
    destination.write_bytes(torn)
    source = Project.event_journal_path_for(project.root, "run-1")
    original = source.read_bytes()
    with source.open("ab") as stream:
        stream.write(b"{malformed}")
    with pytest.raises(ValueError, match="journal event"):
        convert_run(
            project, "run-1", clock=ManualClock(), threads=SimThreads(), install_sync=FakeFileSync()
        )
    assert destination.read_bytes() == torn
    source.write_bytes(original)
    assert (
        convert_run(
            project, "run-1", clock=ManualClock(), threads=SimThreads(), install_sync=FakeFileSync()
        ).status
        is ConversionStatus.CONVERTED
    )


def test_corrupt_destination_is_reported_without_overwriting_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, journal = journal_at(tmp_path, monkeypatch)
    record_trace(journal)
    destination = Project.run_log_path_for(project.root, "run-1")
    destination.write_bytes(b"BADMAGIC")
    with pytest.raises(ValueError, match="byte 0"):
        convert_run(
            project, "run-1", clock=ManualClock(), threads=SimThreads(), install_sync=FakeFileSync()
        )
    assert destination.read_bytes() == b"BADMAGIC"


def test_conversion_upgrades_legacy_records_and_externalizes_large_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, journal = journal_at(tmp_path, monkeypatch)
    record_trace(journal)
    content = "\u2603" * 400000
    journal.emit(
        CoreEventType.AGENT_OUTPUT_CHUNK,
        data=AgentOutputChunkData(channel="assistant", content=content),
        execution_id="turn-a",
    )
    destination = Project.run_log_path_for(project.root, "run-1")
    with FileRunLogWriter(
        destination, clock=ManualClock(), threads=SimThreads(), sync=FakeFileSync()
    ) as writer:
        writer.append(Record(seq=1, epoch=1, body=Oneof("epoch_started", EpochStarted())))
        writer.append(Record(seq=2, epoch=1, body=Oneof("legacy_event", LegacyEvent(json="old"))))
    original = Project.event_journal_path_for(project.root, "run-1").read_bytes()
    assert (
        convert_run(
            project, "run-1", clock=ManualClock(), threads=SimThreads(), install_sync=FakeFileSync()
        ).status
        is ConversionStatus.CONVERTED
    )
    with destination.open("rb") as stream:
        records = list(RunLogReader(stream))
    assert all(
        record.body is not None and record.body.field != "legacy_event" for record in records
    )
    attachment = next(
        record.body.value
        for record in records
        if record.body is not None and record.body.field == "blob"
    )
    assert attachment.blob is not None
    path = Project.run_blobs_directory_for(project.root, "run-1") / attachment.blob.sha256
    assert path.read_bytes() == content.encode()
    assert Project.event_journal_path_for(project.root, "run-1").read_bytes() == original
    assert (
        convert_run(
            project, "run-1", clock=ManualClock(), threads=SimThreads(), install_sync=FakeFileSync()
        ).status
        is ConversionStatus.EXISTS
    )


@pytest.mark.parametrize("empty_header", [False, True])
def test_conversion_repairs_a_tee_with_no_event_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, empty_header: bool
) -> None:
    project, journal = journal_at(tmp_path, monkeypatch)
    record_trace(journal)
    destination = Project.run_log_path_for(project.root, "run-1")
    with FileRunLogWriter(
        destination, clock=ManualClock(), threads=SimThreads(), sync=FakeFileSync()
    ) as writer:
        if empty_header:
            writer.append(Record(seq=1, epoch=1, body=Oneof("epoch_started", EpochStarted())))
    assert (
        convert_run(
            project, "run-1", clock=ManualClock(), threads=SimThreads(), install_sync=FakeFileSync()
        ).status
        is ConversionStatus.CONVERTED
    )
    with destination.open("rb") as stream:
        records = list(RunLogReader(stream))
    assert any(record.body is not None and record.body.field == "span" for record in records)


@pytest.mark.regression
@given(
    names=st.lists(st.text(alphabet="abcXYZ012", min_size=1, max_size=12), min_size=1, max_size=6)
)
@example(names=["last-span"])
def test_conversion_fold_and_every_keyframe_suffix_equal_the_typed_mapping(
    names: list[str],
) -> None:
    with TemporaryDirectory() as temporary, pytest.MonkeyPatch.context() as patch:
        project, journal = journal_at(Path(temporary), patch)
        journal.emit(
            CoreEventType.RUN_STARTED,
            data=RunStartedData(outer_loop="single-agent", input="input"),
        )
        for i, name in enumerate(names):
            journal.emit(
                CoreEventType.SPAN_STARTED,
                data=SpanStartedData(
                    span=Span(
                        id=f"flow:{i}",
                        name=name,
                        parent=None,
                        type=SpanType.FLOW,
                        status=SpanStatus.RUNNING,
                        started_at=float(i),
                    )
                ),
            )
        source = Project.event_journal_path_for(project.root, "run-1")
        before = source.read_bytes()
        assert (
            convert_run(
                project,
                "run-1",
                clock=ManualClock(),
                threads=SimThreads(),
                install_sync=FakeFileSync(),
            ).status
            is ConversionStatus.CONVERTED
        )
        destination = Project.run_log_path_for(project.root, "run-1")
        with destination.open("rb") as stream:
            records = list(RunLogReader(stream))
        state = MappingState(
            "run-1",
            vibesys_version=version("vibesys"),
            wall_span_clock=True,
            clock_epochs=((journal.read()[0].timestamp.timestamp(), 0),),
        )
        expected = []
        for event in journal.read():
            mapped = map_event(state, event)
            expected.extend(mapped.records)
            state = mapped.state
        size = destination.stat().st_size
        full = fold_skeleton(records, log_offset=size)
        assert full == fold_skeleton(expected, log_offset=size)
        checkpoints = sorted((destination.parent / "keyframes").glob("*.pb"))
        assert checkpoints
        assert (destination.parent / "keyframes" / f"{records[-1].seq}.pb").exists()
        for path in checkpoints:
            seed = Keyframe.from_binary(path.read_bytes())
            with destination.open("rb") as stream:
                suffix = list(RunLogReader(stream, offset=seed.log_offset))
            assert fold_skeleton(suffix, seed=seed, log_offset=size) == full
        files = {
            p.relative_to(destination.parent): p.read_bytes()
            for p in destination.parent.rglob("*")
            if p.is_file()
        }
        assert (
            convert_run(
                project,
                "run-1",
                clock=ManualClock(),
                threads=SimThreads(),
                install_sync=FakeFileSync(),
            ).status
            is ConversionStatus.EXISTS
        )
        assert {
            p.relative_to(destination.parent): p.read_bytes()
            for p in destination.parent.rglob("*")
            if p.is_file()
        } == files
        assert source.read_bytes() == before


@pytest.mark.parametrize("missing", ["head", "keyframes", "both"])
def test_conversion_repairs_old_complete_log_without_rewriting_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    project, journal = journal_at(tmp_path, monkeypatch)
    journal.emit(
        CoreEventType.RUN_STARTED, data=RunStartedData(outer_loop="single-agent", input="input")
    )
    record_trace(journal)
    journal.emit(CoreEventType.RUN_FINISHED)
    convert_run(
        project, "run-1", clock=ManualClock(), threads=SimThreads(), install_sync=FakeFileSync()
    )
    directory = Project.log_directory_for(project.root, "run-1")
    original = (directory / "run.log").read_bytes()
    if missing in {"head", "both"}:
        (directory / "run.head").unlink()
    if missing in {"keyframes", "both"}:
        for path in (directory / "keyframes").iterdir():
            path.unlink()
    result = convert_run(
        project, "run-1", clock=ManualClock(), threads=SimThreads(), install_sync=FakeFileSync()
    )
    assert result.status is ConversionStatus.CONVERTED
    assert "repaired" in result.reason
    head = RunHead.from_binary((directory / "run.head").read_bytes())
    seed = Keyframe.from_binary((directory / "keyframes" / f"{head.committed_seq}.pb").read_bytes())
    assert seed.log_offset == len(original)
    assert (directory / "run.log").read_bytes() == original
    assert (
        convert_run(
            project, "run-1", clock=ManualClock(), threads=SimThreads(), install_sync=FakeFileSync()
        ).status
        is ConversionStatus.EXISTS
    )


@pytest.mark.parametrize(
    "path",
    sorted((Path(__file__).parents[3] / "libs/vs-core/tests/envelopes").glob("v*-*.json")),
    ids=lambda p: p.stem,
)
def test_phase_two_conversion_preserves_saved_core_envelopes_and_their_resume_migrations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: Path
) -> None:
    """Golden saves remain resumable; phase 2 never invents a state.pb commit fence."""
    project, journal = journal_at(tmp_path, monkeypatch)
    record_trace(journal)
    envelope = path.read_text()
    # The store owns an opaque runtime payload, not the core schema version.
    payload = json.dumps({"schema_version": 1, "envelope": json.loads(envelope)})
    record = StoredEnvelope(revision=4, schema_version=1, payload=payload.encode())
    original = json.dumps({"version": 1, "record": record.model_dump(mode="json")}).encode()
    namespace = project.state.state_store_namespace("run-1")
    namespace.write_bytes("store.json", original)
    result = convert_run(
        project, "run-1", clock=ManualClock(), threads=SimThreads(), install_sync=FakeFileSync()
    )
    assert result.status is ConversionStatus.CONVERTED
    assert "state.pb conversion is phase 3" in result.reason
    assert namespace.read_bytes("store.json") == original
    assert project.stored_record("run-1") == record
    decoded = FAKE_REGISTRY.decode_envelope(RunEnvelope[FlowState], envelope)
    assert decoded.schema_version == ENVELOPE_SCHEMA_VERSION
    assert not (Project.log_directory_for(project.root, "run-1") / "state.pb").exists()


def test_bulk_reports_every_project_and_index_outcome_and_continues_after_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, journal = journal_at(tmp_path, monkeypatch)
    record_trace(journal)
    # Project source and its existing host index alias are both reported.
    Project.publish_run_for(project.root, "run-1")
    for run_id, content in {
        "bad": b"{malformed}",
        "empty": b"",
        "old": b'{"type":"retired_event","timestamp":"2026-01-01T00:00:00Z"}',
    }.items():
        directory = Project.log_directory_for(project.root, run_id)
        directory.mkdir(parents=True)
        (directory / "core-events.jsonl").write_bytes(content)
        (directory / "run.lock").touch()
    pretrace = EventJournal(ManualClock(start=100), SimThreads())
    pretrace.attach(Project.log_directory_for(project.root, "pretrace"), "pretrace")
    pretrace.emit(CoreEventType.RUN_FINISHED)
    (Project.log_directory_for(project.root, "pretrace") / "run.lock").touch()
    other = tmp_path / "other"
    other.mkdir()
    external = EventJournal(ManualClock(start=100), SimThreads())
    external.attach(Project.log_directory_for(other, "external"), "external")
    record_trace(external)
    (Project.log_directory_for(other, "external") / "run.lock").touch()
    external_link = Project.publish_run_for(other, "external")
    index = external_link.parent
    (index / "broken").symlink_to(tmp_path / "absent")
    (index / "unsupported").write_bytes(b"keep")
    rows = {
        row.run: row.result
        for row in convert_runs(
            project,
            clock=ManualClock(),
            threads=SimThreads(),
            include_host_index=True,
            install_sync=FakeFileSync(),
        )
    }
    assert rows["run-1"].status is ConversionStatus.CONVERTED
    assert rows["bad"].status is ConversionStatus.FAILED
    assert rows["empty"].status is ConversionStatus.SKIPPED
    assert rows["old"].status is ConversionStatus.CANNOT_CONVERT
    assert "unsupported journal event" in rows["old"].reason
    assert "predates" in rows["pretrace"].reason
    assert rows[str(external_link)].status is ConversionStatus.CONVERTED
    assert rows[str(index / "broken")].status is ConversionStatus.FAILED
    assert rows[str(index / "unsupported")].status is ConversionStatus.SKIPPED
    assert "already processed" in rows[str(index / "run-1")].reason
    assert len(rows) == 9
    second = {
        row.run: row.result
        for row in convert_runs(
            project,
            clock=ManualClock(),
            threads=SimThreads(),
            include_host_index=True,
            install_sync=FakeFileSync(),
        )
    }
    assert second["run-1"].status is ConversionStatus.SKIPPED
    assert second["run-1"].reason == "already converted"
    assert second[str(external_link)].reason == "already converted"
    assert (index / "unsupported").read_bytes() == b"keep"


def test_bulk_reports_a_store_only_run_as_unsupported_in_phase_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VIBESYS_STATE_HOME", str(tmp_path / "home"))
    root = tmp_path / "project"
    root.mkdir()
    project = Project.open(root)
    namespace = project.state.state_store_namespace("store-only")
    namespace.write_bytes("store.json", b"original core state")
    rows = convert_runs(
        project, clock=ManualClock(), threads=SimThreads(), install_sync=FakeFileSync()
    )
    assert len(rows) == 1
    assert rows[0].run == "store-only"
    assert rows[0].result.status is ConversionStatus.SKIPPED
    assert "state.pb" in rows[0].result.reason
    assert namespace.read_bytes("store.json") == b"original core state"


def test_conversion_header_records_the_live_schema_hashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The committed goldens pin these hashes, so this is where the live ones are checked."""
    project, journal = journal_at(tmp_path, monkeypatch)
    record_trace(journal)
    convert_run(
        project, "run-1", clock=ManualClock(), threads=SimThreads(), install_sync=FakeFileSync()
    )
    with Project.run_log_path_for(project.root, "run-1").open("rb") as stream:
        header = next(iter(RunLogReader(stream))).body
    assert header is not None
    assert header.field == "epoch_started"
    assert header.value.api_schema_hash == API_SCHEMA_HASH
    assert header.value.store_schema_hash == STORE_SCHEMA_HASH


def test_gateway_conversion_fixture_regenerates_identically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, _ = journal_at(tmp_path, monkeypatch)
    directory = Path(__file__).parents[3] / "proto/testdata/conversion"
    generated = runpy.run_path(str(directory / "generate.py"))["fixtures"](project)
    for name, payload in generated.items():
        saved = (directory / name).read_bytes()
        message = (
            f"proto/testdata/conversion/{name} differs; inspect the difference, then regenerate "
            "with: uv run --locked python proto/testdata/conversion/generate.py"
        )
        if name.endswith(".gz"):
            assert gzip.decompress(saved) == gzip.decompress(payload), message
        else:
            assert saved == payload, message


@pytest.mark.parametrize("damage", ["log-sequence", "keyframe-offset", "stale-head"])
def test_conversion_reports_or_repairs_damaged_derived_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, damage: str
) -> None:
    project, journal = journal_at(tmp_path, monkeypatch)
    journal.emit(
        CoreEventType.RUN_STARTED, data=RunStartedData(outer_loop="single-agent", input="input")
    )
    record_trace(journal)
    journal.emit(CoreEventType.RUN_FINISHED)
    convert_run(
        project, "run-1", clock=ManualClock(), threads=SimThreads(), install_sync=FakeFileSync()
    )
    directory = Project.log_directory_for(project.root, "run-1")
    head = RunHead.from_binary((directory / "run.head").read_bytes())
    if damage == "log-sequence":
        with (directory / "run.log").open("rb") as stream:
            records = list(RunLogReader(stream))
        (directory / "run.head").unlink()  # Unpublished legacy output requires a frame scan.
        records[-1].seq += 1
        (directory / "run.log").write_bytes(
            RUN_LOG_MAGIC + b"".join(encode_frame(r) for r in records)
        )
        with pytest.raises(ValueError, match="non-dense"):
            convert_run(
                project,
                "run-1",
                clock=ManualClock(),
                threads=SimThreads(),
                install_sync=FakeFileSync(),
            )
    elif damage == "keyframe-offset":
        path = directory / "keyframes" / f"{head.committed_seq}.pb"
        seed = Keyframe.from_binary(path.read_bytes())
        seed.log_offset += 1
        path.write_bytes(seed.to_binary())
        with pytest.raises(ValueError, match="checkpoint does not match"):
            convert_run(
                project,
                "run-1",
                clock=ManualClock(),
                threads=SimThreads(),
                install_sync=FakeFileSync(),
            )
    else:
        head.committed_seq -= 1
        (directory / "run.head").write_bytes(head.to_binary())
        assert (
            convert_run(
                project,
                "run-1",
                clock=ManualClock(),
                threads=SimThreads(),
                install_sync=FakeFileSync(),
            ).status
            is ConversionStatus.CONVERTED
        )
        assert (
            convert_run(
                project,
                "run-1",
                clock=ManualClock(),
                threads=SimThreads(),
                install_sync=FakeFileSync(),
            ).status
            is ConversionStatus.EXISTS
        )


def test_bulk_reports_index_publication_failure_and_preserves_the_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, journal = journal_at(tmp_path, monkeypatch)
    record_trace(journal)
    home = tmp_path / "home"
    (home / "runs").mkdir()
    (home / "runs/run-1").write_bytes(b"keep")
    results = convert_runs(
        project,
        clock=ManualClock(),
        threads=SimThreads(),
        include_host_index=True,
        install_sync=FakeFileSync(),
    )
    assert results[0].result.status is ConversionStatus.FAILED
    assert "not a symlink" in results[0].result.reason
    assert results[1].result.status is ConversionStatus.SKIPPED
    assert (home / "runs/run-1").read_bytes() == b"keep"


def test_typed_tee_explicit_commit_publishes_pending_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, journal = journal_at(tmp_path, monkeypatch)
    record_trace(journal)
    for _ in range(300):
        journal.emit(CoreEventType.FRAMEWORK_WARNING, text="progress")
    destination = Project.run_log_path_for(project.root, "run-1")
    writer = FileRunLogWriter(
        destination, clock=ManualClock(), threads=SimThreads(), sync=FakeFileSync()
    )
    content = FileRunContent(
        Project.run_blobs_directory_for(project.root, "run-1"),
        Project.log_streams_directory_for(project.root, "run-1"),
        sync=FakeFileSync(),
    )
    with RunLogTee(writer, content, "run-1") as tee:
        source = Project.event_journal_path_for(project.root, "run-1")
        for line in source.read_bytes().splitlines():
            tee.append(line)
        with destination.open("rb") as stream:
            records = list(RunLogReader(stream))
        checkpoint = destination.parent / "keyframes" / f"{records[-1].seq}.pb"
        assert not checkpoint.exists()
        tee.commit()
        assert Keyframe.from_binary(checkpoint.read_bytes()).through_seq == records[-1].seq


def test_conversion_crash_at_each_install_step_recovers_without_in_run_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, journal = journal_at(tmp_path, monkeypatch)
    journal.emit(
        CoreEventType.RUN_STARTED, data=RunStartedData(outer_loop="single-agent", input="input")
    )
    record_trace(journal)
    journal.close()
    destination = Project.run_log_path_for(project.root, "run-1")
    runs = destination.parent.parent
    source = Project.event_journal_path_for(project.root, "run-1")
    original = source.read_bytes()
    # Force replacement of older derived output, including the backup-keyframes step.
    (destination.parent / "keyframes").mkdir()
    (destination.parent / "keyframes" / "old.pb").write_bytes(b"old")
    real = FakeFileSync()
    prebuilt = tmp_path / "prebuilt"
    shutil.copytree(runs, prebuilt)
    for crash_at in range(1, 6):
        shutil.rmtree(runs)
        shutil.copytree(prebuilt, runs)
        gate = FaultGate(
            FaultPlan(
                seed=0,
                rules=(
                    FaultRule(
                        boundary=Boundary.DURABLE_WRITE,
                        target="install",
                        at=crash_at,
                        fault=HostFault.CRASH_AFTER,
                    ),
                ),
            )
        )
        images: list[set[str]] = []

        def sync(
            descriptor: int, /, *, images: list[set[str]] = images, gate: FaultGate = gate
        ) -> None:
            # Capture the crash image's names before Python's lexical cleanup can remove the stage.
            images.append({str(p.relative_to(runs)) for p in runs.rglob("*")})
            gate.around(Boundary.DURABLE_WRITE, "install", partial(real, descriptor))

        with pytest.raises(HostCrashError):
            convert_run(
                project, "run-1", clock=ManualClock(), threads=SimThreads(), install_sync=sync
            )
        assert len(images) == crash_at
        assert all(
            not any(part.startswith(".convert-run-") for part in Path(name).parts)
            for image in images
            for name in image
        )
        assert source.read_bytes() == original
        assert convert_run(
            project, "run-1", clock=ManualClock(), threads=SimThreads(), install_sync=FakeFileSync()
        ).status in {ConversionStatus.CONVERTED, ConversionStatus.EXISTS}
        head = RunHead.from_binary((destination.parent / "run.head").read_bytes())
        frame = Keyframe.from_binary(
            (destination.parent / "keyframes" / f"{head.committed_seq}.pb").read_bytes()
        )
        assert frame.log_offset == destination.stat().st_size
        assert not list(runs.rglob(".convert-*"))
        assert source.read_bytes() == original


def test_staging_stays_outside_run_even_during_preparation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, journal = journal_at(tmp_path, monkeypatch)
    record_trace(journal)
    journal.close()
    directory = Project.log_directory_for(project.root, "run-1")
    observed: list[Path] = []

    class InspectingClock(ManualClock):
        def now(self) -> float:
            assert not list(directory.parent.rglob(".convert-run-*"))
            observed.extend(directory.parent.parent.glob(".convert-run-1-*"))
            return super().now()

    result = convert_run(
        project, "run-1", clock=InspectingClock(), threads=SimThreads(), install_sync=FakeFileSync()
    )
    assert result.status is ConversionStatus.CONVERTED
    assert observed


def test_stale_stages_are_swept_only_with_exclusive_conversion_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, journal = journal_at(tmp_path, monkeypatch)
    record_trace(journal)
    journal.close()
    directory = Project.log_directory_for(project.root, "run-1")
    stale = directory / ".convert-run-stale"
    stale.mkdir()
    (stale / "previous-keyframes").mkdir()
    (stale / "previous-keyframes" / "old.pb").write_bytes(b"derived")
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    (directory / ".convert-run-link").symlink_to(unrelated, target_is_directory=True)
    with closing(RunLock(directory / "convert.lock")):
        assert (
            convert_run(
                project,
                "run-1",
                clock=ManualClock(),
                threads=SimThreads(),
                install_sync=FakeFileSync(),
            ).status
            is ConversionStatus.SKIPPED
        )
        assert stale.is_dir()
    assert (
        convert_run(
            project, "run-1", clock=ManualClock(), threads=SimThreads(), install_sync=FakeFileSync()
        ).status
        is ConversionStatus.CONVERTED
    )
    assert not stale.exists()
    assert unrelated.is_dir()
    assert (directory / ".convert-run-link").is_symlink()


def test_repeated_failed_installs_leave_no_temporary_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, journal = journal_at(tmp_path, monkeypatch)
    record_trace(journal)
    journal.close()
    directory = Project.log_directory_for(project.root, "run-1")

    def refused_sync(_descriptor: int, /) -> None:
        message = "installation refused"
        raise OSError(message)

    for _ in range(1):
        with pytest.raises(OSError, match="installation refused"):
            convert_run(
                project,
                "run-1",
                clock=ManualClock(),
                threads=SimThreads(),
                install_sync=refused_sync,
            )
        assert not list(directory.parent.parent.rglob(".convert-run-*"))
        assert (directory / "core-events.jsonl").is_file()


def test_final_refusal_retries_only_after_the_journal_revision_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, journal = journal_at(tmp_path, monkeypatch)
    journal.close()
    path = Project.event_journal_path_for(project.root, "run-1")
    fixture = Path(__file__).parent / "fixtures/pre-agent-identity.jsonl"
    path.write_bytes(fixture.read_bytes())
    assert (
        convert_run(
            project, "run-1", clock=ManualClock(), threads=SimThreads(), install_sync=FakeFileSync()
        ).status
        is ConversionStatus.CANNOT_CONVERT
    )
    event = json.loads(path.read_bytes())
    event["data"]["span"]["label"]["attrs_json"] = json.dumps(
        {"agent": "agent-a", "role": "implementer"}
    )
    path.write_text(json.dumps(event) + "\n")
    assert (
        convert_run(
            project, "run-1", clock=ManualClock(), threads=SimThreads(), install_sync=FakeFileSync()
        ).status
        is ConversionStatus.CONVERTED
    )
    assert not (path.parent / "run.conversion.pb").exists()


_MODES = [
    "stopped",
    "modern-live",
    "prelock-live",
    "prelock-future",
    "late-writer",
    "broken",
    "pretrace",
]
_LIVE_MODES = {"modern-live", "prelock-live", "prelock-future"}


@dataclass
class _Tree:
    """A generated project: one run per mode, each in the state a writer or a kill left it."""

    project: Project
    runs: Path
    modes: list[str]
    originals: dict[str, bytes]
    late_line: bytes
    locks: list[RunLock]

    def run_id(self, index: int) -> str:
        return f"run-{index}"

    def journal(self, run_id: str) -> Path:
        return Project.event_journal_path_for(self.project.root, run_id)


def _plant_tree(root: Path, env: pytest.MonkeyPatch, modes: list[str]) -> _Tree:
    project, journal = journal_at(root, env)
    journal.emit(
        CoreEventType.RUN_STARTED, data=RunStartedData(outer_loop="single-agent", input="input")
    )
    record_trace(journal)
    journal.emit(CoreEventType.FRAMEWORK_WARNING, text="late")
    journal.close()
    template = root / "template"
    directory = Project.log_directory_for(project.root, "run-1")
    shutil.copytree(directory, template)
    shutil.rmtree(directory.parent)
    *kept, late = (template / "core-events.jsonl").read_bytes().splitlines(keepends=True)
    tree = _Tree(project, directory.parent.parent, modes, {}, late, [])
    for index, mode in enumerate(modes):
        run_id = tree.run_id(index)
        run = Project.log_directory_for(project.root, run_id)
        shutil.copytree(template, run)
        source = run / "core-events.jsonl"
        if mode == "late-writer":
            source.write_bytes(b"".join(kept))
        if mode in {"prelock-live", "prelock-future"}:
            (run / "run.lock").unlink()
            modified = 999 if mode == "prelock-live" else 2000
            os.utime(source, (modified, modified))
        elif mode == "modern-live":
            tree.locks.append(RunLock(run / "run.lock"))
        elif mode == "broken":
            source.write_bytes(b"{broken}")
        elif mode == "pretrace":
            source.write_bytes(b"")
        tree.originals[run_id] = source.read_bytes()
    return tree


def _kill_one_pass(
    tree: _Tree, interruption: int, clock: ManualClock, threads: SimThreads, image: Path
) -> None:
    """Convert once; the kill at durability cut ``interruption`` leaves its exact crash image."""
    sync = FakeFileSync()
    calls = 0
    current = ""
    resumed: set[str] = set()
    crashed = False

    def interrupt(descriptor: int, /) -> None:
        nonlocal calls, crashed
        sync(descriptor)
        calls += 1
        if tree.modes[int(current[4:])] == "late-writer" and current not in resumed:
            # A pre-lock writer idle at the probe resumes while the converter works.
            resumed.add(current)
            with tree.journal(current).open("ab") as stream:
                stream.write(tree.late_line)
            os.utime(tree.journal(current), (998, 998))
        if calls == interruption and not crashed:
            crashed = True
            shutil.copytree(tree.runs, image)
            raise HostCrashError(Boundary.DURABLE_WRITE, "install", calls)

    for index, mode in enumerate(tree.modes):
        current = tree.run_id(index)
        try:
            result = convert_run(
                tree.project, current, clock=clock, threads=threads, install_sync=interrupt
            )
        except (HostCrashError, ValueError):
            continue
        if mode in _LIVE_MODES:
            assert result.status is ConversionStatus.SKIPPED
            assert not Project.run_log_path_for(tree.project.root, current).exists()
    if crashed:
        # OS death releases locks, but does not run TemporaryDirectory.__exit__.
        for lock in tree.locks:
            lock.close()
        tree.locks.clear()
        shutil.rmtree(tree.runs)
        shutil.copytree(image, tree.runs)


def _mapped_records(journal: Path) -> int:
    """The records the typed mapping makes of the journal as it is now."""
    events = [CoreEvent.model_validate_json(line) for line in journal.read_bytes().splitlines()]
    state = MappingState(
        "run",
        vibesys_version=version("vibesys"),
        wall_span_clock=True,
        clock_epochs=((events[0].timestamp.timestamp(), 0),),
    )
    count = 0
    for event in events:
        mapped = map_event(state, event)
        count += len(mapped.records)
        state = mapped.state
    return count


def _tree_bytes(tree: _Tree) -> dict[Path, bytes]:
    return {p: p.read_bytes() for p in tree.runs.rglob("*") if p.is_file()}


@pytest.mark.regression
@example(modes=["stopped", "prelock-future"], interruption=1, cancel_after=1)
@example(modes=["stopped"], interruption=3, cancel_after=0)
@example(modes=["late-writer"], interruption=30, cancel_after=0)
@settings(max_examples=examples(pr=3, nightly=100))
@given(
    modes=st.lists(st.sampled_from(_MODES), min_size=1, max_size=2),
    interruption=st.integers(min_value=1, max_value=30),
    cancel_after=st.integers(min_value=0, max_value=2),
)
def test_conversion_recovers_generated_run_trees_after_kill_cancel_and_writer_races(
    modes: list[str],
    interruption: int,
    cancel_after: int,
) -> None:
    """After any kill, writer timing and cancelled pass: no stage, each run converted once."""
    with TemporaryDirectory() as temporary, pytest.MonkeyPatch.context() as env:
        root = Path(temporary)
        tree = _plant_tree(root, env, modes)
        clock, threads, sync = ManualClock(start=1000), SimThreads(), FakeFileSync()
        try:
            _kill_one_pass(tree, interruption, clock, threads, root / "crash-image")
            for run_id, original in tree.originals.items():
                if tree.modes[int(run_id[4:])] != "late-writer":
                    assert tree.journal(run_id).read_bytes() == original
        finally:
            for lock in tree.locks:
                lock.close()
        clock.advance(2000)
        # The caller can cancel at any run boundary and then resume from a new pass.
        iterator = iter_conversions(
            tree.project, clock=clock, threads=threads, options=ConversionOptions(install_sync=sync)
        )
        for _ in range(cancel_after):
            next(iterator, None)
        iterator.close()
        rows = convert_runs(tree.project, clock=clock, threads=threads, install_sync=sync)
        assert {row.run for row in rows} == set(tree.originals)
        assert all(
            row.result.status
            in {ConversionStatus.CONVERTED, ConversionStatus.SKIPPED, ConversionStatus.FAILED}
            for row in rows
        )
        assert not list(tree.runs.rglob(".convert-*"))
        settled = _tree_bytes(tree)
        convert_runs(tree.project, clock=clock, threads=threads, install_sync=sync)
        assert _tree_bytes(tree) == settled
        for index, mode in enumerate(modes):
            run_id = tree.run_id(index)
            if mode != "late-writer":
                assert tree.journal(run_id).read_bytes() == tree.originals[run_id]
            if mode in {"broken", "pretrace"}:
                continue
            with Project.run_log_path_for(tree.project.root, run_id).open("rb") as stream:
                records = list(RunLogReader(stream))
            assert [r.seq for r in records] == list(range(1, len(records) + 1))
            assert {r.epoch for r in records} == {1}
            if mode in {"stopped", "late-writer", "prelock-live", "prelock-future", "modern-live"}:
                # A journal the converter saw whole, or a writer that finished, is fully mirrored.
                assert len(records) == _mapped_records(tree.journal(run_id))


@settings(max_examples=examples(pr=6, nightly=100))
@given(
    age=st.integers(min_value=-5000, max_value=5000),
    writer=st.sampled_from(["lock-held", "lock-free", "no-lock"]),
    ended=st.booleans(),
)
def test_liveness_is_decided_on_the_injected_clock_over_mtime_and_writer_state(
    age: int, writer: str, *, ended: bool
) -> None:
    """A held lock is live; else an ended journal is not; else only a journal quiet for 300 s."""
    with TemporaryDirectory() as temporary, pytest.MonkeyPatch.context() as env:
        project, journal = journal_at(Path(temporary), env)
        record_trace(journal)
        if ended:
            journal.emit(CoreEventType.RUN_FINISHED)
        journal.close()
        directory = Project.log_directory_for(project.root, "run-1")
        if writer == "no-lock":
            (directory / "run.lock").unlink()
        now = 10_000
        source = directory / "core-events.jsonl"
        os.utime(source, (now - age, now - age))
        lock = RunLock(directory / "run.lock") if writer == "lock-held" else None
        try:
            result = convert_run(
                project,
                "run-1",
                clock=ManualClock(start=now),
                threads=SimThreads(),
                install_sync=FakeFileSync(),
            )
        finally:
            if lock is not None:
                lock.close()
        quiet = prelock_writer_is_live(modified=now - age, now=now, ended=ended) is False
        live = writer == "lock-held" or (writer == "no-lock" and not quiet)
        assert (result.status is ConversionStatus.SKIPPED) == live
        assert prelock_writer_is_live(modified=now - age, now=now, ended=False) == (age < 300)


def test_a_log_of_an_older_projection_is_rebuilt_once_and_the_reason_is_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, journal = journal_at(tmp_path, monkeypatch)
    record_trace(journal)
    journal.close()
    directory = Project.log_directory_for(project.root, "run-1")
    os.utime(directory / "core-events.jsonl", (100, 100))
    kwargs = {
        "clock": ManualClock(start=1000),
        "threads": SimThreads(),
        "install_sync": FakeFileSync(),
    }
    assert convert_run(project, "run-1", **kwargs).status is ConversionStatus.CONVERTED
    receipt = directory / "run.conversion-source.pb"
    current = RunConversionReceipt.from_binary(receipt.read_bytes())
    # The same projection and the same journal: nothing is written, however often it is asked.
    settled = {p: p.read_bytes() for p in directory.rglob("*") if p.is_file()}
    for _ in range(3):
        assert convert_run(project, "run-1", **kwargs).status is ConversionStatus.EXISTS
    assert {p: p.read_bytes() for p in directory.rglob("*") if p.is_file()} == settled
    # A receipt of an older projection rebuilds the log, once, and says which projection moved.
    older = RunConversionReceipt(
        journal_size=current.journal_size,
        journal_modified_ns=current.journal_modified_ns,
        projection_version=current.projection_version - 1,
    )
    receipt.write_bytes(older.to_binary())
    rebuilt = convert_run(project, "run-1", **kwargs)
    assert rebuilt.status is ConversionStatus.CONVERTED
    assert (
        f"rebuilt: projection {older.projection_version} to {current.projection_version}"
        in rebuilt.reason
    )
    assert convert_run(project, "run-1", **kwargs).status is ConversionStatus.EXISTS
