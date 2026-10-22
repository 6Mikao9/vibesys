"""The evaluation host is shared unless an environment guarantees the node alone."""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from vs_runtime.api.infrastructure import (
    AgentPaths,
    EvalHostTenancy,
    RunEnvironmentView,
    declared_eval_host_tenancy,
    resolve_eval_host_tenancy,
)
from vs_sim.api.testing import examples


@settings(max_examples=examples(pr=8, nightly=100))
@given(local_host=st.booleans(), exclusive_node=st.booleans())
def test_a_local_host_is_shared_and_only_an_exclusive_node_is_exclusive(
    *, local_host: bool, exclusive_node: bool
) -> None:
    tenancy = declared_eval_host_tenancy(local_host=local_host, exclusive_node=exclusive_node)
    if local_host or not exclusive_node:
        assert tenancy is EvalHostTenancy.SHARED
    else:
        assert tenancy is EvalHostTenancy.EXCLUSIVE


@settings(max_examples=examples(pr=8, nightly=100))
@given(
    default=st.sampled_from(EvalHostTenancy),
    override=st.none() | st.sampled_from(EvalHostTenancy),
)
def test_a_task_override_replaces_the_environment_default(
    default: EvalHostTenancy, override: EvalHostTenancy | None
) -> None:
    assert resolve_eval_host_tenancy(default, override) is (
        default if override is None else override
    )


def test_a_view_declares_a_shared_host_until_an_environment_says_otherwise() -> None:
    view = RunEnvironmentView(paths=AgentPaths(), workspace_git_writable=True)
    assert view.evaluation_host_tenancy is EvalHostTenancy.SHARED
