"""The agents of a dynamic run, and the search wrapped in core's agent pipeline.

The search asks an agent by name and gets one reply or one typed failure. Rendering, validating,
correcting, asking again after a lost turn and resuming a turn that waited for its evaluations
are all the pipeline's (`vs_core.api.AgentTurns`); the waiting and resume path lives there and
nowhere in this package, except that an agent says whether it may wait (`waits`) and how a
resumed turn is prompted (`AgentProtocol.resume`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from vibesys.hypothesis import HypothesisOutcome
from vibesys.orchestration.dynamic.models import (
    ImplementerResult,
    ReviewResult,
    planner_reply_type,
)
from vibesys.orchestration.dynamic.search._ids import IDS
from vibesys.orchestration.dynamic.search._operations import OPERATIONS
from vibesys.orchestration.dynamic.search._prompts import PromptContext
from vibesys.orchestration.dynamic.search._schemas import (
    IMPLEMENTER_REPLY,
    JUDGE_REPLY,
    PLANNER_REPLY,
    PROFILER_REPLY,
)
from vibesys.orchestration.dynamic.search._search import DynamicSearch
from vibesys.orchestration.dynamic.search._values import Role
from vibesys.prompts import ReplyCorrectionPrompt, ResumePrompt, WaitUnrecordedPrompt
from vs_core.api import (
    Access,
    Agent,
    AgentProtocol,
    AgentTurns,
    ReplyError,
    ResumeFacts,
    Retry,
    WaitableReply,
    WorkspaceMode,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import BaseModel

    from vibesys.orchestration.dynamic.search._config import DynamicConfig
    from vibesys.orchestration.dynamic.search._state import DynamicState
    from vs_core.api import SchemaRef, Value


class ImplementerReplyModel(WaitableReply[ImplementerResult]):
    """An implementer or profiler turn ends with a result or a wait for evaluations."""


class JudgeReplyModel(WaitableReply[ReviewResult]):
    """A judge turn ends with a verdict or a wait for evaluations."""


def agents(config: DynamicConfig) -> tuple[Agent, ...]:
    """The four roles, with the reply each answers with and how core retries it."""
    lost = {"lost_turns": config.max_turn_drops, "backoff": config.backoff}
    retry = Retry(corrections=config.max_corrections, **lost)
    return (
        # The planner is asked again by the planning call itself: with a fresh prompt, keeping
        # the best partial plan, so core's own corrections and re-asks are off.
        Agent(
            role=Role.PLANNER.value,
            reply=planner_reply_type(config.profile_offer),
            schema=PLANNER_REPLY,
            access=Access.READ_ONLY,
            workspace=None,
            charge="free",
            retry=Retry.none(),
        ),
        Agent(
            role=Role.IMPLEMENTER.value,
            reply=ImplementerReplyModel,
            schema=IMPLEMENTER_REPLY,
            access=Access.WRITE_CANDIDATE,
            workspace=WorkspaceMode.ISOLATED_CHILD,
            charge="paid",
            retry=retry,
            waits=True,
            checkpoint=lambda reply: (
                isinstance(reply, ImplementerResult)
                and reply.outcome is not HypothesisOutcome.BLOCKED
            ),
        ),
        Agent(
            role=Role.JUDGE.value,
            reply=JudgeReplyModel,
            schema=JUDGE_REPLY,
            access=Access.READ_ONLY,
            workspace=WorkspaceMode.READ_ONLY_REVISION,
            charge="free",
            retry=retry,
            waits=True,
        ),
        Agent(
            role=Role.PROFILER.value,
            reply=ImplementerReplyModel,
            schema=PROFILER_REPLY,
            access=Access.WRITE_ARTIFACTS,
            workspace=WorkspaceMode.READ_ONLY_REVISION,
            charge="paid",
            retry=Retry(corrections=0, **lost),
        ),
    )


def _resume(facts: ResumeFacts) -> Value:
    return ResumePrompt(
        role=facts.agent,  # type: ignore[arg-type]
        retained_revision=facts.retained,
        timed_out=facts.timed_out,
        cited_evidence=facts.evidence,
        repeated_failure=facts.repeated_failure,
    )


def _correction(role: str, errors: tuple[ReplyError, ...]) -> Value:
    return ReplyCorrectionPrompt(role=role, errors=errors)  # type: ignore[arg-type]


PROTOCOL = AgentProtocol(
    correction=_correction,
    resume=_resume,
    wait_unrecorded=lambda role: WaitUnrecordedPrompt(),
)


def reply_schemas(config: DynamicConfig) -> Mapping[SchemaRef, type[BaseModel]]:
    """The reply type of every schema the search asks a role to answer with."""
    return {item.schema: item.reply for item in agents(config)}


def build_search(config: DynamicConfig) -> AgentTurns[DynamicState, DynamicConfig]:
    """The dynamic search of one run, wrapped in the agent pipeline."""
    return AgentTurns(
        DynamicSearch(config),
        agents=agents(config),
        protocol=PROTOCOL,
        operations=OPERATIONS,
        ids=IDS,
        inputs=PromptContext,
    )
