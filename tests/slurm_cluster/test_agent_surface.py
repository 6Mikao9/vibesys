"""The agent has no shell client for trusted evaluation, in any configuration (#1649)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from tests.slurm_cluster.harness import OpenRun

pytestmark = pytest.mark.slurm_cluster


def test_the_agent_has_no_gate_client_and_no_evaluation_commands(run: OpenRun) -> None:
    """Trusted evaluation is requested only through the evaluation tool."""
    assert run.session.view.paths.accuracy_command is None
    assert run.session.view.paths.benchmark_command is None
    assert "--gate" not in run.session.view.prompt_notes
    assert run.agent("command -v vibesys-gate").exit_code != 0


def test_the_gpu_client_exists_exactly_when_the_agent_may_run_gpu_commands(run: OpenRun) -> None:
    found = run.agent("command -v vibesys-gpu")

    assert (found.exit_code == 0) is run.has_gpu_commands, found.output
