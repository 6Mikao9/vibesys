"""A real agent container holds exactly the credential ``[agent.auth]`` planned.

Skipped unless ``VIBESYS_E2E_DOCKER=1`` and ``docker`` is on PATH:

```bash
VIBESYS_E2E_DOCKER=1 uv run pytest tests/e2e/test_agent_auth_policy_e2e.py -q
```

The host home holds a planted Claude subscription login and the host
environment a planted API key, OAuth token and stale base URL, so a container
that saw anything besides the plan would show it. No real credential is read
and no model is called: the container is inspected and ``claude auth status``
reports which login the CLI found.
"""

from __future__ import annotations

import json
import os
import shutil
from contextlib import contextmanager
from typing import TYPE_CHECKING

import pytest
from agentshim.testing import FakeClock

from vs_agent.api import (
    AgentAuthPolicy,
    AgentExecutionPolicy,
    AgentSessionSpec,
    CredentialKind,
    cli_auth_delivery,
)
from vs_agent.api.images import agent_image

# test-isolation: the launcher's readiness probe is what a run calls before its first turn
from vs_agent.session_launch import ConfinedSessionLauncher
from vs_sandbox.docker_sandbox import DockerSandbox
from vs_sim.api import MonotonicClock

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

pytestmark = pytest.mark.e2e

_ENABLE_ENV = "VIBESYS_E2E_DOCKER"
_BASE_IMAGE = os.environ.get("VIBESYS_E2E_DOCKER_BASE_IMAGE", "python:3.12-bookworm")
_BUILD_TIMEOUT_S = 900.0
_ENDPOINT = "https://gateway.invalid/claude"
_HOST_ENV = {
    "ANTHROPIC_API_KEY": "sk-ant-api-planted",
    "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-planted",
    "ANTHROPIC_BASE_URL": "https://stale.invalid",
}
_AUTH_ENV = ("ANTHROPIC_AUTH_TOKEN", *_HOST_ENV)


@pytest.fixture
def host_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    if os.environ.get(_ENABLE_ENV) != "1" or shutil.which("docker") is None:
        pytest.skip(f"set {_ENABLE_ENV}=1 with docker on PATH to run against a real daemon")
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    login = {"claudeAiOauth": {"accessToken": "planted", "expiresAt": 9_999_999_999_999}}
    (home / ".claude" / ".credentials.json").write_text(json.dumps(login))
    (home / ".claude.json").write_text("{}")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    for name, value in _HOST_ENV.items():
        monkeypatch.setenv(name, value)
    return home


@contextmanager
def _container(tmp_path: Path, policy: AgentAuthPolicy) -> Iterator[DockerSandbox]:
    delivery = cli_auth_delivery("claude", policy)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sandbox = DockerSandbox(
        host_workspace=str(workspace),
        image=agent_image(_BASE_IMAGE, timeout=_BUILD_TIMEOUT_S, clock=MonotonicClock()),
        bind_mounts=list(delivery.bind_mounts),
        auth_files=list(delivery.copy_paths),
        env=dict(delivery.env),
    )
    sandbox.start()
    try:
        yield sandbox
    finally:
        sandbox.stop()


def _auth_env_in(sandbox: DockerSandbox) -> dict[str, str]:
    result = sandbox.execute("env -0")
    assert result.exit_code == 0, result.output
    entries = (entry.split("=", 1) for entry in result.output.split("\0") if "=" in entry)
    return {name: value for name, value in entries if name in _AUTH_ENV}


def _auth_method(sandbox: DockerSandbox) -> str:
    result = sandbox.execute("claude auth status")
    return json.loads(result.output[result.output.index("{") :])["authMethod"]


def _probed_credential(
    sandbox: DockerSandbox, policy: AgentAuthPolicy, tmp_path: Path
) -> CredentialKind | None:
    """What the run's readiness probe reports; it fails the run on a disallowed kind."""
    launcher = ConfinedSessionLauncher(
        provider="claude",
        docker_sandboxes={"implementer": sandbox},
        auth_policy=policy,
        clock=FakeClock(),
    )
    spec = AgentSessionSpec(
        role="implementer",
        provider="claude",
        workspace=tmp_path / "workspace",
        policy=AgentExecutionPolicy(containerized=True),
    )
    readiness = launcher.probe_readiness(spec)
    assert readiness.problem is None, readiness.detail
    return readiness.credential


def test_an_api_key_policy_hands_over_only_the_key_and_the_endpoint(
    tmp_path: Path, host_home: Path
) -> None:
    del host_home
    policy = AgentAuthPolicy(frozenset({CredentialKind.API_KEY}), _ENDPOINT)
    with _container(tmp_path, policy) as sandbox:
        assert _auth_env_in(sandbox) == {
            "ANTHROPIC_API_KEY": _HOST_ENV["ANTHROPIC_API_KEY"],
            "ANTHROPIC_BASE_URL": _ENDPOINT,
        }
        assert sandbox.execute("test -e $HOME/.claude/.credentials.json").exit_code != 0
        assert _auth_method(sandbox) == "api_key"
        assert _probed_credential(sandbox, policy, tmp_path) is CredentialKind.API_KEY


def test_a_subscription_policy_hands_over_only_the_login(tmp_path: Path, host_home: Path) -> None:
    del host_home
    policy = AgentAuthPolicy(frozenset({CredentialKind.SUBSCRIPTION}))
    with _container(tmp_path, policy) as sandbox:
        assert _auth_env_in(sandbox) == {
            "CLAUDE_CODE_OAUTH_TOKEN": _HOST_ENV["CLAUDE_CODE_OAUTH_TOKEN"],
        }
        assert sandbox.execute("test -e $HOME/.claude/.credentials.json").exit_code != 0
        assert _auth_method(sandbox) == "oauth_token"
        assert _probed_credential(sandbox, policy, tmp_path) is CredentialKind.SUBSCRIPTION
