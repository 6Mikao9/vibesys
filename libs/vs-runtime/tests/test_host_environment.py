"""Properties of selecting the confined host environment for host-only backends."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from vs_project.api import RunEnvironmentRecord
from vs_runtime.api.infrastructure import (
    HostEnvironment,
    RunEnvironmentPresentation,
    RunEnvironmentRequest,
    RunEnvironmentSpec,
    build_run_environment,
    migrate_recorded_run_environment,
    resolve_run_environment_spec,
    run_environment_record,
)
from vs_runtime.api.testing import idle_environment_system
from vs_sandbox.api import (
    ComputeBackend,
    HostSandbox,
    LandlockSandbox,
    ProjectPathPolicy,
    SandboxUnavailableError,
    SeatbeltSandbox,
    WorkspaceSandbox,
    backend_is_host_only,
)
from vs_sandbox.api.testing import FakeComputeBackend
from vs_sim.api.testing import ManualClock, SimThreads

_BACKENDS = st.sampled_from(list(ComputeBackend))
_PLATFORMS = st.sampled_from(["darwin", "linux", "win32", "freebsd14", "cygwin"])
_SPECS = st.sampled_from(
    [
        RunEnvironmentSpec(name="docker", options={"image": None}),
        RunEnvironmentSpec(name="docker", options={"image": "agent:1"}),
        RunEnvironmentSpec(name="modal", options={"gpu": "H100!"}),
        RunEnvironmentSpec(name="skypilot", options={}),
        RunEnvironmentSpec(name="slurm", options={"config_path": "slurm.toml"}),
        RunEnvironmentSpec(name="slurm-gpu", options={"config_path": "slurm.toml"}),
        RunEnvironmentSpec(name="host"),
    ]
)


@dataclass
class _FakeHostSandboxes:
    """Same contract as ``build_host_sandbox`` with ``require_enforcement``.

    A host whose confinement is ``available`` returns a sandbox; otherwise a
    caller requiring enforcement gets ``SandboxUnavailableError``, and one that
    does not gets ``None``, as the real builder does.
    """

    available: bool
    requests: list[bool] = field(default_factory=list)

    def __call__(
        self, workspace: Path, *, env: dict[str, str], require_enforcement: bool = False
    ) -> WorkspaceSandbox | None:
        del env
        self.requests.append(require_enforcement)
        if self.available:
            return _sandbox(workspace)
        if require_enforcement:
            message = "no Seatbelt"
            raise SandboxUnavailableError(message)
        return None


def _sandbox(workspace: Path) -> WorkspaceSandbox:
    return SeatbeltSandbox(workspace=workspace, sandbox_exec_path="/usr/bin/sandbox-exec")


def _request(tmp_path: Path) -> RunEnvironmentRequest:
    return RunEnvironmentRequest(
        log_dir=tmp_path / "logs",
        workspace=tmp_path / "workspace",
        ref_dir=None,
        backend=FakeComputeBackend(threads=SimThreads()),
        agent_backend="stub",
        cli_provider="claude",
        run_id="run-1",
        framework_root=tmp_path,
        environment_bind_mounts=(),
        project_path_policy=ProjectPathPolicy(),
        clock=ManualClock(),
        system=idle_environment_system(),
    )


@given(_BACKENDS, _PLATFORMS)
def test_only_host_only_backends_on_macos_select_the_host(
    backend: ComputeBackend, platform: str
) -> None:
    logs: list[str] = []
    spec = RunEnvironmentSpec(name="docker", options={"image": "agent:1"})

    resolved = resolve_run_environment_spec(
        spec, backend, task_dockerfile=None, platform=platform, log=logs.append
    )

    selects_host = platform == "darwin" and backend_is_host_only(backend)
    assert (resolved.name == "host") is selects_host
    if selects_host:
        assert len(logs) == 1
        assert backend.value in logs[0]
        assert "Seatbelt" in logs[0]
    else:
        assert resolved == spec
        assert logs == []


@given(_SPECS, _BACKENDS, _PLATFORMS)
def test_an_explicit_non_docker_environment_is_never_replaced(
    spec: RunEnvironmentSpec, backend: ComputeBackend, platform: str
) -> None:
    resolved = resolve_run_environment_spec(
        spec, backend, task_dockerfile=None, platform=platform, log=lambda _: None
    )

    if spec.name != "docker":
        assert resolved == spec


@given(backend=_BACKENDS, platform=_PLATFORMS, available=st.booleans())
def test_the_selected_host_requires_seatbelt_and_never_runs_unconfined(
    backend: ComputeBackend, platform: str, *, available: bool
) -> None:
    spec = resolve_run_environment_spec(
        RunEnvironmentSpec(), backend, task_dockerfile=None, platform=platform, log=lambda _: None
    )
    if spec.name != "host":
        return
    sandboxes = _FakeHostSandboxes(available=available)
    environment = HostEnvironment(build_sandbox=sandboxes)

    if available:
        environment.prepare(_request(Path("/project")))
    else:
        with pytest.raises(SandboxUnavailableError):
            environment.prepare(_request(Path("/project")))

    assert sandboxes.requests == [True]


def test_only_metal_declares_itself_host_only() -> None:
    assert {backend for backend in ComputeBackend if backend_is_host_only(backend)} == {
        ComputeBackend.METAL
    }


def test_the_host_spec_builds_the_host_environment() -> None:
    assert isinstance(build_run_environment(RunEnvironmentSpec(name="host")), HostEnvironment)


def test_resuming_a_metal_run_keeps_the_host_environment() -> None:
    resolved = resolve_run_environment_spec(
        RunEnvironmentSpec(),
        ComputeBackend.METAL,
        task_dockerfile=None,
        platform="darwin",
        log=lambda _: None,
    )
    recorded = run_environment_record(resolved)

    assert recorded.name == "host"
    assert migrate_recorded_run_environment(recorded) == recorded
    # The relaunch re-derives the same record, so the resume compatibility check passes.
    again = resolve_run_environment_spec(
        RunEnvironmentSpec(),
        ComputeBackend.METAL,
        task_dockerfile=None,
        platform="darwin",
        log=lambda _: None,
    )
    assert run_environment_record(again) == recorded


@given(st.sampled_from(["darwin", "linux"]))
def test_a_host_record_is_not_migrated_to_docker_on_any_platform(platform: str) -> None:
    record = RunEnvironmentRecord(name="host")

    assert migrate_recorded_run_environment(record) == record
    # On a host that cannot serve it, the relaunch derives Docker and the mismatch is visible.
    relaunch = resolve_run_environment_spec(
        RunEnvironmentSpec(),
        ComputeBackend.METAL,
        task_dockerfile=None,
        platform=platform,
        log=lambda _: None,
    )
    assert (run_environment_record(relaunch) == record) is (platform == "darwin")


@pytest.mark.regression
def test_enforced_host_candidates_have_independent_sessions(tmp_path: Path) -> None:
    """A host-only run must pass dynamic's candidate-isolation admission without Docker."""
    builder = _FakeHostSandboxes(available=True)
    environment = HostEnvironment(build_sandbox=builder)
    sessions = []
    for name in ("root", "candidate-a", "candidate-b"):
        request = _request(tmp_path / name)
        request.workspace.mkdir(parents=True)
        session = environment.prepare(request).open(RunEnvironmentPresentation(prompt_notes=""))
        sessions.append(session)
        assert not session.view.cli_sandboxed
        assert session.view.supports_parallel_candidate_evaluation
        assert session.view.parallel_candidate_obstacle is None
        assert not session.view.agent_git_writable
    assert len({session.sandbox.id for session in sessions}) == 3
    assert builder.requests == [True, True, True]
    for session in sessions:
        session.close()


@pytest.mark.parametrize("enforced", [False, True])
def test_host_admission_uses_the_confinement_guarantee(tmp_path: Path, *, enforced: bool) -> None:
    request = _request(tmp_path)
    request.workspace.mkdir(parents=True)
    confinement = (
        HostSandbox(workspace=request.workspace, bwrap_path="bwrap")
        if enforced
        else LandlockSandbox(workspace=request.workspace, operator_root=Path("/operator/runtime"))
    )
    environment = HostEnvironment(build_sandbox=lambda *_args, **_kwargs: confinement)
    with environment.prepare(request).open(RunEnvironmentPresentation(prompt_notes="")) as session:
        assert session.view.supports_parallel_candidate_evaluation is enforced
        assert (session.view.parallel_candidate_obstacle is None) is enforced
