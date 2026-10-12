"""Envelope harness: a started run on the search's own declaration and a persisted-state round trip."""

from __future__ import annotations

from tests.vibesys.orchestration.dynamic.search._run import FACTS, config

from vibesys.orchestration.dynamic.core_policy.api import (
    dynamic_operation_registry,
)
from vibesys.orchestration.dynamic.flow.api import FLOW_STATE_SCHEMA, dynamic_search
from vs_core.api import (
    ENVELOPE_SCHEMA_VERSION,
    EventCursor,
    FlowState,
    HostFence,
    HostId,
    OperationRegistry,
    RunEnvelope,
)
from vs_core.testing.drive import Harness, new_run


def envelope(
    state: FlowState | None = None,
) -> tuple[OperationRegistry, RunEnvelope[FlowState]]:
    codec = dynamic_operation_registry()
    settings = config()
    search = dynamic_search(settings)
    core = new_run(search, Harness(registry=codec, facts=FACTS))
    return codec, RunEnvelope[FlowState](
        schema_version=ENVELOPE_SCHEMA_VERSION,
        fence=HostFence(host_id=HostId(root="host"), epoch=1),
        strategy_id=search.declaration.strategy_id,
        state_schema=FLOW_STATE_SCHEMA,
        core=core,
        strategy=state or search.init(settings, FACTS)[0],
        event_cursor=EventCursor(sequence=0),
    )


def round_trip(codec: OperationRegistry, saved: RunEnvelope[FlowState]) -> RunEnvelope[FlowState]:
    return codec.decode_envelope(RunEnvelope[FlowState], codec.encode_envelope(saved))
