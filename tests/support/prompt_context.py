"""The template variables the render owner derives from one prompt context.

A prompt test renders a template the way production does: the context's fields plus the
``reply`` variables the run offers its turn, derived from the search's roles.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tests.support.reply_variables import context_variables, profiler_reply

from vibesys.orchestration.dynamic.search.api import dynamic
from vibesys.orchestration.evolve.core_policy.api import reply_schemas as evolve_schemas
from vibesys.orchestration.issue_queue.core_policy.api import reply_schemas as issue_queue_schemas
from vibesys.orchestration.issue_queue.strategy.api import IssueQueueConfig
from vibesys.orchestration.multi.core_policy.api import reply_schemas as multi_schemas
from vibesys.orchestration.single.core_policy.api import reply_schemas as single_schemas
from vs_core.api import ArtifactId, ArtifactRef

if TYPE_CHECKING:
    from pydantic import BaseModel

__all__ = ["context_variables", "profiler_reply", "registered_reply_models"]


def registered_reply_models() -> list[type[BaseModel]]:
    """Every reply model a strategy registers, under each capability variant a run can have."""
    recipe = ArtifactRef(artifact_id=ArtifactId(root="recipe"), digest="recipe")
    models = [
        *dynamic.reply_schemas.values(),
        *evolve_schemas().values(),
        *multi_schemas().values(),
        *single_schemas().values(),
    ]
    for domain in ("llm-serving", "generic"):
        models.extend(
            issue_queue_schemas(
                IssueQueueConfig(
                    recipe=recipe,
                    domain_id=domain,
                    max_rounds=1,
                    max_attempts_per_issue=1,
                    max_issues_per_perf_eval=1,
                    max_issues_per_round=1,
                )
            ).values()
        )
    return models
