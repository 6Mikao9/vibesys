"""Typed data the search hands to the prompt renderer.

The search never builds prompt text. It sends a `PromptContext`; the render operation renders the
role's template with `vs_prompts` and stores the artifacts. A child's workspace is an isolated
child of its parent's revision, so what the parent and the peers are is rendered from the typed
rows below, and the lessons of failed cold starts from their recorded feedback. Facts that are
the run's (the reference path, the chosen profiler) are run-level template variables, not here.
The contexts of the exchanges core starts (a corrected reply, a resumed turn) are core's.
"""

from enum import StrEnum
from typing import Annotated, ClassVar, Literal

from pydantic import Field

from vibesys.orchestration.evolve.population import Individual
from vibesys.orchestration.evolve.search import _roles as roles
from vs_core.api.search import EvidenceRef, RevisionRef, RolePromptContext, SchemaRef, Value


class PromptTemplate(StrEnum):
    """Closed set of the search's own templates (the exchanges' are core's)."""

    MUTATE = "mutate"
    JUDGE = "judge"
    PROFILER = "profiler"


class ObjectiveAxis(Value):
    """One directed objective axis the profiler must report."""

    name: str = Field(min_length=1)
    direction: Literal["max", "min"]


class MutatePrompt(RolePromptContext):
    """One mutation request: a cold start, a repair of an earlier cold start, or a child."""

    reply_schema: ClassVar[SchemaRef | None] = roles.MUTATOR.reply_schema

    template: Literal[PromptTemplate.MUTATE] = PromptTemplate.MUTATE
    base_revision: RevisionRef
    is_cold_start: bool
    repair_seed: bool = False
    """The workspace already holds the previous cold start's files, to be repaired in place."""
    failed_lessons: tuple[str, ...] = ()
    """Distinct feedback of the most recent failed individuals, newest first."""
    num_failed_attempts: int = Field(default=0, ge=0)
    parent: Individual | None = None
    inspirations: tuple[Individual, ...] = ()


class JudgePrompt(RolePromptContext):
    """The independent review of one offspring."""

    reply_schema: ClassVar[SchemaRef | None] = roles.JUDGE.reply_schema

    template: Literal[PromptTemplate.JUDGE] = PromptTemplate.JUDGE
    candidate: RevisionRef
    cited_evidence: tuple[EvidenceRef, ...] = ()
    """The trusted evaluations the mutator took of the offspring, stated at render time."""


class ProfilerPrompt(RolePromptContext):
    """One advisory profiling turn of a passing offspring."""

    reply_schema: ClassVar[SchemaRef | None] = roles.PROFILER.reply_schema

    template: Literal[PromptTemplate.PROFILER] = PromptTemplate.PROFILER
    candidate: RevisionRef
    objectives: tuple[ObjectiveAxis, ...] = ()


type PromptContext = Annotated[
    MutatePrompt | JudgePrompt | ProfilerPrompt, Field(discriminator="template")
]
