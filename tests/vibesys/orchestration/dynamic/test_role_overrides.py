"""`[agent.roles.<id>]` model overrides address a core strategy's roles by its declared role ids.

Design question Q6 of `docs/design/strategies-on-core.md`. A role override in `agent.toml` is
keyed by a role id the selected plugin declares, and any other key is refused before the run
opens. `dynamic` declares `dynamic-orchestrator`, `dynamic-implementer`, `dynamic-judge` and
`dynamic-profiler`, so those are the keys; the legacy ids of the loops it replaces
(`orchestrator`, `implementer`, `judge`, `profiler`) are not declared and are refused naming the
key. The ported strategies follow the same rule with their own prefix.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from vibesys.composition import resolve_agent_specs
from vibesys.config import Config
from vibesys.errors import ConfigurationError
from vibesys.orchestration.dynamic.plugin import PLUGIN

DYNAMIC_ROLES = ("dynamic-orchestrator", "dynamic-implementer", "dynamic-judge", "dynamic-profiler")
LEGACY_ROLES = ("orchestrator", "implementer", "judge", "profiler")


def _config(roles: dict[str, dict[str, str]]) -> Config:
    return Config.model_validate({"model": {"name": "base-model"}, "agent": {"roles": roles}})


def test_the_plugin_declares_the_prefixed_role_ids() -> None:
    assert tuple(role.id for role in PLUGIN.agents) == DYNAMIC_ROLES


@given(
    overrides=st.dictionaries(
        st.sampled_from(DYNAMIC_ROLES),
        st.text(alphabet="abcdefghij-", min_size=1, max_size=12),
    )
)
def test_an_override_changes_only_the_role_it_names(overrides: dict[str, str]) -> None:
    config = _config({role: {"model": model} for role, model in overrides.items()})
    specs = resolve_agent_specs(config, PLUGIN.agents)
    assert {role: spec.model for role, spec in specs.items()} == {
        role: overrides.get(role, "base-model") for role in DYNAMIC_ROLES
    }


@pytest.mark.parametrize("legacy", LEGACY_ROLES)
def test_a_legacy_role_id_is_refused_naming_the_key(legacy: str) -> None:
    config = _config({legacy: {"model": "other"}})
    with pytest.raises(ConfigurationError, match=f"agent.roles.{legacy}"):
        resolve_agent_specs(config, PLUGIN.agents)
