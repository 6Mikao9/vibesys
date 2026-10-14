"""Container environment and staged auth for a CLI agent provider."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from vs_agent.api import DOCKER_PROVIDER_ENV, AgentBackend, cli_auth_delivery

if TYPE_CHECKING:
    from vs_agent.api import AgentAuthPolicy, CliAuthDelivery


@dataclass(frozen=True)
class CliContainerAuth:
    """What a container needs so its CLI agent runs logged in.

    ``env`` is the provider's container environment with the delivered auth
    variables over it; ``delivery`` carries the mounts and start-time copies.
    Every containerized environment (Docker, Slurm's Docker editor, Modal,
    SkyPilot) builds its auth from one of these, so they cannot disagree on
    what reaches the agent.
    """

    provider: str
    env: dict[str, str]
    delivery: CliAuthDelivery


def cli_container_auth(
    agent_backend: str | None, cli_provider: str | None, auth_policy: AgentAuthPolicy | None
) -> CliContainerAuth | None:
    """Return the container auth for a CLI agent, or ``None`` when the run has none.

    ``None`` when the run is not a containerized CLI agent (a different agent
    backend, or no CLI provider selected). *auth_policy* is the run's
    ``[agent.auth]``; without one every credential on the host is forwarded.

    Raises:
        ValueError: without a policy, when the host has no credential for the
            provider; with one (``AgentAuthError``), when it cannot be met.
    """
    effective_agent = agent_backend or AgentBackend.CLI
    if effective_agent != "cli" or not cli_provider:
        return None
    delivery = cli_auth_delivery(cli_provider, auth_policy)
    env = dict(DOCKER_PROVIDER_ENV.get(cli_provider, {}))
    # Container processes inherit only what ``docker run -e`` sets; the editor
    # container has no other view of the host environment.
    env.update(delivery.env)
    return CliContainerAuth(cli_provider, env, delivery)
