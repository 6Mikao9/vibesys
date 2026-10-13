"""The knobs candidate policy reads, which a strategy's own config extends."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from vibesys.metrics import MetricSpace

type Positive = Annotated[int, Field(gt=0)]


class CandidateConfig(BaseModel):
    """Which gates a run measures and how candidates rank."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    metric_space: MetricSpace = Field(default_factory=MetricSpace)
    benchmark_configured: bool = True
    accuracy_configured: bool = True
