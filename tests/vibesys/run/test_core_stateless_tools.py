"""Assumption A3 of the strategy port design: a core session can be offered a stateless tool.

`multi`, `evolve` and `issue_queue` give a role the profiler diagnostics server, an MCP server
that analyses captured profiles and holds no run state. Composition refuses every tool but the
bridged `evaluation` tool today (`_refuse_unbridged_tools`), because a tool that records into
legacy state would be a second writer of run state beside core. The refusal is right for the
issue board and for any tool that writes run state; it is too wide for a stateless tool.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import pytest
from tests.support.docker_environment import host_container_backend
from tests.vibesys.orchestration.plugin import EmptyOptions
from tests.vibesys.run.test_core_host_composition import (
    _policy,
    _request,
    _resumable_client,
    _write_project,
)

from vibesys.run.core_services import CoreCompositionError
from vibesys.run.host import open_product_core_host
from vibesys.run.integration import LocalRunIntegration
from vs_agent.api.testing import FakeAgentInvocationStore
from vs_runtime.api import AgentRole, AgentTool, OrchestrationPlugin
from vs_runtime.api.core import RunTiming
from vs_sim.api.testing import VirtualClock, run_virtual

if TYPE_CHECKING:
    from pathlib import Path


def _compose(tmp_path: Path, tool: str) -> None:
    project_root = tmp_path / "project"
    templates = tmp_path / "templates"
    templates.mkdir()
    _write_project(project_root)
    plugin = OrchestrationPlugin(
        id="core-test",
        agents=(
            AgentRole(
                id="implementer", system_prompt="Implement.", extra_tools=(AgentTool(id=tool),)
            ),
        ),
        options=EmptyOptions,
        core=_policy(templates),
    )
    integration = LocalRunIntegration()
    clock = VirtualClock(at=0.0)

    async def exercise() -> None:
        try:
            async with open_product_core_host(
                _request(project_root),
                integration,
                plugin=replace(plugin),
                options=EmptyOptions(),
                timing=RunTiming(clock, 60.0),
                agent_client_factory=_resumable_client,
                backend_factory=host_container_backend,
                invocation_store_factory=lambda _state, _key: FakeAgentInvocationStore(),
            ):
                return
        finally:
            integration.close()

    run_virtual(clock, exercise())


@pytest.mark.xfail(
    strict=True,
    raises=CoreCompositionError,
    reason="A3 gap: composition refuses every tool but `evaluation`, including the stateless profiler",
)
def test_a_role_with_the_stateless_profiler_tool_composes(tmp_path: Path) -> None:
    _compose(tmp_path, "profiler")


def test_a_role_with_a_tool_that_writes_run_state_is_refused_naming_it(tmp_path: Path) -> None:
    """The issue board records into legacy state, so core must keep refusing it."""
    with pytest.raises(CoreCompositionError, match="issue-board") as raised:
        _compose(tmp_path, "issue-board")
    assert raised.value.resource == "agent role 'implementer' tool 'issue-board'"
