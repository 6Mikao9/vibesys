"""The run-level prompt variables every evolve template can read."""

from __future__ import annotations

from typing import TYPE_CHECKING

from vibesys.orchestration.profilers import ProfilerKind, profiler_definition

if TYPE_CHECKING:
    from collections.abc import Mapping

NO_PROFILER = "none"
"""The profiler id of a run that chose none."""


def run_prompt_variables(
    *,
    objective: str,
    environment_notes: str,
    agent_evaluation: bool,
    domain_sections: Mapping[str, str],
    run_facts: Mapping[str, object],
) -> dict[str, object]:
    """The run-level variables every evolve template reads, besides its own context's fields.

    The plan carries this mapping and the renderer adds it to each prompt, so a template that
    reads a new run-level variable gets it from here for production and for tests alike.
    """
    return {
        "objective": objective,
        "environment_notes": environment_notes,
        "agent_evaluation": agent_evaluation,
        "domain_sections": domain_sections,
        **run_facts,
    }


def run_facts_variables(
    *,
    interface: str,
    reference_path: str,
    profiler_id: str,
    profile_execution: str,
    benchmark_command: str | None,
) -> dict[str, object]:
    """The facts of the run its templates state: the reference, and the profiler and how it is run.

    They are the run's, not any one request's, so they are template variables rather than fields
    of a prompt context. A run that chose no profiler states none.
    """
    facts: dict[str, object] = {"interface": interface, "reference_path": reference_path}
    if profiler_id != NO_PROFILER:
        definition = profiler_definition(ProfilerKind(profiler_id))
        facts |= {
            "profiler_kind": ProfilerKind(profiler_id).value,
            "profile_execution": profile_execution,
            "benchmark_command": benchmark_command,
            "profiler_support_name": definition.support_name,
            "profiler_mcp_name": definition.mcp_name,
        }
    return facts
