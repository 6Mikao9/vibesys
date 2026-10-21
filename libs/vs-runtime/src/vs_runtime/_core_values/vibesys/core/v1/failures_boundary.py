# Generated from vibesys/core/v1/failures.proto by protoc-gen-corevalue. DO NOT EDIT.
from __future__ import annotations

from typing import Callable, Literal, cast

from protobuf import Message, Oneof

import vs_core.api as values
from vs_proto.api import core_failures as pb
from vs_runtime._core_values.vibesys.core.v1.common_boundary import (
    decode_DecisionId,
    decode_InvocationRef,
    encode_DecisionId,
    encode_InvocationRef,
)
from vs_runtime._core_values.vibesys.core.v1.leaves_boundary import decode_Watch, encode_Watch
from vs_runtime._core_values.vibesys.core.v1.vocabulary_boundary import (
    decode_FailureReason,
    decode_RejectionCode,
    decode_ReplyErrorCode,
    decode_TurnFailureKind,
    decode_WaitRefusal,
    encode_FailureReason,
    encode_RejectionCode,
    encode_ReplyErrorCode,
    encode_TurnFailureKind,
    encode_WaitRefusal,
)
from vs_runtime._corevalue_wkt import decode_timestamp, encode_timestamp


def _required[T](value: T | None) -> T:
    if value is None:
        raise ValueError("Required protobuf message is absent")
    return value


def encode_ReplyPathPart(v: str | int) -> pb.ReplyPathPart:
    if type(v) is str:
        return pb.ReplyPathPart(value=Oneof("key", v))
    if type(v) is int:
        return pb.ReplyPathPart(value=Oneof("index", str(v)))
    raise TypeError("Invalid ReplyPathPart member: " + type(v).__name__)


def decode_ReplyPathPart(m: pb.ReplyPathPart) -> str | int:
    match m.value:
        case Oneof("key", value):
            return value
        case Oneof("index", value):
            return int(value)
        case _:
            raise ValueError("Missing ReplyPathPart member")


def encode_ReplyError(v: values.ReplyError) -> pb.ReplyError:
    return pb.ReplyError(
        path=[encode_ReplyPathPart(x) for x in v.path],
        code=encode_ReplyErrorCode(v.code),
        message=v.message,
    )


def decode_ReplyError(m: pb.ReplyError) -> values.ReplyError:
    return values.ReplyError(
        path=tuple(decode_ReplyPathPart(x) for x in m.path),
        code=decode_ReplyErrorCode(m.code),
        message=m.message,
    )


def encode_Failed(v: values.Failed) -> pb.Failed:
    return pb.Failed(
        kind=v.kind,
        reason=encode_FailureReason(v.reason),
        decision=None if v.decision is None else encode_DecisionId(v.decision),
        watch=None if v.watch is None else encode_Watch(v.watch),
        limit_at=v.limit_at,
        cause=encode_RejectionCode(v.cause),
        errors=[encode_ReplyError(x) for x in v.errors],
        turn=None if v.turn is None else encode_InvocationRef(v.turn),
        turn_failure=encode_TurnFailureKind(v.turn_failure),
        digest=v.digest,
        refusal=encode_WaitRefusal(v.refusal),
    )


def decode_Failed(m: pb.Failed) -> values.Failed:
    return values.Failed(
        kind=cast(Literal["failed"], m.kind),
        reason=decode_FailureReason(m.reason),
        decision=None if m.decision is None else decode_DecisionId(m.decision),
        watch=None if m.watch is None else decode_Watch(m.watch),
        limit_at=m.limit_at if m.has_field("limit_at") else None,
        cause=None if m.cause == 0 else decode_RejectionCode(m.cause),
        errors=tuple(decode_ReplyError(x) for x in m.errors),
        turn=None if m.turn is None else decode_InvocationRef(m.turn),
        turn_failure=None if m.turn_failure == 0 else decode_TurnFailureKind(m.turn_failure),
        digest=m.digest if m.has_field("digest") else None,
        refusal=None if m.refusal == 0 else decode_WaitRefusal(m.refusal),
    )


ENCODERS: dict[type, Callable[..., Message]] = {
    values.ReplyError: encode_ReplyError,
    values.Failed: encode_Failed,
}
DECODERS: dict[type, Callable[..., object]] = {
    pb.ReplyPathPart: decode_ReplyPathPart,
    pb.ReplyError: decode_ReplyError,
    pb.Failed: decode_Failed,
}
