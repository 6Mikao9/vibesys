"""Typed data the search hands to the prompt renderer.

The search never builds prompt text. It sends a `PromptContext`; the render operation renders the
named template with `vs_prompts` and stores the artifacts. The evidence the legacy loop kept in
progress files (round history, the Pareto frontier, carried notices, the last profile) is
rendered from the typed rows below, because a round's workspace is an isolated child of a
revision and holds no progress files. The contexts of the core protocol (a reply that broke its
schema, a resumed turn, a wait nobody recorded) come from `vibesys.prompts`, and the correction
of a plan the ledger refused from `vibesys.serial_search`.
"""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field

from vibesys.orchestration.single.search._values import NoReply, Repair
from vibesys.prompts import ReplyCorrectionPrompt, ResumePrompt, WaitUnrecordedPrompt
from vibesys.serial_search.api import OfficialReason, PlanCorrection, PlanEvidence
from vs_core.api.search import RevisionRef, Value


class PromptTemplate(StrEnum):
    """Closed set of single's own templates (the shared ones are `CoreProtocolTemplate`)."""

    PLAN = "plan"
    ROUND = "round"


class PlanPrompt(PlanEvidence):
    """One new-hypothesis planning request."""

    template: Literal[PromptTemplate.PLAN] = PromptTemplate.PLAN
    base_revision: RevisionRef


class RoundPrompt(Value):
    """One implementation, self-review and profile request for the open round."""

    template: Literal[PromptTemplate.ROUND] = PromptTemplate.ROUND
    round_number: int = Field(gt=0)
    retry: int = Field(ge=1)
    hypothesis_id: str
    title: str = ""
    hypothesis: str
    task: str
    pass_criteria: str
    invariants: str = ""
    activation_evidence: str = ""
    falsification_criteria: str = ""
    minimum_acceptance_criteria: str = ""
    base_revision: RevisionRef
    repair: Repair | None = None
    no_reply: NoReply | None = None
    dominated_by_rounds: tuple[int, ...] = ()
    official_reason: OfficialReason | None = None
    interface: str = "inprocess"
    profiler_id: str = "none"
    profiler_support: str | None = None


type PromptContext = Annotated[
    PlanPrompt
    | RoundPrompt
    | PlanCorrection
    | ResumePrompt
    | ReplyCorrectionPrompt
    | WaitUnrecordedPrompt,
    Field(discriminator="template"),
]
