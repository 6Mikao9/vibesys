"""The kernel name of a pipeline step tells every ask, step and attempt apart."""

from hypothesis import given
from hypothesis import strategies as st

from vs_core.api import DecisionId, PipeStep, PipeStepKind

_asks = st.builds(DecisionId, root=st.text(alphabet="ab~r1t2", min_size=1, max_size=6))
_steps = st.builds(
    PipeStep,
    ask=_asks,
    kind=st.sampled_from(PipeStepKind),
    attempt=st.integers(min_value=0, max_value=12),
)


@given(first=_steps, second=_steps)
def test_two_steps_share_a_decision_id_only_when_they_are_the_same_step(
    first: PipeStep, second: PipeStep
) -> None:
    """Asks that contain the separator, or end like a step, never collide with another step."""
    assert (first.decision_id() == second.decision_id()) == (first == second)


@given(step=_steps)
def test_a_step_names_the_same_decision_every_time(step: PipeStep) -> None:
    assert step.decision_id() == step.model_copy().decision_id()
