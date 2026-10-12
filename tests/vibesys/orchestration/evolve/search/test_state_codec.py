"""The search state persists through the core envelope codec unchanged."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from tests.vibesys.orchestration.evolve.search.world import FACTS, evolve_config

from vibesys.orchestration.evolve.core_policy.api import evolve_operation_registry
from vibesys.orchestration.evolve.search.api import (
    STATE_SCHEMA,
    ColdStart,
    EvolveSearch,
    EvolveState,
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


def _started() -> EvolveState:
    config = evolve_config()
    return EvolveSearch(config).init(config, FACTS)[0]


def _envelope(
    state: EvolveState | None = None,
) -> tuple[OperationRegistry, RunEnvelope[EvolveState]]:
    codec = evolve_operation_registry()
    config = evolve_config()
    search = EvolveSearch(config)
    started = search.init(config, FACTS)[0]
    return codec, RunEnvelope[EvolveState](
        schema_version=ENVELOPE_SCHEMA_VERSION,
        fence=HostFence(host_id=HostId(root="host"), epoch=1),
        strategy_id=search.declaration.strategy_id,
        state_schema=STATE_SCHEMA,
        core=new_run(search, Harness(registry=codec, facts=FACTS)),
        strategy=state or started,
        event_cursor=EventCursor(sequence=0),
    )


def test_the_started_state_round_trips_through_the_envelope() -> None:
    codec, saved = _envelope()
    restored = codec.decode_envelope(RunEnvelope[EvolveState], codec.encode_envelope(saved))
    assert restored == saved


def test_a_state_with_ended_cold_starts_round_trips_through_the_envelope() -> None:
    state = _started().model_copy(update={"run": ColdStart(spent=2), "stopping": True})
    codec, saved = _envelope(state)
    restored = codec.decode_envelope(RunEnvelope[EvolveState], codec.encode_envelope(saved))
    assert restored.strategy == state


def test_the_state_schema_version_matches_the_declaration() -> None:
    assert _started().schema_version == STATE_SCHEMA.version


def test_a_state_saved_by_the_flag_based_strategy_is_refused_naming_its_version() -> None:
    with pytest.raises(ValidationError, match="schema_version"):
        EvolveState.model_validate({"schema_version": 1, "phase": "bootstrap", "slots": []})


def test_a_state_with_an_unknown_key_is_refused_naming_the_key() -> None:
    with pytest.raises(ValidationError, match="surprise"):
        EvolveState.model_validate(
            {"schema_version": 2, "population": _started().population.model_dump(), "surprise": 1}
        )
