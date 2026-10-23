"""The shell's executor roles and core's executor lifetimes describe the same two worlds.

Core declares which requests have the host instance as their executor (`executor_lifetime`);
the shell routes each request kind to one executor role. The agent session role is the one whose
provider processes, open turns and session handles die with the host, so it must be exactly the
set core treats as instance-bound. A kind moved between roles, or classified on one side only,
fails here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from vs_core.api import ExecuteRegisteredOperation, ExecutorLifetime, executor_lifetime
from vs_runtime.api.core import REQUEST_DISPATCH, ExecutorRole

if TYPE_CHECKING:
    from vs_core.api import Request


def test_the_session_role_is_exactly_the_requests_whose_executor_is_the_host_instance() -> None:
    kinds = [kind for kind in REQUEST_DISPATCH if kind is not ExecuteRegisteredOperation]
    session_role = {kind for kind in kinds if REQUEST_DISPATCH[kind] is ExecutorRole.SESSIONS}
    instance_bound = {
        kind
        for kind in kinds
        if executor_lifetime(cast("Request", kind.model_construct())) is ExecutorLifetime.INSTANCE
    }
    assert session_role == instance_bound
