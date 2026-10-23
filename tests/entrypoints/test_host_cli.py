"""Host maintenance dispatch uses the existing console launcher."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from entrypoints.host import run
from entrypoints.launcher import main
from vs_project.api import Project
from vs_sim.api.testing import ManualClock

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_convert_missing_run_reports_why_it_was_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VIBESYS_STATE_HOME", str(tmp_path / "state-home"))
    root = tmp_path / "project"
    root.mkdir()
    assert run(
        ["convert-run", "run-1", "--project", str(root)], cwd=tmp_path, clock=ManualClock()
    ) == (
        0,
        "skipped: journal is missing\n",
        "",
    )


def test_launcher_dispatches_host_and_reports_an_invalid_run_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("VIBESYS_STATE_HOME", str(tmp_path / "state-home"))
    assert main(["host", "convert-run", "../escape", "--project", str(tmp_path)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "vibesys host convert-run" in captured.err
    assert "../escape" in captured.err


def test_bulk_command_reports_skips_failures_and_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VIBESYS_STATE_HOME", str(tmp_path / "home"))
    root = tmp_path / "project"
    root.mkdir()
    directory = Project.log_directory_for(root, "bad")
    directory.mkdir(parents=True)
    (directory / "core-events.jsonl").write_bytes(b"{malformed}")
    empty = Project.log_directory_for(root, "empty")
    empty.mkdir(parents=True)
    (empty / "core-events.jsonl").touch()
    for journal in (directory / "core-events.jsonl", empty / "core-events.jsonl"):
        os.utime(journal, (100, 100))
    code, output, error = run(
        ["convert-runs", "--project", "project"], cwd=tmp_path, clock=ManualClock(start=1000)
    )
    assert code == 1
    assert error == ""
    assert "bad: failed:" in output
    assert "empty: skipped: journal has no events" in output
    assert output.endswith("converted=0, exists=0, skipped=1, failed=1, cannot-convert=0\n")


def test_bulk_command_on_an_empty_project_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VIBESYS_STATE_HOME", str(tmp_path / "home"))
    root = tmp_path / "project"
    root.mkdir()
    assert run(["convert-runs"], cwd=root, clock=ManualClock()) == (
        0,
        "converted=0, exists=0, skipped=0, failed=0, cannot-convert=0\n",
        "",
    )


def test_project_flag_never_converts_another_projects_index_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VIBESYS_STATE_HOME", str(tmp_path / "home"))
    root = tmp_path / "project"
    other = tmp_path / "other"
    root.mkdir()
    other.mkdir()
    path = Project.event_journal_path_for(other, "external")
    path.parent.mkdir(parents=True)
    path.write_bytes(b"invalid journal")
    Project.publish_run_for(other, "external")
    before = {p.name: p.read_bytes() for p in path.parent.iterdir()}

    code, output, error = run(
        ["convert-runs", "--project", str(root)], cwd=tmp_path, clock=ManualClock()
    )

    assert code == 0
    assert error == ""
    assert "external" not in output
    assert {p.name: p.read_bytes() for p in path.parent.iterdir()} == before
