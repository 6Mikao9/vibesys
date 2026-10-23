# Generated from vibesys/core/v1/leaves.proto by protoc-gen-corevalue. DO NOT EDIT.
from __future__ import annotations

from enum import StrEnum
from functools import partial
from typing import Annotated, ClassVar, Literal, final

from pydantic import AfterValidator, ConfigDict, Field, field_serializer
from pydantic.dataclasses import dataclass

from vs_core._corevalue import (
    CoreValue,
    FrozenMap,
    TimestampValue,
    bounded_integer,
    cel_integer,
    omit_unset,
    resolve_value,
)
from vs_core._generated.vibesys.core.v1.common_cv import EvidenceId, RequestId, RevisionId, Scope
from vs_core._generated.vibesys.core.v1.vocabulary_cv import ComparisonVerdict
from vs_core._revision_ref import RevisionReference

Direction = Literal["max", "min"]

ChargeClass = Literal["free", "paid", "correction", "resume"]

TaskObjectiveDirection = Literal["max", "min"]

type Finite = Annotated[float, Field(allow_inf_nan=False)]


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class EvidenceKey(CoreValue):
    "Full identity of one evidence record: the request that produced it plus its ID.\n\nEvidenceId alone is only unique within one source request, so two jobs can\nreport the same ID. Every ledger, settlement and continuation lookup keys on\nthis pair."

    model_fields: ClassVar[tuple[str, ...]] = ("kind", "source_request", "evidence_id")
    kind: Literal["evidence_key"] = "evidence_key"
    source_request: RequestId
    evidence_id: EvidenceId

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class RevisionRef(RevisionReference, CoreValue):
    "Immutable revision identity.\n\nCore compares digest as an opaque equality key. Producers that mint a ref from\na git commit use of_git_commit, which makes revision_id the commit and digest\n`git-commit:<commit>`; consumers that must trust the scheme read git_commit,\nwhich is None unless digest names the same commit as revision_id."

    model_fields: ClassVar[tuple[str, ...]] = ("revision_id", "digest")
    revision_id: RevisionId
    digest: Annotated[str, Field(min_length=1)]

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class TaskStage(CoreValue):
    "One stage the task can measure and the seconds its execution may take."

    model_fields: ClassVar[tuple[str, ...]] = ("stage_id", "execution_seconds")
    stage_id: Annotated[str, Field(min_length=1)]
    execution_seconds: Annotated[float, Field(ge=0.0, allow_inf_nan=False)]

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class TaskObjective(CoreValue):
    "One axis the task compares candidates on, and which way is better."

    model_fields: ClassVar[tuple[str, ...]] = ("name", "direction", "noise")
    name: Annotated[str, Field(min_length=1)]
    direction: TaskObjectiveDirection
    noise: Annotated[ObjectiveNoise | None, Field(exclude_if=omit_unset)] = None

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class ObjectiveNoise(CoreValue):
    "Versioned, revisable run declaration for one objective's promotion tolerance.\nOriginal measurements and accepted decision receipts remain immutable."

    model_fields: ClassVar[tuple[str, ...]] = (
        "schema_version",
        "revision",
        "floor",
        "relative_noise",
        "provenance",
        "baseline_values",
    )
    schema_version: Annotated[
        int,
        Field(ge=1),
        AfterValidator(partial(bounded_integer, minimum=-2147483648, maximum=2147483647)),
    ] = 1
    revision: Annotated[int, Field(ge=1)] = 1
    floor: Annotated[float, Field(ge=0.0, lt=1.0, allow_inf_nan=False)]
    relative_noise: Annotated[float, Field(ge=0.0, lt=1.0, allow_inf_nan=False)]
    provenance: Annotated[str, Field(min_length=1)]
    baseline_values: tuple[NoiseSample, ...] = ()

    def __post_init__(self) -> None:
        """Enforce the proto message rules at construction as well as decode."""
        if not (self.schema_version == 1):
            raise ValueError("unsupported objective noise schema version")
        if not (self.relative_noise >= self.floor):
            raise ValueError("relative_noise must be at least floor")

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class NoiseSample(CoreValue):
    "One calibration observation in the declared units of its objective."

    model_fields: ClassVar[tuple[str, ...]] = ("value",)
    value: Annotated[float, Field(allow_inf_nan=False)]

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class Reading(CoreValue):
    "One objective's reading of a run's benchmark stage, as the trusted record states it."

    model_fields: ClassVar[tuple[str, ...]] = (
        "metric",
        "value",
        "direction",
        "unit",
        "repetitions",
    )
    metric: Annotated[str, Field(min_length=1)]
    value: Annotated[float, Field(allow_inf_nan=False)]
    direction: Direction | None = None
    unit: str | None = None
    repetitions: Annotated[int, Field(ge=1)] = 1

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class BaseRef(CoreValue):
    "The revision a candidate is compared with, and what the evaluation reads there.\n\nBoth facts together or neither: a base without its measured input could not be matched to\nits own samples."

    model_fields: ClassVar[tuple[str, ...]] = ("commit", "measured_input")
    commit: RevisionRef
    measured_input: Annotated[str, Field(min_length=1)]

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class Sample(CoreValue):
    "One framework run of one measured input: the unit of measurement, always traceable.\n\nA sample is built only from a run that ended conclusively with a trusted benchmark reading,\nand requires the ``commit`` it measured and the ``measured_input`` digest (#2961). The\n``base`` is set on a candidate's sample and names the revision it is compared with.\n``readings`` are the run's reading of every objective its benchmark reported, headline first."

    model_fields: ClassVar[tuple[str, ...]] = (
        "run",
        "scope",
        "commit",
        "measured_input",
        "evaluation",
        "gates",
        "host",
        "dedicated_resources",
        "observed_at",
        "base",
        "readings",
    )
    run: RequestId
    scope: Scope
    commit: RevisionRef
    measured_input: Annotated[str, Field(min_length=1)]
    evaluation: Annotated[str, Field(min_length=1)]
    gates: tuple[str, ...]
    host: Annotated[str, Field(min_length=1)]
    dedicated_resources: bool
    observed_at: Annotated[float, Field(ge=0.0, allow_inf_nan=False)]
    base: BaseRef | None = None
    readings: Annotated[tuple[Reading, ...], Field(min_length=1)]

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class ObjectiveComparison(CoreValue):
    "How a candidate's samples stand against its base's on one objective: the ratio, its noise and\nthe verdict.\n\n``ratio`` is the candidate's mean over the base's, as measured; ``direction`` says which way is\nbetter, so ``better`` is a ratio above one for ``max`` and below one for ``min``. ``noise`` is\none standard error of the ratio, and a verdict needs two of them: the statistic is in\n``vs_core._dominance``. ``verdict`` is ``better`` or ``worse`` beyond the objective's tolerance,\n``within_noise`` inside it, and ``inconclusive`` when the objective cannot be judged (a base\nmean of zero)."

    model_fields: ClassVar[tuple[str, ...]] = (
        "metric",
        "unit",
        "direction",
        "candidate_value",
        "base_value",
        "ratio",
        "noise",
        "verdict",
    )
    metric: Annotated[str, Field(min_length=1)]
    unit: str | None = None
    direction: Direction
    candidate_value: Annotated[float, Field(allow_inf_nan=False)]
    base_value: Annotated[float, Field(allow_inf_nan=False)]
    ratio: Annotated[float, Field(allow_inf_nan=False)]
    noise: Annotated[float, Field(ge=0.0, allow_inf_nan=False)]
    verdict: ComparisonVerdict

    __hash__ = CoreValue.cached_hash


@final
@dataclass(
    frozen=True,
    slots=True,
    kw_only=True,
    config=ConfigDict(extra="forbid", strict=True, validate_default=True),
)
class Comparison(CoreValue):
    "A candidate's samples compared with its base's on every objective, and the dominance result.\n\n``objectives`` holds one comparison per objective every sample reports, headline first.\n``verdict`` is the one dominance rule over them (``vs_core._dominance``): ``worse`` when some\nobjective is worse beyond its tolerance, else ``better`` when some objective is better beyond\nit (the candidate dominates the base), else ``inconclusive`` when some objective cannot be\njudged, else ``within_noise``."

    model_fields: ClassVar[tuple[str, ...]] = (
        "scope",
        "candidate",
        "candidate_input",
        "base",
        "base_input",
        "verdict",
        "candidate_samples",
        "base_samples",
        "objectives",
    )
    scope: Scope
    candidate: RevisionRef
    candidate_input: Annotated[str, Field(min_length=1)]
    base: RevisionRef
    base_input: Annotated[str, Field(min_length=1)]
    verdict: ComparisonVerdict
    candidate_samples: Annotated[int, Field(ge=0)]
    base_samples: Annotated[int, Field(ge=0)]
    objectives: Annotated[tuple[ObjectiveComparison, ...], Field(min_length=1)]

    __hash__ = CoreValue.cached_hash


resolve_value(EvidenceKey)
resolve_value(RevisionRef)
resolve_value(TaskStage)
resolve_value(TaskObjective)
resolve_value(ObjectiveNoise)
resolve_value(NoiseSample)
resolve_value(Reading)
resolve_value(BaseRef)
resolve_value(Sample)
resolve_value(ObjectiveComparison)
resolve_value(Comparison)
