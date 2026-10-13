"""The options a dynamic run is launched with: the search's config and the task's inputs.

One flat set of keys is what the operator writes and what a run records, so a resume restores
them by name. The search reads only `config`; the host reads the rest (`TaskInputs`). Unknown
keys are rejected.
"""

from __future__ import annotations

from vibesys.orchestration.agent_options import TaskInputs
from vibesys.orchestration.dynamic.search.api import DynamicConfig


class DynamicLaunch(TaskInputs, DynamicConfig):
    """Search config and task inputs of one dynamic run.

    ``profiling`` is not the operator's to set: it follows the run's evaluation plan (a profile
    command and a chosen profiler), which only the host knows (see `dynamic_core`).
    """

    def search_config(self, *, profiling: bool) -> DynamicConfig:
        """The part the search reads, with the profiling the evaluation plan allows."""
        fields = {name: getattr(self, name) for name in DynamicConfig.model_fields}
        return DynamicConfig.model_validate({**fields, "profiling": profiling})
