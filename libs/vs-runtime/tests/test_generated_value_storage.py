"""Generated values use the same strict project and runtime receipt storage boundary."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from hypothesis import given
from hypothesis import strategies as st

from vs_core.api import PauseOwner, RunPause
from vs_project.api import Project, ProjectStateError
from vs_runtime.api.core import ReceiptStore
from vs_sim.api.testing import SimThreads


@given(owner=st.sampled_from(list(PauseOwner)), waiting=st.booleans())
def test_generated_values_survive_receipt_and_typed_slot_restarts(
    owner: PauseOwner, *, waiting: bool
) -> None:
    with TemporaryDirectory() as directory:
        namespace = Project.open(Path(directory)).state.state_store_namespace("run")
        value = RunPause(owner=owner, capacity_wait=waiting)
        store = ReceiptStore(namespace, threads=SimThreads())
        store.record_once("generated", "pause", "key", value)
        assert store.load("generated", "pause", "key", RunPause) == value
        reopened = ReceiptStore(namespace, threads=SimThreads())
        assert reopened.load("generated", "pause", "key", RunPause) == value
        slot = namespace.slot("pause.json", RunPause)
        wire = slot.serialize_transition(slot.transition(value))
        slot.apply(slot.deserialize_transition(wire))
        assert slot.load_optional() == value
        payload = json.loads(wire)
        payload["document"]["unknown"] = True
        with pytest.raises(ProjectStateError):
            slot.deserialize_transition(json.dumps(payload).encode())
