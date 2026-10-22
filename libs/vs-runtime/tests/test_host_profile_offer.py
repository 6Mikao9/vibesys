"""A host run offers its task profile command without depending on a cluster."""

from dataclasses import replace
from pathlib import Path

import pytest

from vs_runtime.api import ProfileField
from vs_runtime.api.infrastructure import RunEnvironmentPresentation, RunEnvironmentRequest
from vs_runtime.api.testing import idle_environment_system, unconfined_host_environment
from vs_sandbox.api.testing import FakeComputeBackend
from vs_sim.api.testing import ManualClock, SimThreads


def _request(root: Path, command: str | None) -> RunEnvironmentRequest:
    return RunEnvironmentRequest(
        log_dir=root / "logs",
        workspace=root,
        ref_dir=None,
        backend=FakeComputeBackend(threads=SimThreads()),
        agent_backend="stub",
        cli_provider="claude",
        run_id="run",
        framework_root=root,
        clock=ManualClock(),
        system=idle_environment_system(),
        profile_command=command,
    )


@pytest.mark.regression
@pytest.mark.parametrize("command", [None, "python profile.py"])
def test_host_capture_is_offered_exactly_when_the_task_declares_a_command(
    tmp_path: Path, command: str | None
) -> None:
    environment = unconfined_host_environment()
    with environment.prepare(_request(tmp_path, command)).open(
        RunEnvironmentPresentation(prompt_notes="")
    ) as session:
        assert session.view.cluster_evaluation is None
        assert session.view.captures_profile is (command is not None)
        assert session.view.supported_profile_fields == ()


@pytest.mark.regression
def test_host_capture_exposes_only_the_declared_fields_and_resolves_the_revision_command(
    tmp_path: Path,
) -> None:
    fields = (ProfileField.PREFILL_TIMING, ProfileField.DECODE_TIMING)
    request = replace(
        _request(tmp_path, "python ${PROJECT_ROOT}/profile.py"), profile_fields=fields
    )
    with (
        unconfined_host_environment()
        .prepare(request)
        .open(RunEnvironmentPresentation(prompt_notes="")) as session
    ):
        assert session.view.supported_profile_fields == fields
        assert session.view.paths.profile_command == f"python {tmp_path}/profile.py"
