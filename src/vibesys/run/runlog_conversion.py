"""Stopped-run migration and complete, idempotent host-wide outcome reporting."""

from __future__ import annotations

import shutil
from contextlib import closing
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

from vibesys.events import CoreEvent, CoreEventType, RunStartedData
from vibesys.run.runlog_tee import JOURNAL_EVENT_MAX_BYTES, RunLogTee
from vs_decode.api import Unreadable, decode_json
from vs_project.api import Project, ProjectError, atomic_write_bytes
from vs_proto.api import (
    Direction,
    Keyframe,
    RunConversionReceipt,
    RunConversionRefusal,
    RunHead,
    validate,
)
from vs_runlog.api import (
    FileRunContent,
    FileRunLogWriter,
    FileSync,
    RunLock,
    RunLogReader,
    RunLogStopReason,
    install_run_log,
    read_run_head,
)

if TYPE_CHECKING:
    from collections.abc import Generator, Iterator

    from vs_sim.api import SleepingClock, Threads


_PRELOCK_QUIET_SECONDS = 300
# Bump with any change to what a converted run.log says (it is rebuilt once, and the receipt says so):
# 2: gate events of older journals name their subject (input or candidate).
_PROJECTION_VERSION = 2


class RunLogConversionError(ValueError):
    """A journal or destination cannot be safely converted."""


class _UnsupportedJournalError(RunLogConversionError):
    """The journal names a historical event kind this build cannot interpret."""


class ConversionStatus(StrEnum):
    """Terminal outcomes of an idempotent conversion."""

    CONVERTED = "converted"
    EXISTS = "exists"
    SKIPPED = "skipped"
    FAILED = "failed"
    CANNOT_CONVERT = "cannot-convert"


@dataclass(frozen=True)
class ConversionResult:
    """A human-reportable conversion outcome and its reason."""

    status: ConversionStatus
    reason: str


def _events(path: Path) -> Iterator[tuple[bytes, CoreEvent]]:
    with path.open("rb") as stream:
        while line := stream.readline(JOURNAL_EVENT_MAX_BYTES + 1):
            event = decode_json(
                line, CoreEvent, source=str(path), max_bytes=JOURNAL_EVENT_MAX_BYTES
            )
            if isinstance(event, Unreadable):
                if any(p.location == ("type",) and p.error_type == "enum" for p in event.problems):
                    message = f"unsupported journal event: {event.details()}"
                    raise _UnsupportedJournalError(message)
                raise RunLogConversionError(event.details("journal event"))
            yield line, event


def _complete_sequence(path: Path) -> int:
    if not path.exists():
        return 0
    # A published head and its final keyframe fence the committed prefix. Older
    # logs without either still use the complete frame scan and repair path.
    if (path.parent / "run.head").is_file():
        head = read_run_head(path)
        if head.committed_seq and _has_checkpoint(path.parent, head.committed_seq):
            return _checkpoint_sequence(path, head.committed_seq)
    with path.open("rb") as stream:
        reader = RunLogReader(stream)
        legacy = False
        typed = False
        sequence = 0
        for record in reader:
            if record.seq != sequence + 1:
                message = f"non-dense run.log sequence: {record.seq} after {sequence}"
                raise RunLogConversionError(message)
            sequence = record.seq
            body = record.body
            legacy |= body is not None and body.field == "legacy_event"
            typed |= body is not None and (
                body.field not in {"legacy_event", "epoch_started"}
                or (body.field == "epoch_started" and body.value.run is not None)
            )
    if reader.stop.reason is RunLogStopReason.CORRUPTION:
        message = f"corrupt run.log at byte {reader.stop.offset}: {reader.stop.detail}"
        raise ValueError(message)
    return sequence if reader.stop.reason is RunLogStopReason.END and not legacy and typed else 0


def _checkpoint_sequence(path: Path, sequence: int) -> int:
    """Validate only the last checkpoint suffix; repair older offline projections once."""
    keyframes = path.parent / "keyframes"
    final = Keyframe.from_binary((keyframes / f"{sequence}.pb").read_bytes())
    prior = max(
        (
            int(p.stem)
            for p in keyframes.glob("*.pb")
            if p.stem.isdigit() and int(p.stem) < sequence
        ),
        default=0,
    )
    offset = (
        Keyframe.from_binary((keyframes / f"{prior}.pb").read_bytes()).log_offset if prior else 0
    )
    with path.open("rb") as stream:
        first = next(iter(RunLogReader(stream)), None)
        reader = RunLogReader(stream, offset=offset)
        last = None
        for record in reader:
            if record.seq != prior + 1:
                message = f"non-dense run.log sequence: {record.seq} after {prior}"
                raise RunLogConversionError(message)
            prior, last = record.seq, record
    if reader.stop.reason is not RunLogStopReason.END or prior != sequence:
        message = f"checkpoint does not match run.log: {path}"
        raise RunLogConversionError(message)
    if first is not None and first.at is not None and last is not None and last.has_field("clock"):
        wall = first.at.to_seconds()
        if wall > last.clock and any(
            v.span is not None and v.span.started_at >= wall
            for h in final.spans.values()
            for v in h.versions
        ):
            return 0
    epoch = next((v.epoch for v in reversed(final.epochs) if v.epoch and v.epoch.run), None)
    if (
        epoch is not None
        and epoch.run is not None
        and not epoch.run.objectives
        and _has_legacy_objective(
            path, {s.name for s in epoch.series if s.direction in {Direction.MIN, Direction.MAX}}
        )
    ):
        return 0
    return sequence


def _has_legacy_objective(path: Path, declared: set[str]) -> bool:
    journal = path.with_name("core-events.jsonl")
    if not journal.is_file():
        return False
    for _, event in _events(journal):
        if isinstance(event.data, RunStartedData):
            overview = event.data.overview
            return bool(overview and overview.objective.metric in declared)
        if event.type in {CoreEventType.SPAN_STARTED, CoreEventType.SPAN_ENDED}:
            break
    return False


def _has_checkpoint(directory: Path, sequence: int) -> bool:
    path = directory / "keyframes" / f"{sequence}.pb"
    if not path.exists():
        return False
    frame = Keyframe.from_binary(path.read_bytes())
    validate(frame)
    if frame.through_seq != sequence or frame.log_offset != (directory / "run.log").stat().st_size:
        message = f"checkpoint does not match run.log: {path}"
        raise RunLogConversionError(message)
    needs_head = bool(frame.lifecycles and any(v.epoch and v.epoch.run for v in frame.epochs))
    head_path = directory / "run.head"
    if not head_path.exists():
        return not needs_head
    head = RunHead.from_binary(head_path.read_bytes())
    validate(head)
    return head.committed_seq == sequence


def convert_run(
    project: Project,
    run_id: str,
    *,
    clock: SleepingClock,
    threads: Threads,
    install_sync: FileSync | None = None,
) -> ConversionResult:
    """Convert one stopped project run; leave its journal and core store untouched.

    Pre-lock journals need five quiet minutes. Historical writers idle longer
    than that cannot be detected without an ownership record. Final mapping
    refusals are retained for the journal revision and displayed by discovery.

    Core envelopes remain the resume authority until phase 3 introduces state.pb.
    A read-path conversion needs only the trace journal, not strategy state codecs.
    """
    directory = project.log_directory_for(project.root, run_id)
    result = _convert_directory(
        directory, run_id, clock=clock, threads=threads, install_sync=install_sync
    )
    if result.status in {ConversionStatus.CONVERTED, ConversionStatus.EXISTS}:
        project.publish_run_for(project.root, run_id)
    if project.state.state_store_namespace(run_id).read_bytes("store.json") is not None:
        result = ConversionResult(
            result.status, result.reason + "; store.json retained, state.pb conversion is phase 3"
        )
    return result


_TERMINAL_EVENTS = frozenset({CoreEventType.RUN_FINISHED, CoreEventType.RUN_FAILED})
_TAIL_BYTES = 1 << 16


def prelock_writer_is_live(*, modified: float, now: float, ended: bool) -> bool:
    """Whether a journal with no ``run.lock`` may still be written, judged on the injected clock.

    A journal whose last event ended the run has no writer. Any other journal is live while its
    modification time is within the quiet window of ``now``, or ahead of it (a skewed writer).
    """
    return not ended and now - modified < _PRELOCK_QUIET_SECONDS


def _ends_with_terminal_event(journal: Path) -> bool:
    """Whether the journal's last complete line is a run-ending event."""
    with journal.open("rb") as stream:
        stream.seek(0, 2)
        size = stream.tell()
        stream.seek(max(0, size - _TAIL_BYTES))
        tail = stream.read()
    lines = tail.rstrip().splitlines()
    cut_off = len(tail) == _TAIL_BYTES and len(lines) == 1
    if not lines or cut_off:
        return False
    event = decode_json(
        lines[-1], CoreEvent, source=str(journal), max_bytes=JOURNAL_EVENT_MAX_BYTES
    )
    return not isinstance(event, Unreadable) and event.type in _TERMINAL_EVENTS


def _writer_is_live(directory: Path, clock: SleepingClock) -> bool:
    """Probe modern writer ownership; judge a pre-lock journal by its last event and the clock."""
    path = directory / "run.lock"
    if not path.exists():
        journal = directory / "core-events.jsonl"
        return journal.exists() and prelock_writer_is_live(
            modified=journal.stat().st_mtime,
            now=clock.now(),
            ended=_ends_with_terminal_event(journal),
        )
    try:
        RunLock(path).close()
    except BlockingIOError:
        return True
    return False


def _convert_directory(
    directory: Path,
    run_id: str,
    *,
    clock: SleepingClock,
    threads: Threads,
    install_sync: FileSync | None = None,
) -> ConversionResult:
    """Convert a stopped run; a run whose writer holds ``run.lock`` is left untouched."""
    if not directory.is_dir():
        return ConversionResult(ConversionStatus.SKIPPED, "journal is missing")
    if _writer_is_live(directory, clock):
        return ConversionResult(
            ConversionStatus.SKIPPED,
            "run is live: writer lock or recently modified pre-lock journal",
        )
    try:
        ownership = RunLock(directory / "convert.lock")
    except BlockingIOError:
        return ConversionResult(ConversionStatus.SKIPPED, "another conversion holds this run")
    with closing(ownership):
        try:
            writer = RunLock(directory / "run.lock")
        except BlockingIOError:
            return ConversionResult(
                ConversionStatus.SKIPPED, "run is live: a writer holds run.lock"
            )
        with closing(writer):
            _sweep_stages(directory, run_id)
        return _convert_revision(
            directory, run_id, clock=clock, threads=threads, install_sync=install_sync
        )


def _sweep_stages(directory: Path, run_id: str) -> None:
    """Recover killed converters under both locks, without following stage symlinks."""
    for parent, prefix in (
        (directory, ".convert-run-"),  # Stages from the first conversion release.
        (directory.parent.parent, f".convert-{run_id}-"),
    ):
        for entry in parent.iterdir():
            if entry.name.startswith(prefix) and not entry.is_symlink() and entry.is_dir():
                shutil.rmtree(entry)


def _convert_revision(
    directory: Path,
    run_id: str,
    *,
    clock: SleepingClock,
    threads: Threads,
    install_sync: FileSync | None,
) -> ConversionResult:
    journal = directory / "core-events.jsonl"
    refusal_path = directory / "run.conversion.pb"
    source = journal.stat() if journal.is_file() else None
    if source is not None and refusal_path.is_file():
        refusal = RunConversionRefusal.from_binary(refusal_path.read_bytes())
        validate(refusal)
        if (refusal.journal_size, refusal.journal_modified_ns) == (
            source.st_size,
            source.st_mtime_ns,
        ):
            return ConversionResult(ConversionStatus.CANNOT_CONVERT, refusal.reason)
    try:
        result = _convert_stopped(
            directory, run_id, clock=clock, threads=threads, install_sync=install_sync
        )
    except (_UnsupportedJournalError, TypeError) as error:
        if source is None:
            raise
        reason = ("cannot convert: " + str(error)).encode()[:2048].decode(errors="ignore")
        refusal = RunConversionRefusal(
            journal_size=source.st_size, journal_modified_ns=source.st_mtime_ns, reason=reason
        )
        validate(refusal)
        atomic_write_bytes(refusal_path, refusal.to_binary())
        return ConversionResult(ConversionStatus.CANNOT_CONVERT, reason)
    if result.status in {ConversionStatus.CONVERTED, ConversionStatus.EXISTS}:
        refusal_path.unlink(missing_ok=True)
    return result


def _unconvertible(journal: Path) -> ConversionResult | None:
    """Why a journal has nothing to convert: it records no event, or predates the run trace."""
    first = None
    has_trace = False
    for _, event in _events(journal):
        if first is None:
            first = event
        has_trace |= event.type in (
            CoreEventType.SPAN_STARTED,
            CoreEventType.SPAN_UPDATED,
            CoreEventType.SPAN_ENDED,
        )
    if first is None:
        return ConversionResult(ConversionStatus.SKIPPED, "journal has no events")
    if not has_trace:
        return ConversionResult(ConversionStatus.SKIPPED, "journal predates the run trace")
    return None


def _convert_stopped(
    directory: Path,
    run_id: str,
    *,
    clock: SleepingClock,
    threads: Threads,
    install_sync: FileSync | None = None,
) -> ConversionResult:
    """Atomically convert a quiescent run with trace events, preserving the JSONL source.

    Callers stop the run before converting. A complete destination is left byte
    for byte unchanged; an incomplete destination is replaced only after a full
    successful conversion. Corrupt destinations require operator attention.
    """
    destination = directory / "run.log"
    journal = directory / "core-events.jsonl"
    receipt_path = directory / "run.conversion-source.pb"
    source = journal.stat() if journal.is_file() else None
    drift = None if source is None else _drift(receipt_path, source.st_size, source.st_mtime_ns)
    if drift is None and (sequence := _complete_sequence(destination)):
        if _has_checkpoint(directory, sequence):
            return ConversionResult(ConversionStatus.EXISTS, "already converted")
        with FileRunLogWriter(destination, clock=clock, threads=threads, sync=install_sync):
            pass
        return ConversionResult(ConversionStatus.CONVERTED, "repaired run.head and keyframes")
    if drift is None and destination.is_file() and destination.stat().st_size > 0:
        drift = "log failed its checks"
    journal = directory / "core-events.jsonl"
    if not journal.exists():
        return ConversionResult(ConversionStatus.SKIPPED, "journal is missing")
    source_revision = journal.stat()
    if (unconvertible := _unconvertible(journal)) is not None:
        return unconvertible
    with TemporaryDirectory(prefix=f".convert-{run_id}-", dir=directory.parent.parent) as temporary:
        staged = Path(temporary) / "run.log"

        def prepare() -> None:
            content = FileRunContent(directory / "blobs", directory / "streams", sync=install_sync)
            writer = FileRunLogWriter(staged, clock=clock, threads=threads, sync=install_sync)
            with RunLogTee(writer, content, run_id) as tee:
                for line, _ in _events(journal):
                    tee.append(line)
                writer.checkpoint()
            _require_unchanged(journal, source_revision.st_size, source_revision.st_mtime_ns)

        install_run_log(staged, destination, prepare=prepare, sync=install_sync)
    receipt = RunConversionReceipt(
        journal_size=source_revision.st_size,
        journal_modified_ns=source_revision.st_mtime_ns,
        projection_version=_PROJECTION_VERSION,
    )
    validate(receipt)
    atomic_write_bytes(receipt_path, receipt.to_binary())
    reason = "converted journal to run.log"
    return ConversionResult(
        ConversionStatus.CONVERTED, reason if drift is None else f"{reason} (rebuilt: {drift})"
    )


@dataclass(frozen=True)
class RunConversion:
    """One discovered run, including failures so a batch never silently drops one."""

    run: str
    result: ConversionResult
    # The row that already attempted this host index entry's target, if any.
    alias_of: str | None = None
    cursor: str = ""


def _drift(path: Path, size: int, modified_ns: int) -> str | None:
    """Why the receipt no longer vouches for the log: the journal changed, or the format did."""
    if not path.is_file():
        return None
    receipt = RunConversionReceipt.from_binary(path.read_bytes())
    validate(receipt)
    if receipt.projection_version != _PROJECTION_VERSION:
        return f"projection {receipt.projection_version} to {_PROJECTION_VERSION}"
    if (receipt.journal_size, receipt.journal_modified_ns) != (size, modified_ns):
        return "journal changed"
    return None


def _require_unchanged(journal: Path, size: int, modified_ns: int) -> None:
    current = journal.stat()
    if (current.st_size, current.st_mtime_ns) != (size, modified_ns):
        message = "journal changed during conversion; retry after its writer stops"
        raise RunLogConversionError(message)


def _attempt(
    directory: Path,
    run_id: str,
    *,
    clock: SleepingClock,
    threads: Threads,
    install_sync: FileSync | None,
) -> ConversionResult:
    try:
        return _convert_directory(
            directory, run_id, clock=clock, threads=threads, install_sync=install_sync
        )
    except (OSError, ValueError, TypeError, ProjectError) as exc:
        return ConversionResult(ConversionStatus.FAILED, str(exc))


def _indexed_target(entry: Path) -> Path | ConversionResult:
    try:
        if not entry.is_symlink():
            return ConversionResult(
                ConversionStatus.SKIPPED, "unsupported: index entry is not a symlink"
            )
        target = entry.resolve(strict=True)
        if not target.is_dir():
            return ConversionResult(ConversionStatus.FAILED, "index target is not a directory")
    except (OSError, RuntimeError) as exc:
        return ConversionResult(ConversionStatus.FAILED, f"unreadable index target: {exc}")
    else:
        return target


@dataclass(frozen=True)
class ConversionOptions:
    """A resumable discovery pass's cursor, scope, and optional durability implementation."""

    after_run: str = ""
    include_host_index: bool = False
    install_sync: FileSync | None = None


_DEFAULT_OPTIONS = ConversionOptions()


def iter_conversions(
    project: Project,
    *,
    clock: SleepingClock,
    threads: Threads,
    options: ConversionOptions = _DEFAULT_OPTIONS,
) -> Generator[RunConversion]:
    """Convert stopped project runs lazily; include the host index only by explicit opt-in.

    Each run is attempted only when the caller asks for the next outcome, so a
    caller with a deadline stops between runs. A run already converted reports
    ``EXISTS``. Originals are never deleted, including after the seven-day
    retention window. A failed run does not prevent later runs from being
    attempted. Index entries without a supported journal are explicitly
    reported, including dangling links. A run whose writer holds ``run.lock``
    is skipped and left untouched.
    """
    entries = (
        [(entry, _indexed_target(entry)) for entry in project.indexed_run_directories()]
        if options.include_host_index
        else []
    )
    seen: dict[Path, str] = {}
    ids = set(project.logged_run_ids_for(project.root)) | set(project.state.checkout_run_ids())
    for run_id in sorted(ids):
        cursor = f"project:{run_id}"
        directory = project.log_directory_for(project.root, run_id)
        seen[directory.resolve()] = run_id
        if cursor <= options.after_run:
            continue
        try:
            result = convert_run(
                project, run_id, clock=clock, threads=threads, install_sync=options.install_sync
            )
        except (OSError, ValueError, TypeError, ProjectError) as exc:
            result = ConversionResult(ConversionStatus.FAILED, str(exc))
        if (
            result.status is ConversionStatus.SKIPPED
            and not (directory / "core-events.jsonl").exists()
        ):
            result = ConversionResult(
                ConversionStatus.SKIPPED,
                "unsupported: no trace journal; store.json core state requires phase 3 state.pb",
            )
        yield RunConversion(run_id, result, cursor=cursor)
    for entry, target in sorted(entries):
        cursor = f"zindex:{entry}"
        if cursor <= options.after_run:
            continue
        if isinstance(target, ConversionResult):
            yield RunConversion(str(entry), target, cursor=cursor)
        elif target in seen:
            yield RunConversion(
                str(entry),
                ConversionResult(ConversionStatus.SKIPPED, "already processed this index target"),
                alias_of=seen[target],
                cursor=cursor,
            )
        else:
            result = _attempt(
                target, entry.name, clock=clock, threads=threads, install_sync=options.install_sync
            )
            seen[target] = str(entry)
            yield RunConversion(str(entry), result, cursor=cursor)


@dataclass(frozen=True)
class ConversionObservation:
    """What a listing may say of a run's history without converting it."""

    available: bool
    detail: str = ""


def observe_conversion(project: Project, run_id: str) -> ConversionObservation:
    """Read one run's published head, or its final refusal; never the journal, never a write."""
    path = project.run_log_path_for(project.root, run_id)
    try:
        head = path.with_name("run.head")
        if head.is_file():
            validate(RunHead.from_binary(head.read_bytes()))
            if path.is_file():
                return ConversionObservation(available=True)
        refusal = path.with_name("run.conversion.pb")
        if refusal.is_file():
            value = RunConversionRefusal.from_binary(refusal.read_bytes())
            validate(value)
            return ConversionObservation(available=False, detail=value.reason)
        pending = path.with_name("core-events.jsonl").is_file()
        return ConversionObservation(
            available=False,
            detail="History conversion pending" if pending else "journal is missing",
        )
    except (OSError, ValueError) as error:
        return ConversionObservation(available=False, detail=str(error))


def convert_runs(
    project: Project,
    *,
    clock: SleepingClock,
    threads: Threads,
    include_host_index: bool = False,
    install_sync: FileSync | None = None,
) -> tuple[RunConversion, ...]:
    """Convert every run :func:`iter_conversions` finds; an existing conversion is skipped."""
    return tuple(
        RunConversion(
            row.run, ConversionResult(ConversionStatus.SKIPPED, row.result.reason), row.alias_of
        )
        if row.result.status is ConversionStatus.EXISTS
        else row
        for row in iter_conversions(
            project,
            clock=clock,
            threads=threads,
            options=ConversionOptions(
                include_host_index=include_host_index, install_sync=install_sync
            ),
        )
    )
