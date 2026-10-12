"""Run-level prompt variables of a multi run (the reply types live with the search's agents)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping


def run_prompt_variables(
    *,
    objective: str,
    environment_notes: str,
    agent_evaluation: bool,
    validation_recipe_schema: str,
    domain_sections: Mapping[str, str],
) -> dict[str, object]:
    """The run-level variables every multi template reads, besides its own context's fields."""
    return {
        "objective": objective,
        "environment_notes": environment_notes,
        "agent_evaluation": agent_evaluation,
        "validation_recipe_schema": validation_recipe_schema,
        "domain_sections": domain_sections,
    }
