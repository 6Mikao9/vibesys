"""Evidence is offered to a decision only when it is trusted and is exactly the asked measurement's."""

from __future__ import annotations

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from vs_core.api import (
    EvidenceId,
    EvidenceKind,
    EvidenceRef,
    MeasurementFailure,
    MeasurementResult,
    ObservationStatus,
    RequestId,
    RevisionId,
    RevisionRef,
    RunId,
    Scope,
    initial_state,
    judged_keys,
    ledger_refs,
    project,
    trusted_keys,
)
from vs_sim.api.testing import examples

SCOPE = Scope(owner=RunId(root="run"), generation=0)
CANDIDATE = RevisionRef(revision_id=RevisionId(root="c"), digest="digest-c")
OTHER = RevisionRef(revision_id=RevisionId(root="o"), digest="digest-o")
REQUEST = RequestId(root="request")


def _ref(name: str, **changes: object) -> EvidenceRef:
    ref = EvidenceRef(
        evidence_id=EvidenceId(root=name),
        kind=EvidenceKind.BENCHMARK,
        purpose="official",
        scope=SCOPE,
        source_request=REQUEST,
        candidate=CANDIDATE,
        observation_sequence=1,
        evaluator_digest="e",
        workload_digest="w",
        environment_digest="n",
        provenance="trusted",
        status=ObservationStatus.SUCCEEDED,
    )
    return ref.model_copy(update=changes)


_FOREIGN = {"source_request": RequestId(root="foreign")}
_FLAWS = st.sampled_from(
    [
        {"provenance": "self-report"},
        {"candidate": OTHER},
        {"purpose": "baseline"},
        {"scope": Scope(owner=RunId(root="run"), generation=1)},
        _FOREIGN,
    ]
)


@given(flaws=st.lists(st.one_of(st.none(), _FLAWS), max_size=6), pinned=st.booleans())
def test_only_unflawed_records_of_the_measurement_are_kept(
    *, flaws: list[dict[str, object] | None], pinned: bool
) -> None:
    refs = tuple(_ref(f"e{i}", **(flaw or {})) for i, flaw in enumerate(flaws))
    result = MeasurementResult(
        scope=SCOPE,
        source_request=REQUEST if pinned else None,
        evidence=refs,
        status=ObservationStatus.SUCCEEDED,
    )
    kept = trusted_keys(result, scope=SCOPE, candidate=CANDIDATE, purpose="official")
    # Another request's record is dropped only when the result names its own request.
    expected = tuple(
        ref.key
        for ref, flaw in zip(refs, flaws, strict=True)
        if flaw is None or (flaw == _FOREIGN and not pinned)
    )
    assert kept == expected


@pytest.mark.regression
@given(
    statuses=st.lists(
        st.sampled_from((ObservationStatus.SUCCEEDED, ObservationStatus.FAILED)), max_size=6
    ),
    failure=st.none() | st.sampled_from(MeasurementFailure),
)
@example(statuses=[ObservationStatus.FAILED], failure=MeasurementFailure.INFRASTRUCTURE)
@example(
    statuses=[ObservationStatus.SUCCEEDED, ObservationStatus.FAILED],
    failure=MeasurementFailure.AMBIGUOUS,
)
def test_a_gate_that_failed_in_a_measurement_that_judged_nothing_is_no_verdict_on_the_candidate(
    statuses: list[ObservationStatus], failure: MeasurementFailure | None
) -> None:
    """A host that refused, or a node that was lost, made the gate fail, not the candidate.

    Regression: a trusted accuracy gate refused for want of free disk, its failed evidence was
    decoded as the candidate failing the gate, and the candidate was rejected and discarded.
    """
    refs = tuple(_ref(f"e{i}", status=status) for i, status in enumerate(statuses))
    result = MeasurementResult(
        scope=SCOPE,
        source_request=REQUEST,
        evidence=refs,
        status=ObservationStatus.FAILED,
        failure=failure,
    )

    kept = judged_keys(result, scope=SCOPE, candidate=CANDIDATE, purpose="official")

    judged = failure is None or failure.charges_candidate
    # what succeeded is a fact either way; what failed is a verdict only when it judged
    assert kept == tuple(
        ref.key for ref in refs if judged or ref.status is ObservationStatus.SUCCEEDED
    )
    # the input's measurement carries the blame beside the failed record, so it reads them all
    assert trusted_keys(result, scope=SCOPE, candidate=CANDIDATE, purpose="official") == tuple(
        ref.key for ref in refs
    )


@settings(max_examples=examples(pr=4, nightly=100))
@given(held=st.integers(0, 4), asked=st.integers(0, 4))
def test_the_ledger_must_hold_every_requested_record_in_order(held: int, asked: int) -> None:
    refs = tuple(_ref(f"e{i}") for i in range(max(held, asked)))
    view = project(initial_state()).model_copy(update={"measurements": refs[:held]})
    keys = tuple(ref.key for ref in refs[:asked])
    assert ledger_refs(view, keys) == (refs[:asked] if asked <= held else None)
