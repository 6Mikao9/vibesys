"""A run's ``[agent.auth]`` policy is met before the first turn, or the run stops.

The run environment delivers only the planned credential to the agent's
container (tested with the run environments); here the readiness probe checks
the kind the CLI reports against the policy, and a host launcher refuses a
policy it cannot enforce. The CLI is scripted with ``agentshim.testing``.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING, cast

import agentshim
import pytest
from agentshim.testing import FakeClock, FakeExecutor, FakeRun, probe_run, scripted_turn
from hypothesis import given
from hypothesis import strategies as st

from vs_agent.api import (
    AgentAuthPolicy,
    AgentClient,
    AuthStatus,
    CredentialKind,
    ProviderNotReadyError,
    ProviderReadiness,
    ReadinessProblem,
)

# test-isolation: these tests exercise the launcher's own internals, which the facade deliberately hides
from vs_agent.client import AgentDiagnosticLog

# test-isolation: these tests exercise the launcher's own internals, which the facade deliberately hides
from vs_agent.session_launch import ConfinedSessionLauncher
from vs_sandbox.api.testing import FakeDockerConfinement
from vs_sim.api.testing import ManualClock

if TYPE_CHECKING:
    from pathlib import Path

    from vs_sandbox.api import DockerSandbox

_KINDS = st.sampled_from(CredentialKind)
#: What the scripted status command of a logged-in CLI reports (a Claude.ai or ChatGPT login).
_SCRIPTED_KIND = CredentialKind.SUBSCRIPTION


@given(
    credential=st.none() | _KINDS,
    allowed=st.none() | st.frozensets(_KINDS, min_size=1),
    auth=st.sampled_from(AuthStatus),
    binary_found=st.booleans(),
)
def test_a_reported_credential_outside_the_policy_is_a_problem(
    credential: CredentialKind | None,
    allowed: frozenset[CredentialKind] | None,
    auth: AuthStatus,
    *,
    binary_found: bool,
) -> None:
    readiness = ProviderReadiness(
        provider="claude",
        binary_found=binary_found,
        path=None,
        version=None,
        auth=auth,
        detail="",
        credential=credential,
        allowed_credentials=allowed,
    )

    disallowed = allowed is not None and credential is not None and credential not in allowed
    if not binary_found:
        assert readiness.problem is ReadinessProblem.BINARY_MISSING
    elif auth is AuthStatus.FAILED:
        assert readiness.problem is ReadinessProblem.AUTH_FAILED
    else:
        assert (readiness.problem is ReadinessProblem.CREDENTIAL_NOT_ALLOWED) == disallowed
        assert (readiness.problem is None) == (not disallowed)


def _logged_in_cli(provider: str) -> FakeExecutor:
    binary = agentshim.get_provider(provider).profile.binary
    turn = scripted_turn(provider, text="done")

    def pick(request: agentshim.CommandRequest) -> FakeRun:
        # The container route prefixes ``docker exec ... <container>``; the CLI's
        # own arguments follow its binary.
        argv = list(request.argv)
        args = argv[argv.index(binary) + 1 :]
        probed = probe_run(provider, args, version="9.9.9", auth=agentshim.AuthState.KNOWN_OK)
        if probed.returncode != 2:
            return FakeRun(stdout=[probed.stdout], stderr=[probed.stderr])
        return turn

    return FakeExecutor(pick, binaries={binary: f"/usr/bin/{binary}"})


def _container_client(
    tmp_path: Path, provider: str, policy: AgentAuthPolicy
) -> tuple[AgentClient, io.StringIO]:
    fake = _logged_in_cli(provider)
    sandbox = cast("DockerSandbox", FakeDockerConfinement(workspace=tmp_path))
    launcher = ConfinedSessionLauncher(
        provider=provider,
        executor_factory=lambda: fake,
        docker_sandboxes={"implementer": sandbox},
        auth_policy=policy,
        transport=agentshim.TransportKind.ONE_SHOT,
        clock=FakeClock(),
    )
    log = io.StringIO()
    client = AgentClient(
        launcher,
        provider=provider,
        diagnostic_log=AgentDiagnosticLog(log),
        check_readiness=True,
        containerized=True,
        clock=ManualClock(),
    )
    return client, log


def _invoke(client: AgentClient, root: Path) -> str:
    return client.invoke_text(
        kind="implementer", workspace=root, system_prompt="sys", user_prompt="go", round_label="r1"
    )


@pytest.mark.parametrize("provider", ["claude", "codex"])
@pytest.mark.parametrize("allowed", [frozenset({kind}) for kind in CredentialKind])
def test_the_run_stops_before_a_turn_when_the_cli_uses_a_kind_the_policy_forbids(
    tmp_path: Path, provider: str, allowed: frozenset[CredentialKind]
) -> None:
    client, log = _container_client(tmp_path, provider, AgentAuthPolicy(allowed))

    if _SCRIPTED_KIND in allowed:
        assert _invoke(client, tmp_path) == "done"
        assert f"confirmed logged in with {_SCRIPTED_KIND.value}" in log.getvalue()
        return
    with pytest.raises(ProviderNotReadyError) as raised:
        _invoke(client, tmp_path)
    assert raised.value.problem is ReadinessProblem.CREDENTIAL_NOT_ALLOWED
    assert f"reports {_SCRIPTED_KIND.value}" in str(raised.value)


def test_a_host_launcher_refuses_an_auth_policy() -> None:
    """A host session sees the operator's own CLI logins, so a policy cannot be enforced there."""
    with pytest.raises(ValueError, match="container"):
        ConfinedSessionLauncher(
            provider="claude",
            auth_policy=AgentAuthPolicy(frozenset({CredentialKind.API_KEY})),
            clock=FakeClock(),
        )


def test_a_policy_must_allow_a_credential() -> None:
    with pytest.raises(ValueError, match="at least one"):
        AgentAuthPolicy(frozenset())
