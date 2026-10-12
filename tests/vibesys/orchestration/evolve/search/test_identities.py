"""Child keys are one component that sorts in schedule order, so the frame admits in slot order."""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from vibesys.orchestration.evolve.search.api import IDS, bootstrap_key, child_key

_NUMBER = st.integers(1, 999_999)


@given(generation=_NUMBER, slot=_NUMBER)
def test_keys_are_one_component(generation: int, slot: int) -> None:
    for key in (bootstrap_key(slot), child_key(generation, slot)):
        assert key.split() == [key]
        assert "/" not in key
        assert ":" not in key


@given(
    generation=st.integers(1, 500_000),
    gap=st.integers(1, 499_999),
    first=_NUMBER,
    second=_NUMBER,
)
def test_keys_sort_in_the_order_the_work_was_scheduled(
    generation: int, gap: int, first: int, second: int
) -> None:
    assert child_key(generation, first) < child_key(generation + gap, second)
    assert (child_key(generation, first) < child_key(generation, second)) == (first < second)
    assert (bootstrap_key(first) < bootstrap_key(second)) == (first < second)


@given(generation=_NUMBER, slot=_NUMBER)
def test_the_key_is_one_component_of_an_invocation_id(generation: int, slot: int) -> None:
    root = IDS.invocation_id("mutator", child_key(generation, slot), 1).root
    # inv:<role>:<key>:<serial>: the key adds no separator of its own.
    assert root.count(":") == 3
