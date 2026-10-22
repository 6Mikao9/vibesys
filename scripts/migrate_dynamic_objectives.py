"""Migrate a stopped/copied dynamic run to an explicit per-objective noise policy.

Requires the run's receipt store under the selected VIBESYS_STATE_HOME.
No evaluator or agent is opened. Resume preflight validates both source and result.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from pydantic import FiniteFloat, TypeAdapter

from vibesys.dynamic_migration import migrate_objective_policy, recorded_objective_readings
from vibesys.orchestration.dynamic.search.api import DynamicResume, dynamic
from vs_core.api import FlowState
from vs_project.api import Committed, Project, StoredEnvelope
from vs_runtime.api.core import (
    ReceiptEvidenceLedger,
    ReceiptStore,
    RuntimeRecord,
    resolve_core_resume,
)
from vs_sim.api import OsThreads, SystemClock


def main() -> None:
    """Migrate under a fenced transaction, preserving and validating receipts."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--floor", type=float, required=True)
    parser.add_argument("--provenance", required=True)
    parser.add_argument("--calibration", type=Path, required=True)
    args = parser.parse_args()
    project = Project.open(args.project)
    store = project.state_store(args.run_id)
    source = store.observe()
    if not isinstance(source, StoredEnvelope):
        message = "a committed core envelope is required"
        raise TypeError(message)
    registry = dynamic.registry()
    if registry is None:
        message = "dynamic operation registry is required"
        raise ValueError(message)
    before = RuntimeRecord[FlowState].decode(source, registry)
    root = DynamicResume.model_validate_json(
        next(flow.snapshot_json for flow in before.envelope.strategy.live.flows if flow.path == "")
    )
    driver = dynamic.driver(root.config)
    resolve_core_resume(project, driver, run_id=args.run_id, registry=registry)
    ledger = ReceiptEvidenceLedger(
        ReceiptStore(project.state.local_namespace(args.run_id, "receipts"), OsThreads())
    )
    calibration = TypeAdapter(dict[str, tuple[FiniteFloat, ...]]).validate_json(
        args.calibration.read_bytes()
    )
    after = migrate_objective_policy(
        before,
        recorded_objective_readings(before, ledger),
        floor=args.floor,
        calibration=calibration,
        provenance=args.provenance,
    )
    if before.envelope.core.run.receipts != after.envelope.core.run.receipts:
        message = "migration changed original receipts"
        raise ValueError(message)
    clock = SystemClock()
    now = clock.now()
    fence = store.acquire("objective-policy-migration", now=now, duration=60.0)
    if fence is None:
        message = "run has an active writer; stop it or migrate a copy"
        raise ValueError(message)
    try:
        audit = project.state.portable_namespace(args.run_id, "objective-policy-migrations")
        # Existing RuntimeRecord contract, not an untyped rewrite. This retains
        # the complete previous state, including the original journal.
        audit.write_bytes(f"v1-{source.revision}/before.json", source.payload)
        outcome = store.commit(
            source.revision,
            StoredEnvelope(
                revision=source.revision + 1,
                schema_version=source.schema_version,
                payload=after.model_dump_json().encode(),
            ),
            fence,
            now=now,
        )
        if not isinstance(outcome, Committed):
            message = f"migration commit refused: {outcome}"
            raise TypeError(message)
    finally:
        store.release(fence, now=clock.now())
    checked = resolve_core_resume(project, driver, run_id=args.run_id, registry=registry)
    if checked.record.envelope.core.run.receipts != before.envelope.core.run.receipts:
        message = "committed migration changed original receipts"
        raise ValueError(message)
    print(
        f"Migrated {args.run_id}; resume preflight passed; {len(before.envelope.core.run.receipts)} receipts preserved"
    )


if __name__ == "__main__":
    main()
