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

from vibesys.events import CoreEvent, CoreEventType
from vibesys.records.api import MappingState, map_event
from vs_proto.api import ClientReplay, ClientReplayBatch

CORE_TYPES = frozenset(kind.value for kind in CoreEventType)

# Wire events the server announces or answers with. They were never core journal facts, so a run
# log has no record of them.
ANNOUNCEMENTS = frozenset(
    {
        "server_started",
        "server_ready",
        "run_status_changed",
        "status_query",
        "control",
        "configuration_failed",
    }
)
# Chat is a session's own log, not the run's: the journeys seed it through the session tail.
SESSION_FACTS = frozenset({"chat", "chat_thread_created", "steer_proposed"})
# The frontend envelope's decorations, which no core event carries.
DECORATIONS = ("protocol_version", "span_id", "diagnostic", "chat_thread_id")


def core_event(original: dict[str, Any]) -> dict[str, Any] | None:
    """The core journal event a retained frontend event stands for; None when it is not a core fact.

    Server output lines become the subprocess output the run log records as a stream. A type
    that is neither a core event nor listed above is an error naming it, never a silent drop.
    """
    kind = original["type"]
    if kind in ANNOUNCEMENTS or kind in SESSION_FACTS:
        return None
    event = {key: value for key, value in original.items() if key not in DECORATIONS}
    if "invocation_id" in event:
        event["execution_id"] = event.pop("invocation_id")
    if kind == "output":
        data = event.pop("data")
        event["type"] = "subprocess_output"
        event["data"] = {
            "kind": "subprocess_output",
            "process_id": data["source"],
            "process_kind": data["source"],
            "stream": data["stream"],
            "content": data["content"],
        }
    elif kind not in CORE_TYPES:
        message = f"fixture event type {kind!r} has no core journal form"
        raise ValueError(message)
    span = (event.get("data") or {}).get("span")
    if span and span.get("status") in ("running", "waiting"):
        # Synthetic fixtures reuse finished span values in STARTED events.
        # The current writer publishes their ending only with terminal status.
        span.pop("ended_at", None)
    return event


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
            event = core_event(original)
            if event is None:
                continue
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
