"""The run-level prompt variables every single template can read."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping


def run_prompt_variables(
    *,
    objective: str,
    environment_notes: str,
    agent_evaluation: bool,
    domain_sections: Mapping[str, str],
    profiler_id: str,
) -> dict[str, object]:
    """The run-level variables every single template reads, besides its own context's fields.

    The plan carries this mapping and the renderer adds it to each prompt, so a template
    that reads a new run-level variable gets it from here for production and for tests alike.
    """
    return {
        "objective": objective,
        "environment_notes": environment_notes,
        "agent_evaluation": agent_evaluation,
        "domain_sections": domain_sections,
        "profiler_id": profiler_id,
    }
