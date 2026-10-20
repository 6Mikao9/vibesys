"""An envelope written by a newer VibeSys is refused by name, even at the same version.

A shape change that every saved document survives keeps ``ENVELOPE_SCHEMA_VERSION``
(``test_persisted_schema.py``), so an older build can meet an envelope of its own version that
holds a field or a closed-set value it does not know. It must refuse it as written by a newer
VibeSys, naming what it does not know, not fail with a raw validation error.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

import vs_core.api as core
from vs_core.api.testing import FAKE_REGISTRY
from vs_sim.api.testing import examples

_Envelope = core.RunEnvelope[core.FlowState]
_SAVED = (Path(__file__).parent / "envelopes" / "v41-floor.json").read_text(encoding="utf-8")
_CURRENT = json.loads(
    FAKE_REGISTRY.encode_envelope(FAKE_REGISTRY.decode_envelope(_Envelope, _SAVED))
)
_NEWER = "written by a newer VibeSys"


def _objects(value: object, at: tuple[str | int, ...] = ()) -> list[tuple[str | int, ...]]:
    """The path of every JSON object inside ``value``, its own included."""
    found: list[tuple[str | int, ...]] = []
    if isinstance(value, dict):
        found.append(at)
        for key, item in value.items():
            found += _objects(item, (*at, key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found += _objects(item, (*at, index))
    return found


def _decode(document: object) -> None:
    FAKE_REGISTRY.decode_envelope(_Envelope, json.dumps(document))


def _at(document: object, path: tuple[str | int, ...]) -> dict[str, object]:
    for key in path:
        document = _child(document, key)
    assert isinstance(document, dict)
    return document


def _child(node: object, key: str | int) -> object:
    if isinstance(node, dict):
        return node[key]
    assert isinstance(node, list)
    assert isinstance(key, int)
    return node[key]


@pytest.mark.xfail(strict=True, reason="a same-version field from a newer VibeSys is not named yet")
@pytest.mark.parametrize(
    "path",
    [(), ("core",), ("core", "evaluation", "comparisons", 0)],
    ids=["root", "core", "nested"],
)
def test_a_field_this_vibesys_does_not_know_is_refused_as_written_by_a_newer_one(
    path: tuple[str | int, ...],
) -> None:
    document = json.loads(json.dumps(_CURRENT))
    _at(document, path)["added_later"] = 1
    with pytest.raises(core.ContractError, match=_NEWER) as refused:
        _decode(document)
    assert "added_later" in str(refused.value)


@pytest.mark.xfail(strict=True, reason="a same-version value from a newer VibeSys is not named yet")
def test_a_closed_set_member_this_vibesys_does_not_know_is_refused_as_written_by_a_newer_one() -> (
    None
):
    document = json.loads(json.dumps(_CURRENT))
    _at(document, ("core", "evaluation", "comparisons", 0))["verdict"] = "added_later"
    with pytest.raises(core.ContractError, match=_NEWER):
        _decode(document)


_OBJECTS = _objects(_CURRENT)


@pytest.mark.xfail(strict=True, reason="a same-version field from a newer VibeSys is not named yet")
@settings(max_examples=examples(pr=20, nightly=200), deadline=None)
@given(index=st.integers(0, len(_OBJECTS) - 1))
@example(index=0)
def test_an_unknown_key_anywhere_is_accepted_or_refused_as_from_a_newer_vibesys(index: int) -> None:
    """Some objects are free-form (option maps); everywhere else the key names a newer writer."""
    document = json.loads(json.dumps(_CURRENT))
    _at(document, _OBJECTS[index])["added_later"] = 1
    try:
        _decode(document)
    except core.ContractError as refused:
        message = str(refused)
    else:
        return
    assert _NEWER in message or "added_later" not in message


def test_an_older_envelope_with_an_unknown_field_is_not_blamed_on_a_newer_vibesys() -> None:
    """The newer-writer reading holds only at the current version; an old envelope is malformed."""
    document = json.loads(_SAVED)
    document["added_later"] = 1
    with pytest.raises(Exception, match="added_later") as refused:
        _decode(document)
    assert _NEWER not in str(refused.value)
