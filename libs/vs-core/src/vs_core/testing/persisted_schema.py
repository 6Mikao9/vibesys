"""Readable snapshots of persisted shapes, and the check that a shape change keeps old documents.

Implementation follows; the tests that pin it are marked strict xfail until then.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping


@dataclass(frozen=True)
class Incompatibility:
    """One change of a persisted shape that a document saved before it may not survive."""

    path: str
    rule: str


@dataclass(frozen=True)
class SchemaSnapshot:
    """A parsed snapshot: the shape's name, its version and its JSON schema."""

    shape: str
    version: tuple[int, ...]
    schema: dict[str, object]


def schema_snapshot(shape: str, version: tuple[int, ...], schema: Mapping[str, object]) -> str:
    raise NotImplementedError


def parse_schema_snapshot(text: str) -> SchemaSnapshot:
    raise NotImplementedError


def schema_incompatibilities(
    old: Mapping[str, object], new: Mapping[str, object]
) -> tuple[Incompatibility, ...]:
    raise NotImplementedError


def snapshot_problems(base: str | None, head: str | None, path: str) -> tuple[str, ...]:
    raise NotImplementedError
