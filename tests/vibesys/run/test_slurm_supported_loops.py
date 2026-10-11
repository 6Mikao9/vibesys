"""The slurm run environment supports only the dynamic loop until #1952 ports the others."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from tests.vibesys.orchestration.dynamic.core_policy._inputs import run_request

from vibesys.api.request import RunEnvironmentSpec
from vibesys.errors import ConfigurationError
from vibesys.run.profilers import validate_run_request
from vs_agent.api.testing import FakeDockerBuildRunner
from vs_project.api import OrchestrationDescriptor

if TYPE_CHECKING:
    from pathlib import Path

# Every orchestration the outer loops select: agent (multi or single), profile-guided
# (multi or single), dynamic, plain, and evolve.
_LOOPS = (
    "dynamic",
    "multi-agent",
    "single-agent",
    "profile-guided-multi-agent",
    "profile-guided-single-agent",
    "plain",
    "evolve",
)
_SLURM_LOCAL = """[slurm]
name = "test-cluster"
remote_workspace_root = "/shared/vibesys"

[slurm.transport]
kind = "local"
"""
_SLURM_GPU_ALIAS = """[slurm_gpu]
partitions = ["main"]
max_gpus = 8
max_time_minutes = 120
"""


def _request(tmp_path: Path, loop: str, environment: str):  # noqa: ANN202  # RunRequest, kept local.
    config = tmp_path / f"{environment}.toml"
    config.write_text(_SLURM_GPU_ALIAS if environment == "slurm-gpu" else _SLURM_LOCAL)
    base = run_request(tmp_path / "project")
    return base.model_copy(
        update={
            "orchestration": OrchestrationDescriptor(id=loop, config_version=1, options={}),
            "run_environment": RunEnvironmentSpec(
                environment, {"config_path": str(config), "build_runner": FakeDockerBuildRunner()}
            ),
        }
    )


@pytest.mark.parametrize("environment", ["slurm", "slurm-gpu"])
@pytest.mark.parametrize("loop", [loop for loop in _LOOPS if loop != "dynamic"])
def test_every_loop_but_dynamic_is_refused_on_slurm_naming_the_loop(
    tmp_path: Path, loop: str, environment: str
) -> None:
    with pytest.raises(ConfigurationError, match=rf"{loop!r} loop.*only the 'dynamic' loop.*#1952"):
        validate_run_request(_request(tmp_path, loop, environment))


@pytest.mark.parametrize("environment", ["slurm", "slurm-gpu"])
def test_the_dynamic_loop_is_accepted_on_slurm(tmp_path: Path, environment: str) -> None:
    validate_run_request(_request(tmp_path, "dynamic", environment))


@pytest.mark.parametrize("loop", _LOOPS)
def test_no_loop_is_refused_on_docker(tmp_path: Path, loop: str) -> None:
    request = run_request(tmp_path / "project").model_copy(
        update={"orchestration": OrchestrationDescriptor(id=loop, config_version=1, options={})}
    )

    validate_run_request(request)
