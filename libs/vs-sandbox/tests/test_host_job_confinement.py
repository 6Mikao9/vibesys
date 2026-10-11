"""Agent GPU jobs are confined by the host, or are not run at all."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import pytest

from vs_sandbox.api import ProjectPathPolicy, SandboxUnavailableError
from vs_sandbox.api.slurm import HostJobConfinement

if TYPE_CHECKING:
    from pathlib import Path


class TestHostJobConfinement:
    def test_a_host_that_cannot_enforce_confinement_refuses_the_job(self, tmp_path: Path) -> None:
        """Enforcement is required: there is no unconfined job."""
        confinement = HostJobConfinement(
            env={"VIBESYS_AGENT_SANDBOX": "0"},
            resources=(),
            project_path_policy=ProjectPathPolicy(),
        )

        with pytest.raises(SandboxUnavailableError):
            confinement.wrap(tmp_path, ["true"])

    @pytest.mark.skipif(sys.platform != "linux", reason="bubblewrap is the Linux mechanism")
    def test_the_job_names_bubblewrap_by_program_not_by_the_submit_hosts_path(
        self, tmp_path: Path
    ) -> None:
        """The job runs on a compute node that need not have the submit host's copy."""
        tool_dir = tmp_path / "operator-home" / "bin"
        tool_dir.mkdir(parents=True)
        bwrap = tool_dir / "bwrap"
        bwrap.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        bwrap.chmod(0o755)
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        confinement = HostJobConfinement(
            env={"PATH": str(tool_dir), "HOME": str(tmp_path)},
            resources=(),
            project_path_policy=ProjectPathPolicy(),
        )

        argv = confinement.wrap(workspace, ["true"])

        assert argv[0] == "bwrap"
        assert str(tool_dir) not in " ".join(argv)
        assert argv[-1] == "true"
