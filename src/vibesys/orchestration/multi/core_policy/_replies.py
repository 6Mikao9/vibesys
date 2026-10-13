"""The run-level prompt variables every multi template can read."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping


def run_prompt_variables(  # noqa: PLR0913  # LW-641206 [PLR0913]; the variables are independent facts of the run that no one record holds.
    *,
    objective: str,
    environment_notes: str,
    agent_evaluation: bool,
    validation_recipe_schema: str,
    domain_sections: Mapping[str, str],
    profiler_id: str,
    profile_execution: str,
    profiler_support_name: str,
    profiler_mcp_name: str,
    benchmark_command: str | None,
    reference_path: str,
    interface: str,
) -> dict[str, object]:
    """The run-level variables every multi template reads, besides its own context's fields.

    The plan carries this mapping and the renderer adds it to each prompt, so a template
    that reads a new run-level variable gets it from here for production and for tests alike.
    """
    return {
        "objective": objective,
        "environment_notes": environment_notes,
        "agent_evaluation": agent_evaluation,
        "validation_recipe_schema": validation_recipe_schema,
        "domain_sections": domain_sections,
        "profiler_id": profiler_id,
        "profile_execution": profile_execution,
        "profiler_support_name": profiler_support_name,
        "profiler_mcp_name": profiler_mcp_name,
        "benchmark_command": benchmark_command,
        "reference_path": reference_path,
        "interface": interface,
    }
