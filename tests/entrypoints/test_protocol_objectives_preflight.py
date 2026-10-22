"""A protocol-2 benchmark with no usable objectives fails preflight, before any measurement.

The Flint launch measured for ten minutes, then failed because the row had several metrics
and the run had applied no objectives. Preflight names the task and ``objectives.toml``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tests.support.fake_github import signed_in_github
from tests.support.task_project import write_task

from entrypoints import cli
from entrypoints.cli.loops import validated_run_request
from entrypoints.cli.preflight import preflight_report
from server.host_preflight import PreflightReport, SettingsInvalid, SettingsValid
from vs_sim.api.testing import ScriptedProbe

if TYPE_CHECKING:
    from argparse import Namespace
    from pathlib import Path

_TASK = "flint-m3pro-quick"
_MANIFEST = (
    'version = 1\nbackends = ["cpu"]\n[agent]\ndomain = "generic"\n'
    '[accuracy]\ncommand = ["true"]\n'
    '[benchmark]\ncommand = ["python", "benchmark.py"]\nresult_protocol = 2\n'
)
_OBJECTIVES = (
    '[[objective]]\nname = "tpot_ms_avg"\ndirection = "min"\n'
    '[[objective]]\nname = "ttft_ms_avg"\ndirection = "min"\n'
    '[[objective]]\nname = "completed_requests"\ndirection = "max"\n'
)


def _argv(root: Path) -> list[str]:
    return [
        "--project",
        str(root),
        "--task",
        _TASK,
        "--outer-loop",
        "dynamic",
        "--backend",
        "cpu",
        "--profiler",
        "none",
        "--runs-dir",
        str(root / "runs"),
        "--no-skills",
    ]


def _parse(argv: list[str]) -> Namespace:
    return cli.parse_cli_invocation(argv).args


def _report(root: Path) -> PreflightReport:
    return preflight_report(
        _argv(root),
        _parse,
        ScriptedProbe(lambda _argv: None),
        github=signed_in_github().cli,
    )


def test_preflight_names_a_task_whose_objectives_file_is_missing(tmp_path: Path) -> None:
    write_task(tmp_path, _MANIFEST, task=_TASK)

    settings = _report(tmp_path).settings

    assert isinstance(settings, SettingsInvalid)
    assert settings.code == "missing_objectives"
    assert _TASK in settings.message
    assert "objectives.toml is missing" in settings.message


def test_preflight_names_a_task_whose_objectives_file_declares_none(tmp_path: Path) -> None:
    directory = write_task(tmp_path, _MANIFEST, task=_TASK)
    (directory / "objectives.toml").write_text(
        "[pareto]\nrelative_noise = 0.01\n", encoding="utf-8"
    )

    settings = _report(tmp_path).settings

    assert isinstance(settings, SettingsInvalid)
    assert settings.code == "missing_objectives"
    assert _TASK in settings.message
    assert "objectives.toml declares no objectives" in settings.message


def test_preflight_names_a_task_whose_objectives_file_is_unusable(tmp_path: Path) -> None:
    directory = write_task(tmp_path, _MANIFEST, task=_TASK)
    (directory / "objectives.toml").write_text(
        '[[objective]]\nname = "tpot_ms_avg"\ndirection = "min"\nweight = 2\n',
        encoding="utf-8",
    )

    settings = _report(tmp_path).settings

    assert isinstance(settings, SettingsInvalid)
    assert settings.code == "invalid_objectives"
    assert _TASK in settings.message
    assert "objectives.toml" in settings.message
    assert "weight" in settings.message


def test_a_named_task_with_objectives_resolves_them_into_the_run(tmp_path: Path) -> None:
    directory = write_task(tmp_path, _MANIFEST, task=_TASK)
    (directory / "objectives.toml").write_text(_OBJECTIVES, encoding="utf-8")

    request, _config = validated_run_request(
        _parse(_argv(tmp_path)), ScriptedProbe(lambda _a: None)
    )
    space = request.orchestration.options["metric_space"]

    assert [axis["name"] for axis in space["objectives"]] == [
        "tpot_ms_avg",
        "ttft_ms_avg",
        "completed_requests",
    ]
    assert isinstance(_report(tmp_path).settings, SettingsValid)
