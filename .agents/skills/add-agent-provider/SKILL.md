---
name: add-agent-provider
description: Add or change support for an agent CLI provider (claude, codex, gemini, opencode), upgrade the agentshim library that drives those CLIs, or bump a provider CLI version pinned in the agent image. Use when editing libs/vs-agent/src/vs_agent/provider_policy.py, provider_profiles.py, spec.py, the vibesys-agent Dev Container Feature, the agentshim pin in pyproject.toml, the --cli-provider flag or [agent].cli_provider, or [agent.quota] fallback providers.
metadata:
  kind: codebase
---

# Add an agent provider

A **provider** is an agent CLI VibeSys can drive: `claude`, `codex`, `gemini`
or `opencode` today. It is not the **agent backend**:
`vs_agent.spec.AgentBackend` (`cli` or `stub`) chooses between driving a
provider CLI and a canned stub runner; providers exist only under `cli`.

VibeSys never talks to a CLI directly. **agentshim**, a separate library
(vic-lsh/agentshim, pinned in `pyproject.toml`), owns provider knowledge: argv,
stream parsing, MCP config formats, schema dialects, state directories, auth
variables, skill directories and resume flags, published per provider as a
`ProviderProfile`. VibeSys owns **driver policy**: which providers it ships,
the CLI versions in its container image, budgets, confinement and quota
handling. See "Where agentshim lives" in
[agent-drivers.md](../../../docs/contributing/agent-drivers.md).

## Builds on

- `software-design`: one source of truth per fact; extend the existing seams
  (`provider_policy` for decisions, `provider_profiles` for facts).
- `testing`: Fakes over mocks; tests use `vs_agent.api.testing`, never a CLI.

## When this applies

- Shipping or dropping a provider; upgrading agentshim (`pyproject.toml`,
  `uv.lock`); bumping a CLI or Node pin for the agent container.
- Changing provider selection: `--cli-provider` (`src/entrypoints/cli/args.py`),
  `[agent].cli_provider`, or `[agent.quota]` (`src/vibesys/config.py:QuotaCfg`).
- Not session, transport or turn-recovery mechanics (agent-drivers.md), nor
  test conventions in general (`testing`, `write-vibesys-tests`).

## Steps

1. **Put CLI behavior in agentshim first.** New argv, parsing, failure
   classification or schema dialects land and release upstream, then the pin
   rises. Try an unreleased commit with the temporary `[tool.uv.sources]`
   override shown in agent-drivers.md and remove it before merging.
   Unenforced: whether a fact is provider knowledge or VibeSys policy needs
   judgment; ask "is this true because of how the CLI behaves?".
2. **Import agentshim only in `libs/vs-agent/`.** Everything else uses
   `vs_agent.api`, tests use `vs_agent.api.testing`. Ruff's banned-api rule
   `TID251` enforces it; the only exemptions (`per-file-ignores` in
   `pyproject.toml`) are `libs/vs-agent/**` and `tests/e2e/**`. Export from
   `vs_agent.api` instead of adding one.
3. **Read provider facts through `vs_agent.provider_profiles.provider_profile`
   at call time.** Host grants, auth, skill and MCP config paths derive from the
   profile (`libs/vs-agent/src/vs_agent/host_resource_declarations.py:declare_provider_state_resources`,
   `libs/vs-agent/src/vs_agent/provider_policy.py:cli_skill_dirs`), so they
   need no per-provider edit. No provider-name literal outside those two
   modules:
   `libs/vs-agent/tests/test_provider_policy.py:test_no_bare_provider_literals_outside_the_allowlist`
   scans `src/` and every `libs/*/src`; do not grow its allowlist.
4. **Ship it in `libs/vs-agent/src/vs_agent/provider_policy.py`:** add it to
   `SHIPPED_PROVIDERS` and pin it in `CLI_VERSIONS` (`DOCKER_PROVIDER_ENV`
   derives). That tuple drives `vs_agent.spec.AgentSpec` (rejects any other
   name at construction), the `--cli-provider` choices, `QuotaCfg` fallback
   validation, and `build_agent_client`'s Docker check.
   `libs/vs-agent/tests/test_provider_policy.py:test_shipped_providers_are_registered_with_agentshim`
   and
   `libs/vs-agent/tests/test_provider_policy.py:test_every_shipped_provider_has_a_container_cli_pin_and_no_other_does`
   hold it.
5. **Install the CLI in the agent image**, the Docker layer built over the
   task image from the `vibesys-agent` Dev Container Feature
   (`libs/vs-agent/src/vs_agent/images/features/src/vibesys-agent/`). It
   installs every CLI with its own `npm` line and ignores agentshim's
   `ProviderProfile.container_install`, whose pins lag. Add a
   `<provider>_version` option (default = the pin) to
   `devcontainer-feature.json`, a `require_option` line and the install to
   `install.sh`, and a `<binary> --version` check to the Feature's `test.sh`.
   `libs/vs-agent/tests/test_agent_feature.py:test_option_defaults_mirror_provider_policy`
   and
   `libs/vs-agent/tests/test_agent_feature.py:test_the_feature_requires_and_smoke_tests_every_shipped_cli`
   tie them to `provider_policy`; only the `devcontainer features test` job in
   `.github/workflows/test.yml` proves the install works.
6. **Upgrade agentshim:** raise the range, relock, read its changelog for
   renamed profile fields and new CLI minimums, and run the vs-agent tests.
   `libs/vs-agent/tests/test_codex_cli_pin.py` fails when agentshim's generated
   Codex app-server protocol needs a newer CLI than `CODEX_DOCKER_CLI_VERSION`.
7. **Bump a CLI pin** in `CLI_VERSIONS` (Codex: `CODEX_DOCKER_CLI_VERSION`) and
   the Feature option default together
   (`libs/vs-agent/tests/test_agent_feature.py:test_option_defaults_mirror_provider_policy`).
8. **Update hand-written provider lists:** the `cli_provider` description in
   `src/vibesys/config.py:AgentCfg`, `agent.toml.example`, chat model
   suggestions in `src/server/chat/options.py`, and agent-drivers.md's tables.
   Unenforced: prose and suggestion data; a check would have to parse English.

## Testing

- Script provider turns with `vs_agent.api.testing.FakeProvider`
  (`libs/vs-agent/src/vs_agent/scripted_provider.py`) through the real
  `AgentClient`; when the subject is a derivation from a `ProviderProfile`, build
  profiles with `vs_agent.api.testing.fake_profiles` so a library release cannot
  break the test (`libs/vs-agent/tests/test_host_resource_declarations.py`).
- These pick up a new provider automatically:
  `tests/vibesys/agents/test_agent_selection.py:test_default_driver_supports_all_agentshim_providers`
  and `tests/vibesys/agents/test_reply_fields_survive_normalization.py`, which
  checks every reply field survives the provider's schema dialect.
- Readiness (missing binary, failed login): `libs/vs-agent/tests/test_readiness.py`.
  Docker auth presence:
  `tests/vibesys/sandbox/test_run_environment.py:test_docker_environment_rejects_a_cli_provider_without_any_auth_source`.
  Quota config: `tests/vibesys/test_quota_config.py`,
  `libs/vs-runtime/tests/test_quota_policy.py`.
- Real CLIs run only in the e2e tier, skipped unless enabled:
  `tests/e2e/test_confined_session_e2e.py` and
  `tests/e2e/test_operator_isolation_e2e.py` (`VIBESYS_E2E_AGENTS=1`; Claude
  and Codex only) and `tests/e2e/test_agent_image_e2e.py`
  (`VIBESYS_E2E_DOCKER=1`; builds the image, runs every CLI). Run them after a
  pin bump or upgrade and paste the output in the PR.

## Known failure modes

- A container CLI older than agentshim's generated protocol cannot decode its
  own server: Codex 0.144.4 broke `thread/start` on stream turns (#1548), found
  only by a live run; agentshim's `container_install` still names 0.144.4.
  Caught by `libs/vs-agent/tests/test_codex_cli_pin.py`.
- An agentshim upgrade changed schema normalization and dropped field
  descriptions from reply schemas (#1253, 0.14.1).
  `tests/vibesys/agents/test_schema_annotations.py` checks the schema a
  provider receives; it covers Claude and Codex only, so extend it.
- Provider literals copied into a launcher, entrypoint or container table
  drifted apart; `provider_policy` ended that
  (`libs/vs-agent/tests/test_provider_policy.py:test_no_bare_provider_literals_outside_the_allowlist`).
- `[agent.quota] fallback_provider` parses, but a run binds every role to one
  provider, so the run host refuses it at startup instead of ignoring it; the
  switch from #1544 serves only hosts whose sessions can change provider
  (`tests/vibesys/run/test_core_host_composition.py:test_a_fallback_a_core_run_cannot_switch_to_fails_at_startup_naming_the_key`).
- Unit tests pass while the real CLI fails: no deterministic test starts a
  CLI. Unenforced: the real tiers need credentials and network, so CI cannot
  run them on every PR.

## Sources of truth

- `libs/vs-agent/src/vs_agent/provider_policy.py`: shipped providers, default,
  container environment, Node and CLI pins.
- `libs/vs-agent/src/vs_agent/provider_profiles.py`: the one seam reading
  agentshim's `ProviderProfile`.
- `pyproject.toml`: the agentshim range and the `TID251` ban and exemptions.
- [agent-drivers.md](../../../docs/contributing/agent-drivers.md) and
  [devcontainer-features.md](../../../docs/contributing/devcontainer-features.md).
