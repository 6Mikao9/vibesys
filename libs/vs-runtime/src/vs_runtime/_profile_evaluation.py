"""Command-backed informational profiles, decoded through the evaluator record protocol."""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import TYPE_CHECKING

from vs_evaluation.api import EvidenceMetric, ExecutorRejectedError
from vs_evaluator_protocol.api import ProtocolError, parse_records, read_measurement
from vs_redaction.api import redact_diagnostic_text
from vs_runtime._failure_classification import RecordState, classify, is_unsettled, signal_of
from vs_sim.api import OutputStream

if TYPE_CHECKING:
    from collections.abc import Callable

    from vs_evaluation.api import ProfileField
    from vs_runtime._trusted_evaluation import TrustedEvaluationPlan
    from vs_runtime.contracts import BenchmarkFailureKind, CaptureCommands, Workspace
    from vs_sim.api import OutputSink


@dataclass(frozen=True)
class ProfileReading:
    """A capture's measurements or failure; never a promotion verdict."""

    metrics: tuple[EvidenceMetric, ...] = ()
    failure: str | None = None
    failure_kind: BenchmarkFailureKind | None = None

    @property
    def completed(self) -> bool:
        """Whether capture settled with a definitive measurement outcome."""
        return not is_unsettled(self.failure_kind)


@dataclass(frozen=True)
class CommandProfileCapture:
    """Capture the task's protocol-v2 profile in the executor-owned revision checkout.

    The caller owns that checkout and evaluation admission. CaptureCommands owns the output
    artifact, cancellation and settlement. Every advertised field must occur in a successful
    result, including fields this particular workstream did not require.
    """

    commands: CaptureCommands
    plan: TrustedEvaluationPlan
    command_for: Callable[[Workspace], str | None]

    def __post_init__(self) -> None:
        self.require(())

    def require(self, fields: tuple[ProfileField, ...]) -> None:
        """Refuse unsupported work before creating a checkout or starting a command."""
        if self.plan.profile_command is None:
            message = "the task declares no profile command"
            raise ExecutorRejectedError(message)
        missing = set(fields) - set(self.plan.profile_fields)
        if missing:
            message = "configured capture cannot supply fields: " + ", ".join(sorted(missing))
            raise ExecutorRejectedError(message)

    async def capture(self, workspace: Workspace, *, output: OutputSink) -> ProfileReading:
        """Run once and accept only complete protocol output containing the promised fields."""
        self.require(())
        command = self.command_for(workspace)
        if command is None:
            message = "the task declares no profile command"
            raise ExecutorRejectedError(message)
        result = await self.commands.capture_output(
            tuple(shlex.split(command)),
            workspace=workspace,
            output_argument="--vs-output",
            timeout_seconds=self.plan.profile_timeout_seconds,
        )
        output.write(OutputStream.STDOUT, result.output.encode())
        state = RecordState.ABSENT
        if result.exit_code != 0 or result.truncated:
            failure = result.diagnostic_output or "profile command returned no complete result"
        else:
            try:
                measured = read_measurement(parse_records(result.output))
                state = RecordState.OUTCOME
                if measured.values is None:
                    failure = measured.failure
                elif measured.metrics is not None:
                    missing = set(self.plan.profile_fields) - measured.values.keys()
                    if not missing:
                        return ProfileReading(
                            metrics=tuple(
                                EvidenceMetric(
                                    name=name, value=value, unit=measured.metrics[name].unit
                                )
                                for name, value in measured.values.items()
                            )
                        )
                    failure = "profile result missing declared fields: " + ", ".join(
                        sorted(missing)
                    )
            except ProtocolError as error:
                state = RecordState.DECLARED
                failure = str(error)
        return ProfileReading(
            failure=redact_diagnostic_text(failure or "profile produced no measurement"),
            failure_kind=classify(signal_of(result.exit_code), state),
        )
