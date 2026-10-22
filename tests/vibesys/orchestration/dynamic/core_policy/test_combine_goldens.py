"""Pin the rendered planner fact list and the speculative combine implementer's instructions."""

from pathlib import Path

import pytest
from tests.vibesys.golden.helpers import assert_exact_text
from tests.vibesys.orchestration.dynamic.core_policy._contexts import render, revision

from vibesys.orchestration.dynamic.search.api import (
    CombineSource,
    FactCoverage,
    ImplementedRevision,
    ImplementPrompt,
    PlannerPrompt,
    PromptTemplate,
)

_FIXTURES = Path(__file__).with_name("combine_goldens")
_CANDIDATES = tuple(
    ImplementedRevision(hypothesis_id=f"h{i}", revision=revision(f"c{i}"), facts=facts)
    for i, facts in enumerate(FactCoverage)
)


@pytest.mark.parametrize("role", ["planner", "implementer"])
def test_speculative_combine_prompt_matches_its_golden(role: str) -> None:
    if role == "planner":
        context = PlannerPrompt(
            capacity=1,
            in_flight=2,
            remaining=4,
            base_revision=revision("input"),
            base_accuracy_passed=True,
            main_revision=revision("input"),
            offer_snapshot="offer",
            combinable=_CANDIDATES,
        )
        text = render(PromptTemplate.PORTFOLIO, context)
    else:
        criteria = "Preserve accuracy and improve throughput."
        context = ImplementPrompt(
            hypothesis_id="combined",
            hypothesis="The parts help together.",
            task="Apply the two changes.",
            pass_criteria=criteria,
            parent_revision=revision("input"),
            combine=tuple(
                CombineSource(
                    hypothesis_id=row.hypothesis_id,
                    revision=row.revision,
                    built_on=revision("input"),
                )
                for row in _CANDIDATES[:2]
            ),
        )
        text = render(PromptTemplate.IMPLEMENT, context)
    assert_exact_text(_FIXTURES / f"{role}.txt", text)
