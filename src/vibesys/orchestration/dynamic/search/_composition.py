"""Which separate trusted measurements may decide a revision, and which are only shown.

An implementer may run the task's gates as separate jobs, in any order. Core trusts each
job on its own: a plan that omits the accuracy stage cannot name ``accuracy_gated``, so a
benchmark-only job is sound on the stages it ran. Joining those jobs is this module's job.
A benchmark from a plan that did not run the task's accuracy stage decides retention, a
parent offer, adoption or main only when that same revision already has a passing official
accuracy reading under the task's accuracy recipe. Otherwise the benchmark is shown and
never enters the evidence those decisions read. A reading from another recipe, purpose,
scope or revision is not paired with it and is not shown as this revision's benchmark.
"""

from __future__ import annotations

from vs_core.api.search import (
    EvidenceKind,
    EvidenceRef,
    MeasurementPlan,
    MeasurementResult,
    ObservationStatus,
    RevisionRef,
    Scope,
    Value,
)


class MeasuredJob(Value):
    """One trusted plan and the measurement that answered it."""

    plan: MeasurementPlan
    result: MeasurementResult


class ComposedJobs(Value):
    """The evidence decisions may use, and benchmarks that are only shown.

    ``decisive`` is what settlement, a parent offer, adoption and main read. ``unverified``
    is benchmark evidence of this revision under the task recipe whose plan omitted accuracy
    and which no passing official accuracy of that recipe covers yet.
    """

    decisive: MeasurementResult
    unverified: tuple[EvidenceRef, ...] = ()


def compose_jobs(
    jobs: tuple[MeasuredJob, ...],
    *,
    recipe_digest: str,
    accuracy_stage: str | None,
) -> ComposedJobs:
    """Join ``jobs`` into the evidence of one revision.

    ``recipe_digest`` and ``accuracy_stage`` are the task's. A profile measurement is
    returned as it ran: nothing is scored from it. When the task names no accuracy stage,
    an official benchmark of the task recipe needs no accuracy reading. When it does, a
    benchmark whose own plan omitted that stage stays out of ``decisive`` until a passing
    official accuracy of the same revision, scope and recipe is among ``jobs``, and any
    failed official accuracy of that same key withdraws the proof.
    """
    if not jobs:
        message = "compose_jobs needs a measurement"
        raise ValueError(message)
    if all(job.plan.purpose == "profile" for job in jobs):
        return ComposedJobs(decisive=_joined(jobs))
    proofs = _proofs(jobs, recipe_digest=recipe_digest, accuracy_stage=accuracy_stage)
    decisive: list[EvidenceRef] = []
    unverified: list[EvidenceRef] = []
    for job in jobs:
        for ref in job.result.evidence:
            if _benchmark_counts(job, ref, proofs, recipe_digest, accuracy_stage):
                decisive.append(ref)
            elif _informational(job, ref, recipe_digest, accuracy_stage, proofs):
                unverified.append(ref)
            elif _counts_with_its_accuracy(job, ref, recipe_digest, accuracy_stage):
                decisive.append(ref)
    return ComposedJobs(
        decisive=_joined(jobs, tuple(decisive)),
        unverified=tuple(unverified),
    )


def _proofs(
    jobs: tuple[MeasuredJob, ...], *, recipe_digest: str, accuracy_stage: str | None
) -> set[tuple[RevisionRef, Scope]]:
    """Revisions whose official accuracy under the task recipe passed and did not fail."""
    passed: set[tuple[RevisionRef, Scope]] = set()
    failed: set[tuple[RevisionRef, Scope]] = set()
    for job in jobs:
        if not _includes_accuracy(job.plan, accuracy_stage):
            continue
        for ref in job.result.evidence:
            if ref.kind is not EvidenceKind.CORRECTNESS or not _official(job, ref, recipe_digest):
                continue
            key = (ref.candidate, ref.scope)
            if ref.status is ObservationStatus.SUCCEEDED:
                passed.add(key)
            elif ref.status is ObservationStatus.FAILED:
                failed.add(key)
    return passed - failed


def _benchmark_counts(
    job: MeasuredJob,
    ref: EvidenceRef,
    proofs: set[tuple[RevisionRef, Scope]],
    recipe_digest: str,
    accuracy_stage: str | None,
) -> bool:
    """Whether ``ref`` is a benchmark a decision may use."""
    if ref.kind is not EvidenceKind.BENCHMARK or not _official(job, ref, recipe_digest):
        return False
    if accuracy_stage is None or _includes_accuracy(job.plan, accuracy_stage):
        return True
    return (ref.candidate, ref.scope) in proofs


def _informational(
    job: MeasuredJob,
    ref: EvidenceRef,
    recipe_digest: str,
    accuracy_stage: str | None,
    proofs: set[tuple[RevisionRef, Scope]],
) -> bool:
    """An official task-recipe benchmark whose plan omitted accuracy and which lacks a proof."""
    return (
        accuracy_stage is not None
        and ref.kind is EvidenceKind.BENCHMARK
        and _official(job, ref, recipe_digest)
        and not _includes_accuracy(job.plan, accuracy_stage)
        and (ref.candidate, ref.scope) not in proofs
    )


def _counts_with_its_accuracy(
    job: MeasuredJob, ref: EvidenceRef, recipe_digest: str, accuracy_stage: str | None
) -> bool:
    """Evidence of a plan that ran the task's accuracy stage, other than its benchmark.

    The benchmark of that plan is decided in ``_benchmark_counts``. Core already applied
    the plan's gate to the job; a correctness reading from it, pass or fail, is decisive.
    """
    return (
        ref.kind is not EvidenceKind.BENCHMARK
        and _includes_accuracy(job.plan, accuracy_stage)
        and _official(job, ref, recipe_digest)
    )


def _includes_accuracy(plan: MeasurementPlan, accuracy_stage: str | None) -> bool:
    """Whether ``plan`` ran the task's accuracy stage, so core gated the job itself."""
    return accuracy_stage is not None and any(
        stage.stage_id == accuracy_stage for stage in plan.stages
    )


def _official(job: MeasuredJob, ref: EvidenceRef, recipe_digest: str) -> bool:
    """Whether ``ref`` is trusted official evidence of ``job``'s revision under the task recipe.

    ``reused_from`` does not matter: an official record core reused is the official answer.
    A local-validation record is not, and neither is another recipe, scope or revision.
    """
    plan = job.plan
    return (
        plan.purpose == "official"
        and plan.recipe.digest == recipe_digest
        and isinstance(plan.candidate, RevisionRef)
        and ref.purpose == "official"
        and ref.provenance == "trusted"
        and ref.scope == job.result.scope
        and ref.candidate == plan.candidate
    )


def _joined(
    jobs: tuple[MeasuredJob, ...], refs: tuple[EvidenceRef, ...] | None = None
) -> MeasurementResult:
    """One result holding ``refs``, or every job's evidence when ``refs`` is omitted.

    One job whose evidence is unchanged is that job's result, so its submission request
    still keys it. Several jobs name no single request: a later reader accepts each
    record on its own scope, candidate and purpose.
    """
    if refs is None:
        refs = tuple(ref for job in jobs for ref in job.result.evidence)
    if len(jobs) == 1 and tuple(jobs[0].result.evidence) == refs:
        return jobs[0].result
    keys = {ref.key for ref in refs}
    contributed = [job for job in jobs if any(ref.key in keys for ref in job.result.evidence)]
    failed = next(
        (job.result for job in contributed if job.result.status is not ObservationStatus.SUCCEEDED),
        None,
    )
    base = jobs[-1].result
    return base.model_copy(
        update={
            "source_request": None if len(jobs) > 1 else jobs[0].result.source_request,
            "scope": refs[0].scope if refs else base.scope,
            "evidence": refs,
            "status": ObservationStatus.SUCCEEDED if failed is None else failed.status,
            "failure": None if failed is None else failed.failure,
            "diagnostic": "\n".join(job.result.diagnostic for job in jobs if job.result.diagnostic),
        }
    )
