"""The options a multi run is launched with: the search's config and the task's inputs.

One flat set of keys is what the operator writes and what a run records, so a resume restores
them by name. The search reads only `config`; the host reads the rest (`TaskInputs`). Unknown
keys are rejected. The two presets differ only in `profile_guided`: the attribution command the
search runs follows from it, and whether the run offers a profiler follows from the evaluation
plan; neither is a launch option of its own.
"""

from typing import Literal, Self

from pydantic import model_validator

from vibesys.inputs import ProfileGuidedInput
from vibesys.orchestration.agent_options import TaskInputs
from vibesys.orchestration.multi.search.api import MultiConfig
from vibesys.serial_search.api import attribution_of

INTERFACES = frozenset({"inprocess", "service"})


class _Launch(TaskInputs, MultiConfig):
    """Search config and task inputs of one multi run."""

    @model_validator(mode="after")
    def _derived_keys_are_not_options(self) -> Self:
        for key in ("attribution", "profiling"):
            if key in self.model_fields_set:
                message = f"{key!r} is not a launch option: it is derived from the run"
                raise ValueError(message)
        if self.interface not in INTERFACES:
            message = f"unsupported multi-agent interface {self.interface!r}"
            raise ValueError(message)
        return self

    def search_config(self, *, profiling: bool) -> MultiConfig:
        """The part the search reads, with the profiling the run's profiler allows."""
        fields = {name: getattr(self, name) for name in MultiConfig.model_fields}
        fields["attribution"] = attribution_of(self.profile_guided)
        fields["profiling"] = profiling
        return MultiConfig.model_validate(fields)


class MultiLaunch(_Launch):
    """The profiling-off preset."""

    profile_guided: Literal[None] = None


class ProfileGuidedMultiLaunch(_Launch):
    """The profiling-on preset: the input's attribution command steers the designer."""

    profile_guided: ProfileGuidedInput
