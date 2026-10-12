"""Envelope harness: a started run on the search's own declaration and a persisted-state round trip."""

from __future__ import annotations

from tests.vibesys.orchestration.dynamic.search._run import FACTS, config

from vibesys.orchestration.dynamic.search.api import (
    STATE_SCHEMA,
    DynamicSearch,
    DynamicState,
    dynamic_operation_registry,
)
from vs_core.api import (
    ENVELOPE_SCHEMA_VERSION,
    EventCursor,
    HostFence,
    HostId,
    OperationRegistry,
    RunEnvelope,
)
from vs_core.testing.drive import Harness, new_run


def envelope(
    state: DynamicState | None = None,
) -> tuple[OperationRegistry, RunEnvelope[DynamicState]]:
    codec = dynamic_operation_registry()
    search = DynamicSearch(config())
    core = new_run(search, Harness(registry=codec, facts=FACTS))
    return codec, RunEnvelope[DynamicState](
        schema_version=ENVELOPE_SCHEMA_VERSION,
        fence=HostFence(host_id=HostId(root="host"), epoch=1),
        strategy_id=search.declaration.strategy_id,
        state_schema=STATE_SCHEMA,
        core=core,
        strategy=state or DynamicState(),
        event_cursor=EventCursor(sequence=0),
    )


def round_trip(
    codec: OperationRegistry, saved: RunEnvelope[DynamicState]
) -> RunEnvelope[DynamicState]:
    return codec.decode_envelope(RunEnvelope[DynamicState], codec.encode_envelope(saved))
