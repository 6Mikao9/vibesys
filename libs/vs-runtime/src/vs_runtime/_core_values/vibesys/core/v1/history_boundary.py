# Generated from vibesys/core/v1/history.proto by protoc-gen-corevalue. DO NOT EDIT.
from __future__ import annotations

from typing import Callable, Literal, cast

from protobuf import Message, Oneof

import vs_core.api as values
from vs_proto.api import core_history as pb
from vs_runtime._core_values.vibesys.core.v1.common_boundary import (
    decode_RequestId,
    decode_Scope,
    encode_RequestId,
    encode_Scope,
)
from vs_runtime._core_values.vibesys.core.v1.leaves_boundary import (
    decode_BenchmarkFailure,
    decode_EvaluationHistoryCursor,
    decode_EvaluationStageResult,
    decode_Observation,
    encode_BenchmarkFailure,
    encode_EvaluationHistoryCursor,
    encode_EvaluationStageResult,
    encode_Observation,
)
from vs_runtime._core_values.vibesys.core.v1.vocabulary_boundary import (
    decode_AttemptTerminalReason,
    decode_EvaluationHistoryAvailability,
    encode_AttemptTerminalReason,
    encode_EvaluationHistoryAvailability,
)
from vs_runtime._corevalue_wkt import decode_timestamp, encode_timestamp


def _required[T](value: T | None) -> T:
    if value is None:
        raise ValueError("Required protobuf message is absent")
    return value


def encode_EvaluationTerminalFacts(v: values.EvaluationTerminalFacts) -> pb.EvaluationTerminalFacts:
    return pb.EvaluationTerminalFacts(
        stages=[encode_EvaluationStageResult(x) for x in v.stages],
        traceback_signature=v.traceback_signature,
        failed_benchmark=None
        if v.failed_benchmark is None
        else encode_BenchmarkFailure(v.failed_benchmark),
        accuracy_passed=v.accuracy_passed,
    )


def decode_EvaluationTerminalFacts(m: pb.EvaluationTerminalFacts) -> values.EvaluationTerminalFacts:
    return values.EvaluationTerminalFacts(
        stages=tuple(decode_EvaluationStageResult(x) for x in m.stages),
        traceback_signature=m.traceback_signature if m.has_field("traceback_signature") else None,
        failed_benchmark=None
        if m.failed_benchmark is None
        else decode_BenchmarkFailure(m.failed_benchmark),
        accuracy_passed=m.accuracy_passed,
    )


def encode_AttemptEvaluationRecord(v: values.AttemptEvaluationRecord) -> pb.AttemptEvaluationRecord:
    return pb.AttemptEvaluationRecord(
        stages=[encode_EvaluationStageResult(x) for x in v.stages],
        traceback_signature=v.traceback_signature,
        failed_benchmark=None
        if v.failed_benchmark is None
        else encode_BenchmarkFailure(v.failed_benchmark),
        accuracy_passed=v.accuracy_passed,
        ordinal=str(v.ordinal),
        submission_id=encode_RequestId(v.submission_id),
        scope=encode_Scope(v.scope),
        terminal_observation=encode_Observation(v.terminal_observation),
    )


def decode_AttemptEvaluationRecord(m: pb.AttemptEvaluationRecord) -> values.AttemptEvaluationRecord:
    return values.AttemptEvaluationRecord(
        stages=tuple(decode_EvaluationStageResult(x) for x in m.stages),
        traceback_signature=m.traceback_signature if m.has_field("traceback_signature") else None,
        failed_benchmark=None
        if m.failed_benchmark is None
        else decode_BenchmarkFailure(m.failed_benchmark),
        accuracy_passed=m.accuracy_passed,
        ordinal=int(m.ordinal),
        submission_id=decode_RequestId(_required(m.submission_id)),
        scope=decode_Scope(_required(m.scope)),
        terminal_observation=decode_Observation(_required(m.terminal_observation)),
    )


def encode_AttemptEvaluationHistory(
    v: values.AttemptEvaluationHistory,
) -> pb.AttemptEvaluationHistory:
    return pb.AttemptEvaluationHistory(
        availability=encode_EvaluationHistoryAvailability(v.availability),
        covered_submissions=[encode_RequestId(x) for x in v.covered_submissions],
        records=[encode_AttemptEvaluationRecord(x) for x in v.records],
    )


def decode_AttemptEvaluationHistory(
    m: pb.AttemptEvaluationHistory,
) -> values.AttemptEvaluationHistory:
    return values.AttemptEvaluationHistory(
        availability=decode_EvaluationHistoryAvailability(m.availability),
        covered_submissions=tuple(decode_RequestId(x) for x in m.covered_submissions),
        records=tuple(decode_AttemptEvaluationRecord(x) for x in m.records),
    )


def encode_RepeatedFailureGuidance(v: values.RepeatedFailureGuidance) -> pb.RepeatedFailureGuidance:
    return pb.RepeatedFailureGuidance(
        reason=encode_AttemptTerminalReason(v.reason),
        consecutive_failures=str(v.consecutive_failures),
        limit=str(v.limit),
        cursor=encode_EvaluationHistoryCursor(v.cursor),
        traceback_signature=v.traceback_signature,
        failed_benchmark=None
        if v.failed_benchmark is None
        else encode_BenchmarkFailure(v.failed_benchmark),
    )


def decode_RepeatedFailureGuidance(m: pb.RepeatedFailureGuidance) -> values.RepeatedFailureGuidance:
    return values.RepeatedFailureGuidance(
        reason=cast(
            Literal[
                values.AttemptTerminalReason.REPEATED_TRACEBACK,
                values.AttemptTerminalReason.REPEATED_MEASUREMENT,
            ],
            decode_AttemptTerminalReason(m.reason),
        ),
        consecutive_failures=int(m.consecutive_failures),
        limit=int(m.limit),
        cursor=decode_EvaluationHistoryCursor(_required(m.cursor)),
        traceback_signature=m.traceback_signature if m.has_field("traceback_signature") else None,
        failed_benchmark=None
        if m.failed_benchmark is None
        else decode_BenchmarkFailure(m.failed_benchmark),
    )


ENCODERS: dict[type, Callable[..., Message]] = {
    values.EvaluationTerminalFacts: encode_EvaluationTerminalFacts,
    values.AttemptEvaluationRecord: encode_AttemptEvaluationRecord,
    values.AttemptEvaluationHistory: encode_AttemptEvaluationHistory,
    values.RepeatedFailureGuidance: encode_RepeatedFailureGuidance,
}
DECODERS: dict[type, Callable[..., object]] = {
    pb.EvaluationTerminalFacts: decode_EvaluationTerminalFacts,
    pb.AttemptEvaluationRecord: decode_AttemptEvaluationRecord,
    pb.AttemptEvaluationHistory: decode_AttemptEvaluationHistory,
    pb.RepeatedFailureGuidance: decode_RepeatedFailureGuidance,
}
