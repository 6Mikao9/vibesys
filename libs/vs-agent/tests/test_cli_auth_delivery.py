"""What a CLI agent's container is given to log in with when the run has no auth policy.

The CLI logs in the way the launching shell configured it. When that is an
environment credential, the container gets that credential (and the endpoint
the shell set) and none of the operator's auth files: not their login, and not
their settings, whose hooks, plugins and helper commands name host paths and
can carry credentials of their own. When the CLI would log in from a file, the
host's auth files are staged as before.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import agentshim
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from vs_agent.api import CredentialKind, cli_auth_delivery
from vs_agent.api.testing import fake_profiles
from vs_sim.api.testing import examples

# A stand-in CLI with the shape every classified provider has: environment
# credentials ahead of a login file in the precedence order, an endpoint
# variable with a companion, and settings files beside the login.
_KEY = "FIXTURE_KEY"
_TOKEN = "FIXTURE_TOKEN"
_SUBSCRIPTION_TOKEN = "FIXTURE_SUBSCRIPTION_TOKEN"
_URL = "FIXTURE_BASE_URL"
_HEADERS = "FIXTURE_HEADERS"
_LOGIN = ".fixture/login.json"
_SETTINGS = (".fixture/settings.json", ".fixture.json")
_ENV_SOURCES = (
    (_TOKEN, CredentialKind.API_KEY),
    (_KEY, CredentialKind.API_KEY),
    (_SUBSCRIPTION_TOKEN, CredentialKind.SUBSCRIPTION),
)
_AUTH_ENV = (*(name for name, _ in _ENV_SOURCES), _URL, _HEADERS)


def _fixture_profile(*, classified: bool) -> agentshim.ProviderProfile:
    return fake_profiles.profile(
        "fixture",
        state_dirs=(".fixture", ".fixture.json"),
        auth_env_vars=_AUTH_ENV,
        auth_files=(_LOGIN, *_SETTINGS),
        credential_files=(_LOGIN,),
        credential_sources=(
            (
                *(fake_profiles.env_credential(name, kind) for name, kind in _ENV_SOURCES),
                fake_profiles.file_credential(_LOGIN, CredentialKind.SUBSCRIPTION),
            )
            if classified
            else ()
        ),
        endpoint=fake_profiles.endpoint(_URL, _HEADERS) if classified else None,
    )


@dataclass(frozen=True)
class _Host:
    env: dict[str, str]
    files: frozenset[str]
    classified: bool


_env_value = st.sampled_from([None, "", "  ", "secret-value"])


@st.composite
def _hosts(draw: st.DrawFn) -> _Host:
    env: dict[str, str] = {}
    for name in (*(name for name, _ in _ENV_SOURCES), _HEADERS):
        value = draw(_env_value)
        if value is not None:
            env[name] = f"{value}-{name}" if value.strip() else value
    url = draw(st.sampled_from([None, "", "https://gateway.invalid/v1", "not-a-url"]))
    if url is not None:
        env[_URL] = url
    files = draw(st.frozensets(st.sampled_from((_LOGIN, *_SETTINGS))))
    return _Host(env, files, classified=draw(st.booleans()))


def _set(env: dict[str, str], name: str) -> bool:
    return bool(env.get(name, "").strip())


def _expected_env_credential(host: _Host) -> str | None:
    """The environment credential the CLI logs in with on *host*, or ``None`` for files."""
    if not host.classified:
        return None
    endpoint = host.env.get(_URL, "").strip()
    if endpoint:
        parts = urlsplit(endpoint)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            return None
        # A custom endpoint takes only API keys.
        allowed = {CredentialKind.API_KEY}
    else:
        allowed = set(CredentialKind)
    for name, kind in _ENV_SOURCES:
        if kind in allowed and _set(host.env, name):
            return name
    return None


def _plant(home: Path, files: frozenset[str]) -> None:
    for leaf in files:
        (home / leaf).parent.mkdir(parents=True, exist_ok=True)
        (home / leaf).write_text("{}")


@settings(max_examples=examples(pr=60, nightly=600), deadline=None)
@given(host=_hosts())
def test_an_env_credential_the_cli_would_use_is_delivered_alone(
    tmp_path_factory: pytest.TempPathFactory, host: _Host
) -> None:
    home = tmp_path_factory.mktemp("home")
    _plant(home, host.files)
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setenv("HOME", str(home))
        for name in _AUTH_ENV:
            monkeypatch.delenv(name, raising=False)
        for name, value in host.env.items():
            monkeypatch.setenv(name, value)
        fake_profiles.install(
            monkeypatch, {"fixture": _fixture_profile(classified=host.classified)}
        )
        chosen = _expected_env_credential(host)
        forwarded = {name: value for name, value in host.env.items() if value.strip()}
        if chosen is None and not host.files and not forwarded:
            with pytest.raises(ValueError, match="no 'fixture' CLI authentication"):
                cli_auth_delivery("fixture", None)
            return
        delivery = cli_auth_delivery("fixture", None)

    staged = {Path(host_path) for host_path, _, _ in delivery.bind_mounts}
    if chosen is not None:
        expected_env = {chosen: host.env[chosen]}
        if _set(host.env, _URL):
            expected_env[_URL] = host.env[_URL].strip()
            if _set(host.env, _HEADERS):
                expected_env[_HEADERS] = host.env[_HEADERS]
        assert dict(delivery.env) == expected_env
        assert staged == set()
        assert delivery.copy_paths == ()
        assert chosen in delivery.describe()
    else:
        assert dict(delivery.env) == forwarded
        assert staged == {home / leaf for leaf in host.files}
    description = delivery.describe()
    for value in host.env.values():
        if value.strip():
            assert value not in description


_SHIPPED = ("claude", "codex", "gemini", "opencode")


def _env_sources(provider: str) -> list[tuple[str, str]]:
    profile = agentshim.get_provider(provider).profile
    return [
        (provider, source.name)
        for source in profile.credential_sources
        if isinstance(source, agentshim.EnvCredential)
    ]


@pytest.mark.parametrize(
    ("provider", "variable"),
    [pair for provider in _SHIPPED for pair in _env_sources(provider)],
)
def test_no_shipped_provider_stages_host_files_beside_an_env_credential(
    provider: str, variable: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every shipped CLI whose env credential would win gets no host auth file at all."""
    profile = agentshim.get_provider(provider).profile
    _plant(tmp_path, frozenset(profile.auth_files))
    monkeypatch.setenv("HOME", str(tmp_path))
    for name in profile.auth_env_vars:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(variable, "synthetic-credential")

    delivery = cli_auth_delivery(provider, None)

    assert delivery.bind_mounts == ()
    assert delivery.copy_paths == ()
    assert dict(delivery.env) == {variable: "synthetic-credential"}


def test_a_claude_gateway_token_gets_no_host_login_or_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reported case: a gateway token plus base URL on a host also logged in by OAuth."""
    profile = agentshim.get_provider("claude").profile
    _plant(tmp_path, frozenset(profile.auth_files))
    monkeypatch.setenv("HOME", str(tmp_path))
    for name in profile.auth_env_vars:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "synthetic-gateway-token")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://gateway.invalid/anthropic")

    delivery = cli_auth_delivery("claude", None)

    assert delivery.bind_mounts == ()
    assert delivery.copy_paths == ()
    assert dict(delivery.env) == {
        "ANTHROPIC_AUTH_TOKEN": "synthetic-gateway-token",
        "ANTHROPIC_BASE_URL": "https://gateway.invalid/anthropic",
    }
    assert "ANTHROPIC_AUTH_TOKEN" in delivery.describe()
    assert ".claude/.credentials.json" in delivery.describe()
    assert "synthetic-gateway-token" not in delivery.describe()


def test_a_claude_file_login_stages_the_host_files_as_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no env credential, the login is shared writable and settings are copied."""
    profile = agentshim.get_provider("claude").profile
    _plant(tmp_path, frozenset(profile.auth_files))
    monkeypatch.setenv("HOME", str(tmp_path))
    for name in profile.auth_env_vars:
        monkeypatch.delenv(name, raising=False)

    delivery = cli_auth_delivery("claude", None)

    assert {Path(host) for host, _, _ in delivery.bind_mounts} == {
        tmp_path / leaf for leaf in profile.auth_files
    }
    assert delivery.plan is None
    assert "host's auth files" in delivery.describe()
