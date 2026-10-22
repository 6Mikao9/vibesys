"""ConvertSavedRuns: bounded, repeatable conversion of a project's stopped runs."""

from __future__ import annotations

from contextlib import closing
from typing import TYPE_CHECKING

import pytest
from hypothesis import given
from hypothesis import strategies as st

from vibesys.api.host_operations.api import serve_convert_saved_runs
from vibesys.api.transport import (
    ConversionResult,
    ConversionStatus,
    RunConversion,
    iter_conversions,
)
from vibesys.events import CoreEventType, SpanStartedData
from vibesys.run import EventJournal
from vs_core.api import Span, SpanStatus, SpanType
from vs_project.api import Project
from vs_proto.api import FailureCategory, MutateRequest, MutateResponse, Oneof, validate
from vs_proto.api import host_payloads as pb
from vs_runlog.api import RunLock
from vs_sim.api.testing import ManualClock, SimThreads

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

_CAP_SECONDS = 120.0
_RESERVE_SECONDS = 20.0


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Project:
    """An empty project whose run logs live under this test's state home."""
    monkeypatch.setenv("VIBESYS_STATE_HOME", str(tmp_path / "state"))
    root = tmp_path / "project"
    root.mkdir()
    return Project.open(root)


def legacy_run(project: Project, run_id: str) -> Path:
    """Write a JSONL-only run with a flow span, as a pre-transport checkout did."""
    directory = Project.log_directory_for(project.root, run_id)
    journal = EventJournal(ManualClock(start=100), SimThreads())
    journal.attach(directory, run_id)
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
    journal.close()
    return directory


def snapshot(directory: Path) -> dict[str, bytes | None]:
    """Every entry under ``directory`` with its bytes; directories map to None."""
    return {
        str(path.relative_to(directory)): path.read_bytes() if path.is_file() else None
        for path in sorted(directory.rglob("*"))
    }


def payload(project: str, request_id: str = "convert-a") -> bytes:
    """One ConvertSavedRuns envelope."""
    request = MutateRequest(
        checkout="/src/checkout",
        method=Oneof(
            "convert_saved_runs",
            pb.ConvertSavedRunsRequest(request_id=request_id, project=project),
        ),
    )
    validate(request)
    return request.to_binary()


def real(clock: ManualClock) -> Callable[[Project], Iterator[RunConversion]]:
    """The shipping converter on simulated time."""
    return lambda opened: iter_conversions(opened, clock=clock, threads=SimThreads())


def serve(
    project: Project,
    clock: ManualClock,
    conversions: Callable[[Project], Iterator[RunConversion]] | None = None,
) -> pb.ConvertSavedRunsResponse:
    """Call the handler and unwrap a validated, correlated response."""
    encoded = serve_convert_saved_runs(
        payload(str(project.root)),
        conversions=real(clock) if conversions is None else conversions,
        clock=clock,
        home=project.root.parent,
    )
    response = MutateResponse.from_binary(encoded)
    validate(response)
    assert response.result is not None
    assert response.result.field == "convert_saved_runs", response.result.value
    converted = response.result.value
    assert isinstance(converted, pb.ConvertSavedRunsResponse)
    assert converted.request_id == "convert-a"
    return converted


def failure(encoded: bytes) -> FailureCategory:
    """The typed failure category of one envelope."""
    response = MutateResponse.from_binary(encoded)
    validate(response)
    assert response.result is not None
    assert response.result.field == "failure"
    return response.result.value.category


def test_a_legacy_run_converts_and_a_repeat_changes_nothing(project: Project) -> None:
    directory = legacy_run(project, "run-1")
    journal = (directory / "core-events.jsonl").read_bytes()
    clock = ManualClock()

    first = serve(project, clock)

    assert first.complete
    assert first.project_root == str(project.root)
    assert [(row.run_id, row.status) for row in first.runs] == [
        ("run-1", pb.RunConversionStatus.CONVERTED)
    ]
    assert (directory / "run.log").is_file()
    assert (directory / "core-events.jsonl").read_bytes() == journal
    converted = snapshot(directory)

    second = serve(project, clock)

    assert second.complete
    assert not second.runs
    assert second.already_converted == 1
    assert snapshot(directory) == converted


def test_a_live_run_is_skipped_and_left_byte_for_byte_unchanged(project: Project) -> None:
    directory = legacy_run(project, "run-1")
    with closing(RunLock(directory / "run.lock")):
        before = snapshot(directory)
        response = serve(project, ManualClock())
        after = snapshot(directory)
    assert response.complete
    assert [(row.run_id, row.status) for row in response.runs] == [
        ("run-1", pb.RunConversionStatus.SKIPPED)
    ]
    assert "live" in response.runs[0].reason
    assert after == before


def test_a_call_stops_before_its_cap_and_repeats_make_progress(project: Project) -> None:
    for run_id in ("run-1", "run-2", "run-3"):
        legacy_run(project, run_id)
    clock = ManualClock()
    per_conversion = (_CAP_SECONDS - _RESERVE_SECONDS) * 0.6

    def slow(opened: Project) -> Iterator[RunConversion]:
        for row in iter_conversions(opened, clock=clock, threads=SimThreads()):
            if row.result.status is ConversionStatus.CONVERTED:
                clock.advance(per_conversion)
            yield row

    first = serve(project, clock, slow)
    assert not first.complete
    assert [row.run_id for row in first.runs] == ["run-1", "run-2"]

    second = serve(project, clock, slow)
    assert second.complete
    assert second.already_converted == 2
    assert [(row.run_id, row.status) for row in second.runs] == [
        ("run-3", pb.RunConversionStatus.CONVERTED)
    ]


_STATUSES = st.sampled_from(list(ConversionStatus))


@given(statuses=st.lists(_STATUSES, max_size=260))
def test_every_attempted_run_is_a_row_or_counted(
    statuses: list[ConversionStatus], tmp_path_factory: pytest.TempPathFactory
) -> None:
    root = tmp_path_factory.getbasetemp() / "project"
    root.mkdir(exist_ok=True)
    rows = [
        RunConversion(f"run-{index}", ConversionResult(status, "r" * 3000))
        for index, status in enumerate(statuses)
    ]
    clock = ManualClock()
    encoded = serve_convert_saved_runs(
        payload(str(root)), conversions=lambda _: iter(rows), clock=clock, home=root
    )
    response = MutateResponse.from_binary(encoded)
    validate(response)
    assert response.result is not None
    converted = response.result.value
    assert isinstance(converted, pb.ConvertSavedRunsResponse)
    assert converted.complete
    exists = statuses.count(ConversionStatus.EXISTS)
    assert converted.already_converted == exists
    assert len(converted.runs) == min(100, len(statuses) - exists)
    assert len(converted.runs) + converted.unreported == len(statuses) - exists
    assert [row.run_id for row in converted.runs] == [
        row.run for row in rows if row.result.status is not ConversionStatus.EXISTS
    ][:100]
    assert all(len(row.reason.encode()) <= 2048 for row in converted.runs)


def test_an_unknown_project_is_a_typed_not_found(tmp_path: Path) -> None:
    clock = ManualClock()
    encoded = serve_convert_saved_runs(
        payload(str(tmp_path / "missing")), conversions=real(clock), clock=clock, home=tmp_path
    )
    assert failure(encoded) is FailureCategory.NOT_FOUND


@pytest.mark.parametrize(
    "envelope",
    [
        b"\xff",
        MutateRequest(
            checkout="/src/checkout",
            method=Oneof("remove_cluster", pb.RemoveClusterRequest(request_id="a", name="host-a")),
        ).to_binary(),
        bytes(1 << 21),
    ],
    ids=["malformed", "other-method", "oversize"],
)
def test_a_bad_envelope_is_invalid_input(envelope: bytes, tmp_path: Path) -> None:
    clock = ManualClock()
    encoded = serve_convert_saved_runs(
        envelope, conversions=real(clock), clock=clock, home=tmp_path
    )
    assert failure(encoded) is FailureCategory.INVALID_INPUT
