"""The agents of a multi run, and the search wrapped in core's agent pipeline.

The search asks an agent by name and gets one reply or one typed failure. Rendering, validating,
correcting, asking again after a lost turn and resuming a turn that waited for its evaluations
are all the pipeline's (`vs_core.api.AgentTurns`); the waiting and resume path lives there and
nowhere in this package, except that an agent says whether it may wait (`waits`) and how a
resumed turn is prompted (`AgentProtocol.resume`). The designer's plan is the one reply the
pipeline does not correct: the search checks it against the hypothesis ledger and corrects it
with a typed problem.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from vibesys.hypothesis import OrchestratorPlan
from vibesys.orchestration.multi.models import ImplementerResponse, JudgeResponse, PreRoundDecision
from vibesys.orchestration.multi.search.api import (
    IDS,
    IMPLEMENTER_REPLY,
    JUDGE_REPLY,
    OPERATIONS,
    PLANNER_REPLY,
    PRE_ROUND_REPLY,
    PROFILER_REPLY,
    MultiSearch,
    PromptContext,
    Role,
)
from vibesys.orchestration.profilers import ProfilerSummary
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

    from vibesys.orchestration.multi.search.api import MultiConfig, MultiState
    from vs_core.api import SchemaRef, Value


class ImplementerReplyModel(WaitableReply[ImplementerResponse]):
    """The implementer's turn ends with a result or a wait for evaluations."""


class JudgeReplyModel(WaitableReply[JudgeResponse]):
    """The judge's turn ends with a verdict or a wait for evaluations."""


def agents(config: MultiConfig) -> tuple[Agent, ...]:
    """The five roles, with the reply each answers with and how core retries it."""
    lost = {"lost_turns": config.max_turn_drops, "backoff": config.backoff}
    retry = Retry(corrections=config.max_corrections, **lost)
    return (
        # The designer's plan is corrected by the search, which names what the ledger refused;
        # core only asks again after a lost turn.
        Agent(
            role=Role.PLANNER.value,
            reply=OrchestratorPlan,
            schema=PLANNER_REPLY,
            access=Access.WRITE_ARTIFACTS,
            workspace=None,
            charge="free",
            retry=Retry(corrections=0, **lost),
        ),
        Agent(
            role=Role.PREROUND.value,
            reply=PreRoundDecision,
            schema=PRE_ROUND_REPLY,
            access=Access.READ_ONLY,
            workspace=None,
            charge="free",
            retry=retry,
        ),
        Agent(
            role=Role.PROFILER.value,
            reply=ProfilerSummary,
            schema=PROFILER_REPLY,
            access=Access.WRITE_ARTIFACTS,
            workspace=None,
            charge="free",
            retry=retry,
        ),
        Agent(
            role=Role.IMPLEMENTER.value,
            reply=ImplementerReplyModel,
            schema=IMPLEMENTER_REPLY,
            access=Access.WRITE_CANDIDATE,
            workspace=config.workspace_mode,
            charge="paid",
            retry=retry,
            waits=True,
            checkpoint=lambda reply: isinstance(reply, ImplementerResponse),
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
    )


def _resume(facts: ResumeFacts) -> Value:
    return ResumePrompt(
        role=facts.agent,  # ty: ignore[invalid-argument-type]
        retained_revision=facts.retained,
        timed_out=facts.timed_out,
        cited_evidence=facts.evidence,
        repeated_failure=facts.repeated_failure,
    )


def _correction(role: str, errors: tuple[ReplyError, ...]) -> Value:
    return ReplyCorrectionPrompt(role=role, errors=errors)  # ty: ignore[invalid-argument-type]


PROTOCOL = AgentProtocol(
    correction=_correction,
    resume=_resume,
    wait_unrecorded=lambda _role: WaitUnrecordedPrompt(),
)


def reply_schemas(config: MultiConfig) -> Mapping[SchemaRef, type[BaseModel]]:
    """The reply type of every schema the search asks a role to answer with."""
    return {item.schema: item.reply for item in agents(config)}


def build_search(config: MultiConfig) -> AgentTurns[MultiState, MultiConfig]:
    """The multi search of one run, wrapped in the agent pipeline."""
    return AgentTurns(
        MultiSearch(config),
        agents=agents(config),
        protocol=PROTOCOL,
        operations=OPERATIONS,
        ids=IDS,
        inputs=PromptContext,
    )
