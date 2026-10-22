"""A flow sees every sample the ledger holds of a revision, whichever scope took it.

The strategy measures the input once, and core takes more runs of it in attempts' scopes when a
comparison is a close call. A decision that compares with the input reads all of them
(``ctx.evaluation.recorded``), so it must offer the records that give core a sample (not a
re-read) of the revision for the purpose under the recipe's evaluator, workload and
environment, and no other.
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

import vs_core.api as core
from vs_core.api.testing import FakeView

from .flow_support import Config, start

INPUT = core.RevisionRef.of_git_commit("input")
OTHER = core.RevisionRef.of_git_commit("other")
RECIPE = core.MeasurementRecipe(
    evaluator_digest="e",
    workload_digest="w",
    environment_digest="n",
    recipe=core.ArtifactRef(artifact_id=core.ArtifactId(root="recipe"), digest="recipe"),
    stages=(),
    queue_allowance=0.0,
    accuracy_gated=(),
)


class Seen(core.Value):
    """The evidence IDs the flow was offered."""

    ids: tuple[str, ...]


def record(
    name: str, sequence: int, owner: core.RunId | core.AttemptId, **changes: object
) -> core.EvidenceRef:
    """A conclusive trusted baseline benchmark of the input, taken in ``owner``'s scope."""
    ref = core.EvidenceRef(
        evidence_id=core.EvidenceId(root=name),
        kind=core.EvidenceKind.BENCHMARK,
        purpose="baseline",
        scope=core.Scope(owner=owner, generation=0),
        source_request=core.RequestId(root=f"request-{name}"),
        candidate=INPUT,
        observation_sequence=sequence,
        evaluator_digest="e",
        workload_digest="w",
        environment_digest="n",
        provenance="trusted",
        status=core.ObservationStatus.SUCCEEDED,
        reading=core.Reading(metric="rate", value=1.0, direction="max"),
    )
    return ref.model_copy(update=changes)


FOREIGN = {
    "another revision": {"candidate": OTHER},
    "another purpose": {"purpose": "official"},
    "accuracy": {"kind": core.EvidenceKind.CORRECTNESS},
    "no reading": {"reading": None},
    "re-read of another run": {"reused_from": core.RequestId(root="source")},
    "untrusted": {"provenance": "self-report"},
    "failed": {"status": core.ObservationStatus.FAILED},
    "another evaluator": {"evaluator_digest": "x"},
    "another workload": {"workload_digest": "x"},
    "another environment": {"environment_digest": "x"},
}
RUN = core.RunId(root="run")


def offered(measurements: tuple[core.EvidenceRef, ...]) -> tuple[str, ...]:
    """The evidence IDs ``recorded`` offers for the input's baseline benchmarks."""

    async def decide(ctx: core.Flow, _config: Config) -> None:
        found = ctx.evaluation.recorded(INPUT, purpose="baseline", recipe=RECIPE)
        ctx.publish(Seen(ids=tuple(item.evidence_id.root for item in found)))

    run = start(decide, view=FakeView(changes={"measurements": measurements}))
    seen = run.published(Seen)
    assert seen is not None
    return seen.ids


@given(
    sequences=st.lists(st.integers(1, 50), unique=True, max_size=6),
    attempts=st.lists(st.sampled_from(["a", "b", "run"]), min_size=6, max_size=6),
    flaws=st.lists(st.sampled_from(sorted(FOREIGN)), max_size=6),
)
def test_every_conclusive_trusted_sample_of_the_revision_is_offered_oldest_first_from_any_scope(
    sequences: list[int], attempts: list[str], flaws: list[str]
) -> None:
    def owner(name: str) -> core.RunId | core.AttemptId:
        return RUN if name == "run" else core.AttemptId(root=name)

    mine = [record(f"mine-{n}", n, owner(attempts[i])) for i, n in enumerate(sequences)]
    foreign = [record(f"foreign-{i}", 1, RUN, **FOREIGN[name]) for i, name in enumerate(flaws)]
    assert offered((*foreign, *mine)) == tuple(f"mine-{n}" for n in sorted(sequences))
