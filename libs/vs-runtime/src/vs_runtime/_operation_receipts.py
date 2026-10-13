"""Durable record and result receipts that make operation execution idempotent.

A record receipt is written before an owner performs an effect, and a result
is sealed after it. Their presence is how a restarted host knows whether an effect
may have started (record without result) or finished (result). Both are keyed by
request identity and written atomically through the shared ``ReceiptStore``, which
also owns the sealed-result rule.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from vs_core.api import ObservationStatus, OperationSchemaRef, OperationWire

if TYPE_CHECKING:
    from vs_runtime._receipt_store import ReceiptStore

RECEIPT_SCHEMA_VERSION = 1


class RecordReceipt(BaseModel):
    """The effect is about to start. It carries what a later inspection must route on."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: Literal[1] = RECEIPT_SCHEMA_VERSION
    request_id: str = Field(min_length=1)
    payload_digest: str = Field(min_length=1)
    operation: OperationWire


class ResultReceipt(BaseModel):
    """The terminal result of one request: replayed verbatim, never recomputed.

    ``outcome_json`` is present exactly when the status is SUCCEEDED.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: Literal[1] = RECEIPT_SCHEMA_VERSION
    request_id: str = Field(min_length=1)
    payload_digest: str = Field(min_length=1)
    schema_ref: OperationSchemaRef
    status: ObservationStatus
    outcome_json: str | None = None
    detail: str = ""


class OperationReceipts(Protocol):
    """Durable receipts by request identity."""

    @property
    def store(self) -> ReceiptStore:
        """The shared store that runs the effect-once rule."""
        ...

    def record(self, request_id: str) -> RecordReceipt | None: ...

    def record_record(self, receipt: RecordReceipt) -> None: ...

    def result(self, request_id: str) -> ResultReceipt | None: ...

    def record_result(self, receipt: ResultReceipt) -> None: ...


class NamespaceOperationReceipts:
    """Receipts as one atomically written file each below a Project state namespace.

    Re-recording an identical receipt is a no-op. A different payload under the
    same request identity raises ``ContractError`` naming the request.
    """

    _FAMILY = "operations"

    def __init__(self, store: ReceiptStore) -> None:
        """Bind to the shared store that holds this run's receipts."""
        self._store = store

    @property
    def store(self) -> ReceiptStore:
        """The shared store, which runs the effect-once rule for these receipts."""
        return self._store

    def record(self, request_id: str) -> RecordReceipt | None:
        """The recorded record, or None when the effect never started."""
        return self._store.load(self._FAMILY, "record", request_id, RecordReceipt)

    def record_record(self, receipt: RecordReceipt) -> None:
        """Durably record the record, idempotent for the identical payload."""
        self._store.record_once(self._FAMILY, "record", receipt.request_id, receipt)

    def result(self, request_id: str) -> ResultReceipt | None:
        """The sealed terminal result, or None."""
        sealed = self._store.sealed(request_id, ResultReceipt)
        return None if sealed is None else sealed[1]

    def record_result(self, receipt: ResultReceipt) -> None:
        """Seal a result an inspection proved, idempotent for the identical result."""
        self._store.seal(receipt.request_id, receipt.payload_digest, receipt)
