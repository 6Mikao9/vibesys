"""The agents of an issue-queue run, and the search wrapped in core's agent pipeline.

The search asks an agent by name and gets one reply or one typed failure. Rendering, validating,
correcting, asking again after a lost turn and resuming a turn that waited for its evaluations
are all the pipeline's (`vs_core.api.AgentTurns`); an agent says whether it may wait (`waits`),
whether its reply asks for a checkpoint of its workspace, and how a resumed turn is prompted
(`AgentProtocol.resume`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from vibesys.orchestration.issue_queue.models import (
    ImplementerReplyModel,
    IssueImplementerResult,
    JudgeReplyModel,
    perf_result_model,
)
from vibesys.orchestration.issue_queue.search.api import (
    IDS,
    IMPLEMENTER_REPLY,
    JUDGE_REPLY,
    OPERATIONS,
    PERF_REPLY,
    IssueQueueSearch,
    PromptContext,
    Role,
)
from vibesys.prompts import ReplyCorrectionPrompt, ResumePrompt, WaitUnrecordedPrompt
from vs_core.api import (
    Access,
    Agent,
    AgentProtocol,
    AgentTurns,
    ReplyError,
    ResumeFacts,
    Retry,
    WorkspaceMode,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import BaseModel

    from vibesys.orchestration.issue_queue.search.api import IssueQueueConfig, IssueQueueState
    from vs_core.api import SchemaRef, Value


def agents(config: IssueQueueConfig) -> tuple[Agent, ...]:
    """The three roles, with the reply each answers with and how core retries it."""
    retry = Retry(
        corrections=config.max_corrections,
        lost_turns=config.max_turn_drops,
        backoff=config.backoff,
    )
    return (
        Agent(
            role=Role.IMPLEMENTER.value,
            reply=ImplementerReplyModel,
            schema=IMPLEMENTER_REPLY,
            access=Access.WRITE_CANDIDATE,
            workspace=WorkspaceMode.ISOLATED_CHILD,
            charge="paid",
            retry=retry,
            waits=True,
            checkpoint=lambda reply: isinstance(reply, IssueImplementerResult),
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
        # The evaluator starts servers and writes its measurements in its own checkout, which
        # is discarded when its attempt settles; it needs no evaluation tool, so it never waits.
        Agent(
            role=Role.PERF.value,
            reply=perf_result_model(config.max_issues_per_perf_eval, domain_id=config.domain_id),
            schema=PERF_REPLY,
            access=Access.WRITE_CANDIDATE,
            workspace=WorkspaceMode.ISOLATED_CHILD,
            charge="free",
            retry=retry,
            checkpoint=lambda _reply: True,
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


def reply_schemas(config: IssueQueueConfig) -> Mapping[SchemaRef, type[BaseModel]]:
    """The reply type of every schema the search asks a role to answer with.

    The judge's schema admits one filed bug of type `bug`; the evaluator's admits at most
    the configured number of issues, so a reply beyond either is not a valid reply.
    """
    return {item.schema: item.reply for item in agents(config)}


def build_search(config: IssueQueueConfig) -> AgentTurns[IssueQueueState, IssueQueueConfig]:
    """The issue-queue search of one run, wrapped in the agent pipeline."""
    return AgentTurns(
        IssueQueueSearch(config),
        agents=agents(config),
        protocol=PROTOCOL,
        operations=OPERATIONS,
        ids=IDS,
        inputs=PromptContext,
        render_limit=config.operation_seconds,
    )
