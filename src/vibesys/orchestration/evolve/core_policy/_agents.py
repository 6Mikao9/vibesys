"""The agents of an evolve run, and the search wrapped in core's agent pipeline.

The search asks an agent by name and gets one reply or one typed failure. Rendering, validating,
correcting, asking again after a lost turn and resuming a turn that waited for its evaluations
are all the pipeline's (`vs_core.api.AgentTurns`); the waiting and resume path lives there and
nowhere in this package, except that an agent says whether it may wait (`waits`) and how a
resumed turn is prompted (`AgentProtocol.resume`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from vibesys.orchestration.evolve.models import JudgeResponse, MutatorResponse
from vibesys.orchestration.evolve.search.api import (
    IDS,
    JUDGE_REPLY,
    MUTATOR_REPLY,
    OPERATIONS,
    PROFILER_REPLY,
    EvolveSearch,
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

    from vibesys.orchestration.evolve.search.api import EvolveConfig, EvolveState
    from vs_core.api import SchemaRef, Value

# The shared protocol templates name the mutator's role by what it does for a candidate.
_PROTOCOL_ROLE: dict[str, Literal["implementer", "judge", "profiler"]] = {
    Role.MUTATOR.value: "implementer",
    Role.JUDGE.value: "judge",
    Role.PROFILER.value: "profiler",
}


class MutatorReplyModel(WaitableReply[MutatorResponse]):
    """The mutator's turn ends with a result or a wait for evaluations."""


class JudgeReplyModel(WaitableReply[JudgeResponse]):
    """The judge's turn ends with a verdict or a wait for evaluations."""


def agents(config: EvolveConfig) -> tuple[Agent, ...]:
    """The three roles, with the reply each answers with and how core retries it."""
    retry = Retry(
        corrections=config.max_corrections,
        lost_turns=config.max_turn_drops,
        backoff=config.backoff,
    )
    return (
        # The mutator is the one paid turn of a child, and works in the child's own workspace.
        Agent(
            role=Role.MUTATOR.value,
            reply=MutatorReplyModel,
            schema=MUTATOR_REPLY,
            access=Access.WRITE_CANDIDATE,
            workspace=config.workspace_mode,
            charge="paid",
            retry=retry,
            waits=True,
            checkpoint=lambda reply: isinstance(reply, MutatorResponse),
        ),
        # Core attributes a reviewer's verdict to a candidate only when the turn reads that exact
        # retained revision under the read-only mode; it resolves to the attempt's live workspace.
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
            reply=ProfilerSummary,
            schema=PROFILER_REPLY,
            access=Access.READ_ONLY,
            workspace=WorkspaceMode.READ_ONLY_REVISION,
            charge="free",
            retry=retry,
        ),
    )


def _resume(facts: ResumeFacts) -> Value:
    return ResumePrompt(
        role=_PROTOCOL_ROLE[facts.agent],
        retained_revision=facts.retained,
        timed_out=facts.timed_out,
        cited_evidence=facts.evidence,
        repeated_failure=facts.repeated_failure,
    )


def _correction(role: str, errors: tuple[ReplyError, ...]) -> Value:
    return ReplyCorrectionPrompt(role=_PROTOCOL_ROLE[role], errors=errors)


PROTOCOL = AgentProtocol(
    correction=_correction,
    resume=_resume,
    wait_unrecorded=lambda _role: WaitUnrecordedPrompt(),
)


def reply_schemas(config: EvolveConfig) -> Mapping[SchemaRef, type[BaseModel]]:
    """The reply type of every schema the search asks a role to answer with."""
    return {item.schema: item.reply for item in agents(config)}


def build_search(config: EvolveConfig) -> AgentTurns[EvolveState, EvolveConfig]:
    """The evolve search of one run, wrapped in the agent pipeline."""
    return AgentTurns(
        EvolveSearch(config),
        agents=agents(config),
        protocol=PROTOCOL,
        operations=OPERATIONS,
        ids=IDS,
        inputs=PromptContext,
    )
