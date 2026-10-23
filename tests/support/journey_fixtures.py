"""Native desktop journeys: declarative core events interpreted by the shipping projector.

Regenerate with ``uv run python -m tests.support.journey_fixtures``. The golden
check compares the same projection byte for byte, including blobs and streams.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from tests.support.lineage_fixture import lineage_fixture
from tests.support.retained_events import replay_of
from tests.support.shared_scenarios import regenerate

from vs_proto.api import ClientReplay, ClientReplayBatch, Lifecycle, Oneof, Record, validate
from vs_runlog.api import fold_skeleton

ROOT = Path(__file__).resolve().parents[2] / "clients/apps/desktop-shell/e2e/scenarios"


def project_scenario(name: str) -> ClientReplay:
    """Project a named declaration, refusing unknown fields through the real core contracts."""
    declaration = json.loads((ROOT / f"{name}.json").read_text())
    replay = replay_of(lineage_fixture(declaration) if "lineage" in declaration else declaration)
    rows = [Record.from_binary(wire) for batch in replay.batches for wire in batch.records]
    for index, native in enumerate(
        declaration.get("nativeLifecycles", []), len(declaration["events"]) + 1
    ):
        previous = rows[-1]
        row = Record(
            seq=previous.seq + 1,
            epoch=previous.epoch,
            at=previous.at,
            clock=previous.clock,
            body=Oneof("lifecycle", Lifecycle.from_json(json.dumps(native))),
        )
        validate(row)
        rows.append(row)
        replay.batches.append(ClientReplayBatch(source_sequence=index, records=[row.to_binary()]))
    fold_skeleton(rows, log_offset=0)
    return replay


def live_artifacts() -> dict[str, bytes]:
    """Publish a prefix and one native delta per later event, including incremental stream bytes."""
    fixture = json.loads((ROOT / "live.json").read_text())
    events = fixture["events"]
    through = fixture["liveAfter"]
    previous = replay_of({**fixture, "events": events[:through]})
    output = {"live.initial.pb": previous.to_binary()}
    for index in range(through, len(events)):
        current = replay_of({**fixture, "events": events[: index + 1]})
        delta = ClientReplay(
            batches=current.batches[len(previous.batches) :],
            blobs={key: value for key, value in current.blobs.items() if key not in previous.blobs},
            streams={
                key: value[len(previous.streams.get(key, b"")) :]
                for key, value in current.streams.items()
                if value != previous.streams.get(key, b"")
            },
        )
        output[f"live.{events[index]['sequence']}.pb"] = delta.to_binary()
        previous = current
    return output


def fingerprint() -> str:
    """Invalidate goldens when their declarations, projector, adapter or schemas change."""
    repository = ROOT.parents[4]
    paths = sorted(ROOT.glob("*.json"))
    paths += sorted((repository / "src/vibesys/records").glob("*.py"))
    paths += sorted((repository / "proto/vibesys").rglob("*.proto"))
    paths += [
        Path(__file__),
        Path(__file__).with_name("retained_events.py"),
        Path(__file__).with_name("lineage_fixture.py"),
    ]
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.read_bytes())
    return digest.hexdigest()


if __name__ == "__main__":
    for path in sorted(ROOT.glob("*.json")):
        target = path.with_suffix(".pb")
        target.write_bytes(project_scenario(path.stem).to_binary())
        sys.stdout.write(f"wrote {target.name}\n")
        sys.stdout.flush()
    (ROOT / "source.sha256").write_text(fingerprint() + "\n")
    for name, payload in live_artifacts().items():
        (ROOT / name).write_bytes(payload)
    (ROOT / "goldens.sha256").write_text(
        "".join(
            f"{path.name} {hashlib.sha256(path.read_bytes()).hexdigest()}\n"
            for path in sorted(ROOT.glob("*.pb"))
        )
    )
    trace = json.loads((ROOT / "trace.json").read_text())
    trace.update(
        name="queue-rs-live",
        description="A dynamic search over two planning calls, seven ideas, W5 with a turn and a judge.",
    )
    (ROOT.parents[3] / "libs/run-fixtures/src/data/queue-rs-live.json").write_text(
        json.dumps(trace, indent=2, ensure_ascii=False) + "\n"
    )

    regenerate()
