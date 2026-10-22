"""A product resume refuses changed decision identity as a configuration diagnostic."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from tests.vibesys.run.test_core_run_startup_ownership import _FailingBridge, _startup_host, counter

import vs_core.api as core
from vibesys.errors import ConfigurationError
from vibesys.run.core_run import drive_core_run
from vs_project.api import StoredEnvelope
from vs_runtime.api.core import CoreRuntime, RuntimeRecord
from vs_runtime.api.core_shell_fixtures import CounterConfig
from vs_sim.api.testing import run_virtual

if TYPE_CHECKING:
    from pathlib import Path


def test_product_resume_reports_receipt_integrity_before_starting_services(tmp_path: Path) -> None:
    bridge = _FailingBridge()
    host, integration, clock = _startup_host(tmp_path, bridge)
    services = host.services
    writer = CoreRuntime(
        services.store, counter.driver(CounterConfig()), services.state, config=CounterConfig()
    )
    writer.start("writer", now_at=0, lease_duration=1)
    writer.release_lease(now_at=0)
    saved = writer.record
    state = saved.envelope.core
    decision = core.Finish(
        decision_id=core.DecisionId(root="changed-decision"),
        scope=core.Scope(owner=state.run.run_id, generation=0),
        mode="drain",
        result=core.RunResultProposal(outcome="cancelled", reason="operator stop"),
    )
    receipt = core.DecisionReceipt(
        decision_id=decision.decision_id,
        decision=decision,
        feedback=core.Accepted(decision_id=decision.decision_id),
        payload_digest="0" * 64,
    )
    changed = state.model_copy(
        update={"run": state.run.model_copy(update={"receipts": (receipt,)})}
    )
    record = RuntimeRecord[core.FlowState].fresh(
        saved.envelope.model_copy(update={"core": changed})
    )
    stored = services.store.load()
    assert isinstance(stored, StoredEnvelope)
    fence = services.store.acquire("prepare", now=0, duration=1)
    assert fence is not None
    services.store.commit(
        stored.revision,
        StoredEnvelope(
            revision=stored.revision + 1,
            schema_version=1,
            payload=record.model_dump_json().encode(),
        ),
        fence,
        now=0,
    )
    assert services.store.release(fence, now=0)
    before = services.store.load()

    async def resume() -> None:
        with pytest.raises(ConfigurationError) as failure:
            await drive_core_run(host, integration)
        assert failure.value.diagnostic.code == "core_resume_invalid"
        assert failure.value.diagnostic.stage == "resume_resolution"
        assert "changed-decision.digest" in str(failure.value)
        assert "explicit persisted-state migration" in str(failure.value)

    run_virtual(clock, resume())
    assert services.store.load() == before
    assert bridge.shell is None
    assert services.store.acquire("next-writer", now=clock.now(), duration=1) is not None
