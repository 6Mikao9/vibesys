"""The options an evolve run is launched with: the search's config and the task's inputs.

One flat set of keys is what the operator writes and what a run records, so a resume restores
them by name. The search reads only `config`; the host reads the rest (`EvolveTask`). Unknown
keys are rejected.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from vibesys.metrics import MetricSpace
from vibesys.orchestration.evolve.search.api import EvolveConfig
from vs_core.api.search import WorkspaceMode

INTERFACE = "inprocess"
"""The interface an evolve run's prompts describe; the operator does not choose it."""


class EvolveTask(BaseModel):
    """What the operator says about the task, apart from how it is searched."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    modality: Annotated[str, Field(min_length=1, max_length=256)] | None = None
    metric_space: MetricSpace = Field(default_factory=MetricSpace)


class EvolveLaunch(EvolveTask, EvolveConfig):
    """Search config and task inputs of one evolve run.

    ``profiling`` is not the operator's to set: it follows the run's evaluation plan (a chosen
    profiler), which only the host knows (see `evolve_core`).
    """

    @model_validator(mode="after")
    def _profiling_follows_the_plan(self) -> EvolveLaunch:
        for name in ("profiling", "exclusive_root"):
            if name in self.model_fields_set:
                msg = f"{name!r} is not a launch option: it follows the run's environment and plan"
                raise ValueError(msg)
        return self

    def search_config(
        self, *, profiling: bool, workspace_mode: WorkspaceMode = WorkspaceMode.ISOLATED_CHILD
    ) -> EvolveConfig:
        """The part the search reads, with the profiling the evaluation plan allows.

        A host that cannot open candidate sandboxes runs the children one at a time in its root.
        """
        fields = {name: getattr(self, name) for name in EvolveConfig.model_fields}
        exclusive_root = workspace_mode is WorkspaceMode.EXCLUSIVE_ROOT
        fields["max_parallelism"] = 1 if exclusive_root else fields["max_parallelism"]
        return EvolveConfig.model_validate(
            {**fields, "profiling": profiling, "exclusive_root": exclusive_root}
        )
