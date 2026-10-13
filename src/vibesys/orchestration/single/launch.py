"""The options a single run is launched with: the search's config and the task's inputs.

One flat set of keys is what the operator writes and what a run records, so a resume restores
them by name. The search reads only `config`; the host reads the rest (`TaskInputs`). Unknown
keys are rejected. The two presets differ only in `profile_guided`: the attribution command
the search runs follows from it, so it is not a launch option of its own.
"""

from typing import Literal, Self

from pydantic import model_validator

from vibesys.inputs import ProfileGuidedInput
from vibesys.orchestration.agent_options import TaskInputs
from vibesys.orchestration.single.search.api import SingleConfig
from vibesys.serial_search.api import attribution_of

INTERFACES = frozenset({"inprocess", "service"})


class _Launch(TaskInputs, SingleConfig):
    """Search config and task inputs of one single run."""

    @model_validator(mode="after")
    def _attribution_follows_the_preset(self) -> Self:
        if "attribution" in self.model_fields_set:
            message = "'attribution' is not a launch option: it follows 'profile_guided'"
            raise ValueError(message)
        if self.interface not in INTERFACES:
            message = f"unsupported single-agent interface {self.interface!r}"
            raise ValueError(message)
        return self

    def search_config(self) -> SingleConfig:
        """The part the search reads, with the attribution the preset selects."""
        fields = {name: getattr(self, name) for name in SingleConfig.model_fields}
        fields["attribution"] = attribution_of(self.profile_guided)
        return SingleConfig.model_validate(fields)


class SingleLaunch(_Launch):
    """The profiling-off preset."""

    profile_guided: Literal[None] = None


class ProfileGuidedSingleLaunch(_Launch):
    """The profiling-on preset: the input's attribution command steers the designer."""

    profile_guided: ProfileGuidedInput
