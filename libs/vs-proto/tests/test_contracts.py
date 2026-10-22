"""Public contracts preserve record bytes and enforce protobuf validation."""

from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from vs_proto.api import (
    API_SCHEMA_HASH,
    RUN_LOG_MAGIC,
    RUN_LOG_MAX_RECORD_BYTES,
    STORE_SCHEMA_HASH,
    EpochStarted,
    LegacyEvent,
    Oneof,
    Record,
    TailResponse,
    Timestamp,
    ValidationError,
    core_contracts,
    validate,
)

_TEXT = st.text(alphabet=st.characters(exclude_categories=("Cs",)))
_UINT32 = st.integers(min_value=0, max_value=2**32 - 1)
_UINT64 = st.integers(min_value=0, max_value=2**64 - 1)


@given(seq=_UINT64, epoch=_UINT32, text=_TEXT)
def test_legacy_event_round_trips_verbatim(seq: int, epoch: int, text: str) -> None:
    record = Record(seq=seq, epoch=epoch, body=Oneof("legacy_event", LegacyEvent(json=text)))
    validate(record)
    assert Record.from_binary(record.to_binary()) == record
    tail = TailResponse(records=[record.to_binary()], through_seq=seq)
    assert TailResponse.from_binary(tail.to_binary()) == tail


@given(seq=_UINT64, epoch=_UINT32, run_id=_TEXT, dirty=st.booleans())
def test_epoch_started_round_trips(seq: int, epoch: int, run_id: str, *, dirty: bool) -> None:
    started = EpochStarted(
        run_id=run_id,
        epoch=epoch,
        tree_hash="tree-hash",
        dirty=dirty,
        api_schema_hash=API_SCHEMA_HASH,
        store_schema_hash=STORE_SCHEMA_HASH,
    )
    record = Record(
        seq=seq,
        at=Timestamp(seconds=123, nanos=456),
        epoch=epoch,
        body=Oneof("epoch_started", started),
    )
    validate(record)
    assert Record.from_binary(record.to_binary()) == record


@given(seq=_UINT64, epoch=_UINT32)
def test_record_requires_a_body(seq: int, epoch: int) -> None:
    with pytest.raises(ValidationError) as failure:
        validate(Record(seq=seq, epoch=epoch))
    assert failure.value.violations[0].proto.rule_id == "required"


def test_generated_runlog_limits_agree_across_languages() -> None:

    root = Path(__file__).resolve().parents[3]
    typescript = (root / "clients/libs/proto/src/generated/framing.ts").read_text()
    go = (root / "gateway/gen/framing.go").read_text()
    assert f"RUN_LOG_MAX_RECORD_BYTES = {RUN_LOG_MAX_RECORD_BYTES};" in typescript
    assert f"RunLogMaxRecordBytes = {RUN_LOG_MAX_RECORD_BYTES}" in go
    assert f"RUN_LOG_MAGIC = '{RUN_LOG_MAGIC.decode()}';" in typescript
    assert f'RunLogMagic = "{RUN_LOG_MAGIC.decode()}"' in go


@given(queued=st.floats(min_value=0, max_value=1e9), wait=st.floats(min_value=0, max_value=1e6))
def test_evaluation_admission_timing_is_validated_and_round_trips(
    queued: float, wait: float
) -> None:
    timing = core_contracts.EvaluationTiming(queued_at=queued, admitted_at=queued + wait)
    validate(timing)
    assert core_contracts.EvaluationTiming.from_binary(timing.to_binary()) == timing
    validate(core_contracts.EvaluationTiming(queued_at=queued))
    with pytest.raises(ValidationError):
        validate(core_contracts.EvaluationTiming(queued_at=queued + 1.0, admitted_at=queued))


@given(key=_TEXT.filter(bool), retain=st.booleans())
def test_evaluation_sequence_lease_requires_an_identity(key: str, *, retain: bool) -> None:
    lease = core_contracts.EvaluationLease(key=key, retain=retain)
    validate(lease)
    assert core_contracts.EvaluationLease.from_binary(lease.to_binary()) == lease
    with pytest.raises(ValidationError):
        validate(core_contracts.EvaluationLease(key="", retain=retain))
