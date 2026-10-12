"""The run-level variables every evolve prompt template reads."""

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
) -> dict[str, object]:
    """The run-level variables every evolve template reads, besides its own context's fields."""
    return {
        "objective": objective,
        "environment_notes": environment_notes,
        "agent_evaluation": agent_evaluation,
        "domain_sections": domain_sections,
    }
