"""A strategy's identities hold no whitespace, stay distinct per subject and invert for operations."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from vs_core.api import IdScheme, attempt_id, component, decision_of_operation, item_id

_SUBJECTS = st.text(max_size=40).filter(bool)
_NAMESPACES = st.from_regex(r"[a-z]{1,8}", fullmatch=True).filter(lambda name: name != "operation")


@given(namespace=_NAMESPACES, first=_SUBJECTS, second=_SUBJECTS)
def test_identities_accept_any_subject_and_keep_distinct_subjects_distinct(
    namespace: str, first: str, second: str
) -> None:
    scheme = IdScheme(namespace)
    for build in (
        lambda subject: scheme.decision_id("turn", subject, 3).root,
        lambda subject: scheme.invocation_id("role", subject, 3).root,
        lambda subject: scheme.session_id("role", subject).root,
        lambda subject: attempt_id(subject, 3).root,
        lambda subject: item_id(subject, 3).root,
    ):
        built = build(first)
        assert built.split() == [built]
        assert (build(first) == build(second)) == (first == second)


@given(first=_NAMESPACES, second=_NAMESPACES, subject=_SUBJECTS)
def test_two_namespaces_never_share_a_decision_identity(
    first: str, second: str, subject: str
) -> None:
    one = IdScheme(first).decision_id("turn", subject).root
    other = IdScheme(second).decision_id("turn", subject).root
    assert (one == other) == (first == second)


@given(namespace=_NAMESPACES, subject=_SUBJECTS)
def test_the_operation_identity_of_a_decision_inverts_to_that_decision(
    namespace: str, subject: str
) -> None:
    decision = IdScheme(namespace).decision_id("verify", subject).root
    assert decision_of_operation(f"operation:{decision}") == decision
    assert decision_of_operation(decision) is None


@given(namespace=st.text(max_size=8).filter(lambda name: name != component(name)))
def test_a_namespace_that_needs_encoding_is_refused(namespace: str) -> None:
    with pytest.raises(ValueError, match="id namespace"):
        IdScheme(namespace)


def test_the_namespace_of_core_operation_identities_is_refused() -> None:
    with pytest.raises(ValueError, match="operation"):
        IdScheme("operation")
