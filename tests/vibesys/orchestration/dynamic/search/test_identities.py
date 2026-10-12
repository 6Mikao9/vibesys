"""Child keys encode any agent-chosen work ID as one component that sorts in schedule order."""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from vibesys.orchestration.dynamic.search.api import IDS, planning_key, workstream_key

_SUBJECTS = st.text(max_size=40).filter(bool)


@given(first=_SUBJECTS, second=_SUBJECTS, sequence=st.integers(1, 999_999))
def test_keys_accept_any_work_id_and_keep_distinct_work_distinct(
    first: str, second: str, sequence: int
) -> None:
    built = workstream_key(sequence, first)
    assert built.split() == [built]
    assert "/" not in built
    assert (built == workstream_key(sequence, second)) == (first == second)


@given(
    earlier=st.integers(1, 500_000),
    gap=st.integers(1, 499_999),
    first=_SUBJECTS,
    second=_SUBJECTS,
)
def test_keys_sort_in_the_order_the_work_was_scheduled(
    earlier: int, gap: int, first: str, second: str
) -> None:
    assert workstream_key(earlier, first) < workstream_key(earlier + gap, second)


@given(call=st.integers(1, 1000))
def test_planning_keys_are_one_component(call: int) -> None:
    key = planning_key(call)
    assert key.split() == [key]
    assert "/" not in key


@given(subject=_SUBJECTS)
def test_the_subject_is_one_component_whatever_separators_it_holds(subject: str) -> None:
    root = IDS.invocation_id("judge", subject, 1).root
    # inv:<role>:<subject>:<serial>: the subject adds no separator of its own.
    assert root.count(":") == 3
