"""The core lifecycle's agent-facing protocol: typed contexts and the templates they render.

Every strategy on the core lifecycle speaks the same protocol to its agents when a reply violates
its schema, when a suspended turn resumes after its awaited evaluations, when a reply waits and
the run recorded no wait, and when it explains the in-turn evaluation tool and the run
environment. The wording lives in ``core_protocol/*.j2``; a strategy lists the directory
(`vibesys.prompts.CORE_PROTOCOL_DIR`) as a fallback root of its core policy and adds the contexts below to its
own prompt-context union.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field

from vs_core.api import DEFAULT_RELATIVE_NOISE, MIN_SAMPLES_WITHOUT_FLOOR, VERDICT_NOISES, Value


class CoreProtocolTemplate(StrEnum):
    """The shared templates a strategy can request through its render operation."""

    RESUME = "resume"
    REPLY_CORRECTION = "reply_correction"
    WAIT_UNRECORDED = "wait_unrecorded"


class WaitUnrecordedPrompt(Value):
    """The previous reply said it was waiting for an evaluation, but the run recorded no wait."""

    template: Literal[CoreProtocolTemplate.WAIT_UNRECORDED] = CoreProtocolTemplate.WAIT_UNRECORDED


class ComparisonRule(Value):
    """How core reaches a comparison's verdict, as the core protocol templates state it.

    ``resume.j2`` renders it, so the agent is told the rule core applies
    (`vs_core._dominance`), not a fixed text that drifts from it.
    """

    verdict_noises: float = Field(gt=0)
    """How many noises a difference must exceed for an objective to be better or worse."""
    default_noise: float = Field(gt=0)
    """The relative noise of one run assumed where the task declares none and the runs show none."""
    min_runs: int = Field(ge=1)
    """The runs each side is taken to when the result stays within noise and the task declares none."""


def core_protocol_variables() -> dict[str, object]:
    """The run-level variables the core protocol templates read.

    Every strategy on the core lifecycle adds them to its own run-level variables, from
    the rule core applies, as it adds the core protocol directory to its template
    roots.
    """
    return {
        "comparison_rule": ComparisonRule(
            verdict_noises=VERDICT_NOISES,
            default_noise=DEFAULT_RELATIVE_NOISE,
            min_runs=MIN_SAMPLES_WITHOUT_FLOOR,
        )
    }
