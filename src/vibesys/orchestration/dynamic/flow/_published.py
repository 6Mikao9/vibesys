"""Read the record a dynamic flow search published from its persisted state."""

from pydantic import ValidationError

from vibesys.orchestration.dynamic.flow._run_state import RunSnapshot
from vs_core.api.search import FlowState

_MARK = '"dynamic-run-snapshot"'


def published(state: FlowState) -> RunSnapshot | None:
    """The latest record the search published, or None before it published any."""
    for step in reversed(state.journal):
        for text in reversed(step.reads):
            if _MARK in text:
                try:
                    return RunSnapshot.model_validate_json(text)
                except ValidationError:
                    continue
    return None
