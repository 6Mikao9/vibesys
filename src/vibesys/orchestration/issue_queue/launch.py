"""The options an issue-queue run is launched with: the search's config and the host's inputs.

One flat set of keys is what the operator writes and what a run records, so a resume restores
them by name. The search reads only `IssueQueueConfig`; the host reads the rest (the tracker the
board lives in, the modality the domain prompts are written for). Unknown keys are rejected.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from vibesys.orchestration.issue_queue.search.api import IssueQueueConfig
from vs_issue_tracker.api import IssueTrackerConfig


class IssueQueueLaunch(IssueQueueConfig):
    """Search config and host inputs of one issue-queue run."""

    modality: Annotated[str, Field(min_length=1, max_length=256)] | None = None
    tracker: IssueTrackerConfig = Field(default_factory=IssueTrackerConfig.local)

    def search_config(self) -> IssueQueueConfig:
        """The part the search reads."""
        fields = {name: getattr(self, name) for name in IssueQueueConfig.model_fields}
        return IssueQueueConfig.model_validate(fields)
