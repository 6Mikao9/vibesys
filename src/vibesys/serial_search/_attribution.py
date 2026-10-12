"""The profile-guided preset's attribution command and the focus policy it feeds.

The command runs against a retained revision (the `CaptureCommand` operation) and prints the
profile result protocol; the ranked components it reports steer the designer through
`ProfileFocus`, and each closed round updates the focus state. These are pure functions over the
hypothesis ledger, shared by every strategy that offers the preset.
"""

from __future__ import annotations

import shlex
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from vibesys.hypothesis import rounds
from vibesys.profile_focus import (
    FocusView,
    ProfileAttributionError,
    ProfileBottleneck,
    ProfileFocus,
    ProfileFocusConfig,
    parse_attribution,
)
from vs_core.api.search import CommandCapture, FailureReason, Value

# The delimiters `parse_attribution` expects around the command's output.
_BEGIN = "__VIBESYS_ATTRIBUTION_BEGIN__"
_END = "__VIBESYS_ATTRIBUTION_END__"
# The argument the runtime appends, followed by the temporary output path.
OUTPUT_ARGUMENT = "--vs-output"

type Positive = Annotated[int, Field(gt=0)]


class AttributionConfig(BaseModel):
    """The profile-guided preset's attribution command and its plateau policy."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    command: tuple[str, ...] = Field(min_length=1)
    timeout_seconds: Positive = 1800
    min_measured_rounds: Positive = 2
    min_relative_improvement: Annotated[float, Field(ge=0)] = 0.02


def focus_policy(attribution: AttributionConfig | None) -> ProfileFocus | None:
    """The component-focus policy of a profile-guided run, else None."""
    if attribution is None:
        return None
    return ProfileFocus(
        ProfileFocusConfig(
            plateau_min_rounds=attribution.min_measured_rounds,
            min_relative_improvement=attribution.min_relative_improvement,
        )
    )


_OUTPUT_TAIL_CHARS = 1000
_COMMAND_NOT_FOUND = 127


def capture_failure(
    command: tuple[str, ...], *, exit_code: int | None, detail: str, output: str
) -> str:
    """Why the attribution command did not produce a result, naming what ran and where.

    The command runs in a throwaway candidate in the run's own command environment (for the
    `slurm` run environment that is the editor container), not in a Slurm job and not on the
    operator's host, so an interpreter or package that exists only there is absent. Exit 127
    is the shell's "program not found", the usual symptom of that.
    """
    ran = shlex.join(command)
    reason = detail or f"exited with code {exit_code}"
    parts = [
        f"the attribution command `{ran}` {reason}.",
        "It runs in the run's command environment (the editor container under the slurm "
        "run environment), where only the workspace and the image's tools exist.",
    ]
    if exit_code == _COMMAND_NOT_FOUND:
        parts.append(
            "Exit code 127 means a program or script it calls was not found there; make the "
            "command self-contained in the workspace or use tools the image provides."
        )
    tail = output.strip()[-_OUTPUT_TAIL_CHARS:]
    if tail:
        parts.append(f"Output tail: {tail}")
    return " ".join(parts)


def bottlenecks_of(output: str) -> tuple[ProfileBottleneck, ...]:
    """The ranked components the attribution command printed.

    Raises:
        ProfileAttributionError: the output is not a valid profile result.
    """
    return parse_attribution(f"{_BEGIN}\n{output}\n{_END}")


def observe(
    ledger: rounds.Ledger,
    policy: ProfileFocus,
    *,
    round_number: int,
    bottlenecks: tuple[ProfileBottleneck, ...],
) -> rounds.Ledger:
    """The ledger once the attribution of ``round_number`` is folded into the focus state."""
    guidance = ledger.search.profile_guidance or policy.initial()
    focused = policy.observe(guidance, round_number=round_number, bottlenecks=bottlenecks)
    search = ledger.search.model_copy(update={"profile_guidance": focused}, deep=True)
    return rounds.Ledger(search, ledger.carry)


def focus_of(ledger: rounds.Ledger, policy: ProfileFocus | None) -> FocusView | None:
    """What the designer sees of the focus state, or None for a plain run."""
    guidance = ledger.search.profile_guidance
    if policy is None or guidance is None:
        return None
    return policy.focus(guidance)


def record_round(
    ledger: rounds.Ledger, policy: ProfileFocus | None, *, passed: bool
) -> rounds.Ledger:
    """Record the newest closed round's result on a profile-guided run's focus state."""
    guidance = ledger.search.profile_guidance
    if policy is None or guidance is None:
        return ledger
    record = ledger.records[-1]
    delta = record.perf_delta_pct
    focused = policy.record(
        guidance,
        round_number=record.round_number,
        passed=passed and record.official_evaluation,
        relative_improvement=None if delta is None else delta / 100,
    )
    return rounds.Ledger(
        ledger.search.model_copy(update={"profile_guidance": focused}), ledger.carry
    )


class AttributionKind(StrEnum):
    """How the attribution command failed. Closed."""

    COMMAND = "command"
    """It ran and did not succeed; ``exit_code``, ``detail`` and ``output`` say how."""
    OUTPUT = "output"
    """It succeeded but printed something that is not the result protocol; ``detail`` says what."""
    ENDED = "ended"
    """The request for it ended without a result; ``ended`` says why."""


class AttributionFailure(Value):
    """Why the profile-guided attribution could not steer the designer.

    ``detail`` and ``output`` are what the command or its parser printed, kept as captured data.
    """

    kind: AttributionKind
    exit_code: int | None = None
    detail: str = ""
    output: str = ""
    ended: FailureReason | None = None


def fold_capture(
    ledger: rounds.Ledger,
    attribution: AttributionConfig,
    outcome: CommandCapture,
    *,
    round_number: int,
) -> rounds.Ledger | AttributionFailure:
    """The ledger once the command's ranked components steer the designer, else why they cannot.

    Profile guidance is the preset's contract, so a command that did not print a valid result is
    a failure of the run.
    """
    if outcome.status != "succeeded" or outcome.exit_code != 0:
        return AttributionFailure(
            kind=AttributionKind.COMMAND,
            exit_code=outcome.exit_code,
            detail=outcome.detail,
            output=outcome.output,
        )
    try:
        bottlenecks = bottlenecks_of(outcome.output)
    except ProfileAttributionError as error:
        return AttributionFailure(kind=AttributionKind.OUTPUT, detail=str(error))
    policy = focus_policy(attribution)
    if policy is None:
        message = "an attribution config has a focus policy"
        raise ValueError(message)
    return observe(ledger, policy, round_number=round_number, bottlenecks=bottlenecks)


def attribution_reason(failure: AttributionFailure, attribution: AttributionConfig) -> str:
    """The operator-facing reason a run ends when its attribution failed."""
    match failure.kind:
        case AttributionKind.COMMAND:
            detail = capture_failure(
                attribution.command,
                exit_code=failure.exit_code,
                detail=failure.detail,
                output=failure.output,
            )
        case AttributionKind.OUTPUT:
            detail = failure.detail
        case AttributionKind.ENDED:
            ended = failure.ended.value if failure.ended else "unknown"
            detail = f"the attribution command did not run ({ended})"
    return f"profile-guided attribution failed: {detail}"
