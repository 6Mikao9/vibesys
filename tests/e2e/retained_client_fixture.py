"""Replay retained browser fixtures through the shipping typed-record projection.

The output is existing Record protobufs and content bytes. There is no second
record mapper here: the same pure owner used by RunLogTee interprets the events.
"""

from __future__ import annotations

import fcntl
import json
import sys
from pathlib import Path
from typing import Any

from vibesys.events import CoreEvent
from vibesys.records.api import MappingState, map_event
from vs_proto.api import ClientReplay, ClientReplayBatch


def write_fixture(root: Path, fixture: dict[str, Any]) -> None:
    """Publish one immutable fixture under the caller's content-addressed directory."""
    root.parent.mkdir(parents=True, exist_ok=True)
    with root.with_suffix(".lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if (root / ".complete").exists():
            return
        replay = ClientReplay()
        streams: dict[str, bytearray] = {}
        state = MappingState(
            str(fixture["runId"]), native_intents=True, project_root="/work/queue-rs"
        )
        for original in fixture["events"]:
            if original["type"] in {"server_started", "server_ready", "run_status_changed"}:
                continue  # Retired server announcements are projections, not journal facts.
            event = dict(original)
            # These are the validated frontend envelope's decorations, not core fields.
            event.pop("protocol_version", None)
            event.pop("span_id", None)
            event.pop("diagnostic", None)
            event.pop("chat_thread_id", None)
            if "invocation_id" in event:
                event["execution_id"] = event.pop("invocation_id")
            span = (event.get("data") or {}).get("span")
            if span and span.get("status") in ("running", "waiting"):
                # Synthetic fixtures reuse finished span values in STARTED events.
                # The current writer publishes their ending only with terminal status.
                span.pop("ended_at", None)
            projected = map_event(state, CoreEvent.model_validate(event))
            state = projected.state
            replay.batches.append(
                ClientReplayBatch(
                    source_sequence=original["sequence"],
                    records=[record.to_binary() for record in projected.records],
                )
            )
            for blob in projected.blobs:
                replay.blobs[blob.sha256] = blob.content
            for stream in projected.streams:
                content = streams.setdefault(stream.id, bytearray())
                if len(content) != stream.offset:
                    message = "fixture stream offset is not contiguous"
                    raise ValueError(message)
                content.extend(stream.content)
        replay.streams.update({key: bytes(value) for key, value in streams.items()})
        root.mkdir(parents=True, exist_ok=True)
        (root / "replay.pb").write_bytes(replay.to_binary())
        (root / ".complete").touch()


if __name__ == "__main__":
    for item in json.load(sys.stdin):
        write_fixture(Path(sys.argv[1]) / item["key"], item["fixture"])
