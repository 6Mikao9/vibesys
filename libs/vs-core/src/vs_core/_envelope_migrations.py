"""The chain that brings a stored run envelope of an older version to the current one.

A run is resumed from the envelope it stored, however long ago and whatever version of
VibeSys wrote it: updating the code never forces a fresh run. This module is the one
registry of the steps. ``MIGRATIONS[n]`` is the pure rewrite of an envelope of version ``n``
into the one a run at version ``n + 1`` would have stored, and the chain is every step in
order (`upgrade_envelope_document`). Loading strictly validates the result at the current
version (`vs_core._registry.OperationRegistry.decode_envelope`).

The rule, enforced by ``tests/test_envelope_migrations.py``: every version from the oldest
supported one up to the one before ``ENVELOPE_SCHEMA_VERSION`` has exactly one registered
migration and one fixture envelope (``tests/envelopes/v<n>-*.json``) that the chain brings to
the current version and decodes. A change to ``ENVELOPE_SCHEMA_VERSION`` without the previous
version's migration and fixture fails there. The fixtures are saved by the code of their own
version, never derived from a later one. Since version 42 a compatible shape change keeps the
version (``scripts/check_persisted_schema_compat.py``), so every step from 42 on rewrites data
its fixture holds; the number-only steps below predate that rule.

A migration is frozen at the version it targets, so it states what it needs of the document
itself and imports nothing that a later change can move (not the models, not the comparison
statistic). It invents nothing: a field that has no honest value for old data takes the value
the new code itself gives "unknown". It leaves history alone (receipts and events record what
was told and done, as it was).
"""

from __future__ import annotations

import json
from hashlib import sha256
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from pydantic import JsonValue

type JsonObject = dict[str, JsonValue]
type Migration = Callable[[JsonObject], JsonObject]

_MIN_RUNS_WITHOUT_FLOOR: Final = 3
"""Runs a side that version 35 needs for an estimate of the noise when no floor is declared."""


def _v34_to_v35(envelope: JsonObject) -> JsonObject:
    """Comparisons held with no estimate of their noise read ``inconclusive``, not ``within_noise``.

    Version 35 added the verdict ``inconclusive`` for exactly the comparisons that have no
    estimate of the noise (a task that declares no floor, and fewer than three runs a side or
    runs that show no spread). Version 34 called each of them ``within_noise``. The envelope
    holds the run's comparisons as they stand in ``core.evaluation.comparisons``, with the
    counts and noise that decide it, and the task's declared floor in
    ``core.run.facts.task.relative_noise``; a held ``within_noise`` is rewritten exactly when
    the 35 rule finds no estimate. A ``within_noise`` that was measured (a declared floor, or
    three runs a side with a spread) stays, and so do ``better`` and ``worse``. Every other
    part of the envelope is the same shape in both versions.

    The comparisons recorded in receipts are what an agent was told then, and stay as told.
    """
    core = _object(envelope.get("core"))
    evaluation = _object(core.get("evaluation"))
    comparisons = evaluation.get("comparisons")
    if isinstance(comparisons, list) and not _floor_declared(core):
        rewritten: list[JsonValue] = [
            item | {"verdict": "inconclusive"} if _is_unmeasured(item) else item
            for item in comparisons
        ]
        evaluation = evaluation | {"comparisons": rewritten}
        core = core | {"evaluation": evaluation}
    return envelope | {"core": core, "schema_version": 35}


def _v35_to_v36(envelope: JsonObject) -> JsonObject:
    """Version 36 gave ``EvidenceRef`` an optional ``unmet_requirement`` and nothing else.

    Evidence saved at 35 has none: the evaluator could not state one then, and the field's value
    for "none stated" is absent (it is not serialized when unset), which is exactly what a
    version 35 document holds. Every version 35 document is a valid version 36 one with its
    number changed.
    """
    return envelope | {"schema_version": 36}


def _v36_to_v37(envelope: JsonObject) -> JsonObject:
    """Version 37 added the events ``stop_lifted`` and ``stop_lift_recorded`` and nothing else.

    A stop could not be lifted before, so no stored envelope holds either: the shape only grew
    (the flow journal's event union and the strategy events core publishes), and every
    version 36 document is a valid version 37 one with its number changed.
    """
    return envelope | {"schema_version": 37}


def _v37_to_v38(envelope: JsonObject) -> JsonObject:
    """Version 38 added the capacity hold of a turn and the failure kind of a turn lost to it.

    A request record may carry ``capacity_hold`` (not serialized when unset), the records
    events gained ``turn_held``, and a turn result may say ``provider_unavailable``. A version
    37 run had no capacity gate reporting holds, so no stored record holds one and no turn was
    lost to its provider: the shape only grew, and every version 37 document is a valid
    version 38 one with its number changed.
    """
    return envelope | {"schema_version": 38}


def _v38_to_v39(envelope: JsonObject) -> JsonObject:
    """Version 39 added the recovery check resolution ``unknown`` and nothing else.

    Recovery settles a check whose target's result is discarded whatever it was (a cancel of
    a job) as ``unknown`` when its executor could not say. A version 38 run had no such
    resolution, so no stored check holds it: the shape only grew, and every version 38
    document is a valid version 39 one with its number changed.
    """
    return envelope | {"schema_version": 39}


def _v39_to_v40(envelope: JsonObject) -> JsonObject:
    """Version 40 gave ``MeasurementResult`` an optional ``decision_id`` and nothing else.

    A result saved at 39 answers no decision of its own: it names none, and the field's value
    for "none" is absent (it is not serialized when unset), which is exactly what a version 39
    document holds. Every version 39 document is a valid version 40 one with its number changed.
    """
    return envelope | {"schema_version": 40}


def _v40_to_v41(envelope: JsonObject) -> JsonObject:
    """Version 41 gave ``RunResultProposal`` an optional ``uncompared`` and nothing else.

    A result saved at 40 says nothing about an adoption never compared with the input, and the
    field's value for "compared" is absent (it is not serialized when unset), which is exactly
    what a version 40 document holds. Every version 40 document is a valid version 41 one with
    its number changed.
    """
    return envelope | {"schema_version": 41}


def _v41_to_v42(envelope: JsonObject) -> JsonObject:
    """Version 42 added the measurement failure ``run_environment_unavailable`` and nothing else.

    No run could end a measurement that way before, so no stored envelope holds it: the closed
    set only grew, and every version 41 document is a valid version 42 one with its number
    changed.
    """
    return envelope | {"schema_version": 42}


def _v42_to_v43(envelope: JsonObject) -> JsonObject:
    """Version 43 judges every objective: runs and records hold ``readings``, a comparison ``objectives``.

    A sample and a benchmark ``EvidenceRef`` held the headline ``reading`` of their run and
    nothing of its other objectives; they now hold ``readings``, every objective's, headline
    first. An old one holds exactly the headline, so ``readings`` is that one reading (an
    evidence record with no reading has none, as an absent list). A comparison held the
    headline's ratio, noise and verdict at its top; it now holds one entry in ``objectives``
    (the same fields) and keeps its sample counts and its ``verdict``, which was the headline's
    and is now the result over all objectives. Nothing is invented: the other objectives of an
    old run were never recorded, so an old comparison compares the headline alone, and its
    verdict (even ``inconclusive``) is kept as it was decided. Receipts and events carry the
    same shapes, and are rewritten the same way, with what they told left as told.
    """
    return {**_object(_rewritten(envelope)), "schema_version": 43}


def _v43_to_v44(envelope: JsonObject) -> JsonObject:
    """Version 44 reads the JSON documents the envelope holds as text at the version 43 shapes.

    Version 43 rewrote the fields the envelope types, but a run also keeps documents as text: an
    operation's request and outcome, a flow's recorded result, its checkpoint and its published
    record (`_EMBEDDED_JSON`). A save of version 42 still held a sample's or a record's single
    ``reading`` and a comparison's flat headline inside them, where the version 43 models refuse
    them, so no run saved by version 42 with a benchmark in its history could be resumed. Each
    such document is parsed, rewritten the way version 43 rewrote the envelope, and written
    back; a document that holds none of those shapes is left exactly as it was written, so no
    request and no digest that names it changes. Nothing is invented and nothing decided is
    decided again: the rewrite only restates a reading or a verdict in the shape that holds it.
    """
    return {**_object(_embedded(envelope)), "schema_version": 44}


_EMBEDDED_JSON: Final = (
    "payload_json",
    "outcome_json",
    "result_json",
    "snapshot_json",
    "published",
    "output_json",
    "attrs_json",
)
"""The fields of the envelope that hold a JSON document as text."""
_CANONICAL_JSON: Final = frozenset({"payload_json"})
"""Of those, the ones whose text is canonical (keys sorted at every level): a request is proved
byte for byte against the canonical text of the value it decodes to, so a request rewritten here
stays that text."""


def _embedded(value: JsonValue) -> JsonValue:
    """``value`` with the JSON documents held as text in it at the version 43 shapes."""
    if isinstance(value, list):
        return [_embedded(item) for item in value]
    if not isinstance(value, dict):
        return value
    return _restated(
        value,
        {
            key: _embedded_text(item, canonical=key in _CANONICAL_JSON)
            if key in _EMBEDDED_JSON and isinstance(item, str)
            else _embedded(item)
            for key, item in value.items()
        },
    )


def _embedded_text(text: str, *, canonical: bool = False) -> str:
    """``text``, a JSON document, at the version 43 shapes (``canonical``: with sorted keys).

    Any other text, and a document that has none of the old shapes, is returned as it is.
    """
    try:
        document = json.loads(text)
    except ValueError:
        return text
    if not isinstance(document, dict | list):
        return text
    rewritten = _rewritten(_embedded(document))
    if rewritten == document:
        return text
    return json.dumps(rewritten, separators=(",", ":"), ensure_ascii=False, sort_keys=canonical)


def upgrade_document_shapes(source: str) -> str:
    """``source``, a persisted JSON document, at the version 43 shapes.

    The envelope is not the only document a run keeps its samples, evidence records and
    comparisons in (the publication journal holds its events), and none of the others carries a
    version: the rewrite is the one of the migration to 44, which leaves a document that is
    already in the shapes, and any text that is no JSON document, as it is.
    """
    return _embedded_text(source)


_OBJECTIVE_FIELDS: Final = (
    "metric",
    "unit",
    "direction",
    "candidate_value",
    "base_value",
    "ratio",
    "noise",
)
"""What a version 42 comparison stated of its one (headline) objective, less its verdict, which
the comparison keeps and the objective repeats."""


def _rewritten(value: JsonValue) -> JsonValue:
    """``value`` with every sample, evidence record and comparison in it at the version 43 shape."""
    if isinstance(value, list):
        return [_rewritten(item) for item in value]
    if not isinstance(value, dict):
        return value
    node = _restated(value, {key: _rewritten(item) for key, item in value.items()})
    if "reading" in node and ("evidence_id" in node or {"run", "measured_input"} <= node.keys()):
        node["readings"] = [node.pop("reading")]
    elif {"candidate_input", "base_input", "candidate_value", "metric"} <= node.keys():
        objective = {key: node.pop(key) for key in _OBJECTIVE_FIELDS if key in node}
        node["objectives"] = [{**objective, "verdict": node["verdict"]}]
    return node


_COVERED_BY_A_DIGEST: Final = (("decision", "payload_digest"), ("request", "payload_digest"))
"""The documents a record keeps beside the digest of the text it was proven by: a decision's
receipt, and a request's record (`vs_core._proofs`)."""


def _digest(document: JsonValue) -> str:
    """The fingerprint core states for a value, read off its JSON: sorted keys, no whitespace."""
    text = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256(text.encode()).hexdigest()


def _restated(old: JsonObject, new: JsonObject) -> JsonObject:
    """``new`` with the digest of a decision or request this step rewrote stated again.

    A receipt and a request record prove what they hold by a digest of it, and resuming refuses a
    save whose proof no longer holds ("accepted decision integrity failed"): rewriting the
    document leaves its saved digest standing for text that is gone. The digest is restated only
    where it is certain to be the same fingerprint: the saved one was the digest of the saved
    document as read off its JSON, so that of the rewritten document, read the same way, is what
    core states for it. Any other digest is left as it was and resuming refuses the save.
    """
    node = new
    for cover, field in _COVERED_BY_A_DIGEST:
        if (
            field in old
            and cover in old
            and cover in new
            and new[cover] != old[cover]
            and old[field] == _digest(old[cover])
        ):
            node = {**node, field: _digest(new[cover])}
    return node


def _floor_declared(core: JsonObject) -> bool:
    run = _object(core.get("run"))
    task = _object(_object(run.get("facts")).get("task"))
    return _positive(task.get("relative_noise"))


def _is_unmeasured(item: JsonValue) -> bool:
    """Whether a held comparison called ``within_noise`` has no estimate of noise (no floor)."""
    if not isinstance(item, dict) or item.get("verdict") != "within_noise":
        return False
    candidate, base = item.get("candidate_samples"), item.get("base_samples")
    if not (isinstance(candidate, int) and isinstance(base, int)):
        return False
    return not (min(candidate, base) >= _MIN_RUNS_WITHOUT_FLOOR and _positive(item.get("noise")))


def _positive(value: JsonValue) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and value > 0


def _object(value: JsonValue) -> JsonObject:
    return value if isinstance(value, dict) else {}


MIGRATIONS: Final[Mapping[int, Migration]] = {
    34: _v34_to_v35,
    35: _v35_to_v36,
    36: _v36_to_v37,
    37: _v37_to_v38,
    38: _v38_to_v39,
    39: _v39_to_v40,
    40: _v40_to_v41,
    41: _v41_to_v42,
    42: _v42_to_v43,
    43: _v43_to_v44,
}
"""Version ``n`` to ``n + 1``, one entry per version, contiguous from the oldest supported."""

OLDEST_SUPPORTED_ENVELOPE_VERSION: Final = min(MIGRATIONS)
"""The oldest envelope version a run can still be resumed from."""


def upgrade_envelope_document(envelope: JsonObject, *, to: int) -> JsonObject:
    """``envelope`` (a version in the chain) rewritten step by step into version ``to``.

    The caller has checked that the version is supported. A missing step, or a step that
    leaves another version than it targets, is a defect of this registry (its test catches
    it) and raises here rather than being skipped.
    """
    version = envelope.get("schema_version")
    while isinstance(version, int) and version < to:
        step = MIGRATIONS.get(version)
        if step is None:
            message = f"no migration registered for envelope version {version}"
            raise LookupError(message)
        envelope = step(envelope)
        version += 1
        if envelope.get("schema_version") != version:
            message = f"the migration of envelope version {version - 1} did not set {version}"
            raise LookupError(message)
    return envelope
