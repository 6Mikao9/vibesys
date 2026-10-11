"""Bind pure profiler policy to product configuration and host preflight."""

from vibesys.composition import agent_spec_from_config
from vibesys.errors import ConfigurationDiagnostic, ConfigurationError
from vibesys.orchestration.profilers import (
    ACTIVE_PROFILER_KINDS,
    ProfilerKind,
    ProfilerPreflightResult,
    preflight_profiler_kind,
    profiler_definition,
    resolve_profiler_kind,
)
from vibesys.run.contracts import RunRequest
from vs_agent.api import agent_supports_tool_servers
from vs_project.api import Project, ProjectLayoutError
from vs_runtime.api.infrastructure import (
    NativeCpuProfilerKind,
    RunEnvironment,
    build_run_environment,
    make_run_environment_spec,
    preflight_native_cpu_profiler,
    validate_run_environment_profile,
)


def native_profiler_preflight(kind: ProfilerKind) -> ProfilerPreflightResult:
    """Adapt runtime-native host checks into profiler policy results."""
    native_kind = {
        ProfilerKind.LINUX_CPU: NativeCpuProfilerKind.LINUX,
        ProfilerKind.MACOS_CPU: NativeCpuProfilerKind.MACOS,
    }[kind]
    capability = preflight_native_cpu_profiler(native_kind)
    return ProfilerPreflightResult(
        kind,
        capability.usable,
        capability.diagnostics,
        capability.details,
    )


def resolve_run_profiler(
    request: RunRequest, environment: RunEnvironment, *, preflight: bool = True
) -> ProfilerKind:
    """Resolve and validate the profiler selected for one product run."""
    config = request.config
    try:
        resolved = resolve_profiler_kind(
            request.profiler_kind,
            domain=request.input_bundle.domain,
            backend=request.backend,
            environment_default_profiler_kind=ProfilerKind(environment.default_profiler_id),
            environment_supported_profiler_kinds=(
                None
                if environment.supported_profiler_ids is None
                else frozenset(ProfilerKind(value) for value in environment.supported_profiler_ids)
            ),
        )
    except ValueError as exc:
        raise ConfigurationError(
            ConfigurationDiagnostic(
                code="profiler_incompatible",
                stage="profiler_validation",
                message=f"--profiler: {exc}",
            )
        ) from exc
    if resolved in ACTIVE_PROFILER_KINDS:
        try:
            validate_run_environment_profile(environment, request.input_bundle.profile_command)
        except ValueError as exc:
            raise ConfigurationError(
                ConfigurationDiagnostic(
                    code="profile_workload_invalid",
                    stage="profiler_validation",
                    message=str(exc),
                )
            ) from exc
        agent_spec = agent_spec_from_config(
            config,
            backend=request.agent_backend,
            provider=request.cli_provider,
            model=config.model.name,
        )
        if not agent_supports_tool_servers(agent_spec):
            definition = profiler_definition(resolved)
            raise ConfigurationError(
                ConfigurationDiagnostic(
                    code="agent_profiler_incompatible",
                    stage="agent_capability_validation",
                    message=(
                        f"Profiler {resolved.value!r} requires agent tool server "
                        f"{definition.mcp_name!r}, but agent backend "
                        f"{agent_spec.backend.value!r} does not support agent tool "
                        "servers. Select the 'cli' agent backend or "
                        "disable profiling with --profiler none."
                    ),
                )
            )
    if not preflight or not environment.requires_local_profiler_preflight:
        return resolved
    result = preflight_profiler_kind(resolved, native_preflight=native_profiler_preflight)
    if not result.usable:
        raise ConfigurationError(
            ConfigurationDiagnostic(
                code="profiler_preflight_failed",
                stage="profiler_preflight",
                message=result.error_message(),
            )
        )
    return resolved


#: The run environments whose trusted evaluation is the evaluation tool alone.
_EVALUATION_TOOL_ONLY_ENVIRONMENTS = frozenset({"slurm", "slurm-gpu"})
#: The one outer loop that has the evaluation tool; the others move to it with roadmap #1952.
_EVALUATION_TOOL_LOOP = "dynamic"


def _require_evaluation_tool_loop(request: RunRequest) -> None:
    """Refuse a loop that cannot evaluate on a run environment that offers only the tool.

    The Slurm environment gives agents no shell client for evaluation, and only the
    dynamic loop reaches the evaluation tool. The other loops evaluate each round through
    a path that Slurm no longer has, so they would run without any trusted evaluation.
    """
    environment = request.run_environment
    loop = request.orchestration.id
    if (
        environment is None
        or environment.name not in _EVALUATION_TOOL_ONLY_ENVIRONMENTS
        or loop == _EVALUATION_TOOL_LOOP
    ):
        return
    raise ConfigurationError(
        ConfigurationDiagnostic(
            code="slurm_loop_unsupported",
            stage="request_validation",
            message=(
                f"the {loop!r} loop cannot run on the slurm run environment: it supports only "
                f"the {_EVALUATION_TOOL_LOOP!r} loop until the other loops are ported to the "
                "evaluation tool (roadmap #1952); use --outer-loop dynamic or another run "
                "environment"
            ),
        )
    )


def validate_run_request(request: RunRequest) -> None:
    """Validate placement, profiler policy, and workload before opening run resources."""
    _require_evaluation_tool_loop(request)
    if request.runs_dir is not None:
        try:
            Project.validate_collection_root(request.runs_dir)
        except ProjectLayoutError as exc:
            raise ConfigurationError(
                ConfigurationDiagnostic(
                    code="invalid_runs_dir",
                    stage="request_validation",
                    message=f"--runs-dir: {exc}",
                )
            ) from exc
    environment = build_run_environment(request.run_environment or make_run_environment_spec())
    resolve_run_profiler(request, environment, preflight=False)


__all__ = ["native_profiler_preflight", "resolve_run_profiler", "validate_run_request"]
