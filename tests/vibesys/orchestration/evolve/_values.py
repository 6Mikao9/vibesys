"""Generated values of the evolve search: children, closings, settlements and finished children.

The pure helpers of the search take plain values, so their tests draw those values here and call
the helpers directly.
"""

from __future__ import annotations

from hypothesis import strategies as st
from tests.vibesys.orchestration.evolve._host import config

from vibesys.metrics import AcceptedReading, MetricRow
from vibesys.orchestration.evolve.search.api import (
    ChildDone,
    ChildStart,
    Closing,
    EvolveConfig,
    Failure,
    FailureKind,
    MutatePrompt,
    ProfileReading,
    SlotProposal,
)
from vs_core.api import (
    AttemptId,
    AttemptRef,
    EvidenceId,
    EvidenceKind,
    RequestId,
    RevisionRef,
    Settlement,
    SettlementId,
)

BASE = RevisionRef.of_git_commit("base")
CONFIG: EvolveConfig = config()


def revision(name: str) -> RevisionRef:
    """A revision named ``name``."""
    return RevisionRef.of_git_commit(name)


def start(key: str = "bootstrap.1", *, cold: bool = True, parent: int | None = None) -> ChildStart:
    """A scheduled child: a cold start, or the child of the individual ``parent``."""
    return ChildStart(
        key=key,
        slot=1,
        generation=0 if cold else 1,
        proposal=None if cold else SlotProposal(parent_id=parent or 1),
        base=BASE,
        prompt=MutatePrompt(base_revision=BASE, is_cold_start=cold),
        config=CONFIG,
    )


def reading(
    kind: EvidenceKind, *, passed: bool = True, value: float | None = None
) -> AcceptedReading:
    """A decoded reading of one gate; a benchmark carries its throughput."""
    metrics = (
        (MetricRow(name="throughput", value=value, direction="max", unit="tokens/s"),)
        if value is not None
        else ()
    )
    return AcceptedReading(
        source_request=RequestId(root="measure"),
        evidence_id=EvidenceId(root=kind.value),
        kind=kind,
        passed=passed,
        stage=kind.value,
        metrics=metrics,
    )


def settlement(
    candidate: RevisionRef | None, *, eligible: bool = True, retention: str = "candidate"
) -> Settlement:
    """What core settled for a child's workspace."""
    return Settlement(
        settlement_id=SettlementId(root=f"settlement-{candidate and candidate.revision_id.root}"),
        attempt=AttemptRef(attempt_id=AttemptId(root="attempt"), generation=0),
        candidate=candidate,
        assessments=(),
        eligible=eligible,
        retention=retention,  # ty: ignore[invalid-argument-type]
        outcome="succeeded" if eligible else "failed",
    )


def passing_closing(commit: str, value: float, *, summary: str = "") -> Closing:
    """A child that passed review and both gates with this throughput."""
    return Closing(
        revision=revision(commit),
        summary=summary or f"change {commit}",
        readings=(
            reading(EvidenceKind.CORRECTNESS),
            reading(EvidenceKind.BENCHMARK, value=value),
        ),
        passed=True,
    )


def passing(
    key: str, commit: str, value: float, *, cold: bool = True, parent: int | None = None
) -> ChildDone:
    """A child that passed and that core retained as eligible."""
    return ChildDone(
        start(key, cold=cold, parent=parent),
        passing_closing(commit, value),
        settlement(revision(commit)),
    )


def failing(key: str, *, cold: bool = True, commit: str | None = None) -> ChildDone:
    """A child the judge rejected; a cold start keeps its work in progress when it left some."""
    closing = Closing(
        revision=None if commit is None else revision(commit),
        feedback="needs work",
        passed=False,
    )
    return ChildDone(
        start(key, cold=cold),
        closing,
        settlement(
            None if commit is None else revision(commit),
            eligible=False,
            retention="discard" if commit is None or not cold else "wip",
        ),
    )


@st.composite
def closings(draw: st.DrawFn) -> Closing:
    """Any closing a child can end with: passing, failed by a gate, or ended by a cause."""
    revision_ = draw(st.none() | st.sampled_from("abc").map(revision))
    cause = draw(st.none() | st.sampled_from(list(FailureKind)).map(lambda k: Failure(kind=k)))
    readings = draw(
        st.sampled_from(
            [
                (),
                (reading(EvidenceKind.CORRECTNESS), reading(EvidenceKind.BENCHMARK, value=90.0)),
                (reading(EvidenceKind.CORRECTNESS, passed=False),),
                (reading(EvidenceKind.CORRECTNESS), reading(EvidenceKind.BENCHMARK, passed=False)),
            ]
        )
    )
    profile = draw(
        st.none()
        | st.just(ProfileReading(perf_metric=7.0, perf_unit="x", metrics=(("cache_hit", 0.4),)))
    )
    return Closing(
        revision=revision_,
        summary=draw(st.sampled_from(["", "tweak"])),
        feedback=draw(st.sampled_from(["", "needs work"])),
        readings=readings,
        profile=profile,
        patch=draw(st.none() | st.just("diff")),
        passed=draw(st.booleans()) and cause is None and revision_ is not None,
        cause=cause,
    )


@st.composite
def finished_children(draw: st.DrawFn) -> ChildDone:
    """Any child the search can record, whatever core settled for it."""
    cold = draw(st.booleans())
    closing = draw(closings())
    settled = draw(st.booleans())
    retention = draw(st.sampled_from(["discard", "wip", "candidate"]))
    eligible = draw(st.booleans())
    refused = draw(st.none() | st.just(Failure(kind=FailureKind.NOT_OPENED)))
    return ChildDone(
        start("bootstrap.1" if cold else "g1.c1", cold=cold),
        closing,
        settlement(closing.revision, eligible=eligible, retention=retention) if settled else None,
        refused,
    )
