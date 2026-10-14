---
name: write-search-flow
description: Add or change a search, the outer loop the operator picks with --outer-loop (agent/single, multi, profile-guided, dynamic, evolve, plain/issue_queue), written as a vs-core flow; covers the @search root flow, roles and corrections, operations, prompts, workspace settlement and admission, the winner, the core_policy projection, launch options, wiring, tach edges, and the search's stories, fault, role-override and golden tests. Use when editing src/vibesys/orchestration/<x>/search/ or core_policy/, src/vibesys/<x>_core.py or <x>_roles.py, a search's Role, or tests under tests/vibesys/orchestration/.
metadata:
  kind: codebase
---

# Write a search flow

A **search** is a run's outer optimization loop: it picks ideas, has agents
implement them in candidate workspaces, has candidates measured, and names a
winner. Each built-in search is a **flow**: an `async def` that takes
`ctx: Flow` first and touches the world only through `ctx`; `vs-core` journals
what it observes and replays it after a crash, so it must be deterministic.
[flows.md](../../../docs/contributing/flows.md) is the vocabulary. Open
[references/search-anatomy.md](references/search-anatomy.md) for the file map,
the command-line-to-plugin names, and worked examples.

## Builds on

- `software-design`: pure core, thin shell. Decisions live in pure helpers
  (`SingleAttempt`, evolve's `Round`); the flow only sequences awaits.
- `testing`. The shared test hosts are the `write-vibesys-tests` skill's.

## When this applies

- Adding a built-in search or a preset (a plugin id that reuses a search with
  other options, as `profile-guided-single-agent` reuses `single`).
- Changing a search's flows, config, roles, operations, prompts, budget,
  settlement, admission or published record:
  `src/vibesys/orchestration/<x>/search/` and `core_policy/`.
- Changing its wiring: `src/vibesys/<x>_core.py`, `src/vibesys/<x>_roles.py`,
  `orchestration/<x>/launch.py`, `src/vibesys/api/catalog.py`,
  `src/entrypoints/cli/loops.py`, or its `tach.toml` modules.

## Steps

1. **Keep the package shape**: private `_*.py` modules, an `api.py` with
   `__all__`, and `vs_core` imported only through `vs_core.api.search` (only
   `core_policy/` may read `vs_core.api`). Enforced by
   `tests/architecture/test_search_packages.py`.
2. **Write flow code only against `ctx`**: no clock, randomness, threads, files,
   sockets, `asyncio` or blind `except`; a missing capability goes into
   `libs/vs-core/src/vs_core/flows/`, reviewed. Enforced by
   `tests/architecture/test_flow_code_is_restricted.py` and `BLE001`.
3. **Register the root** with `@search(name, config=, operations=, roles=)`
   (plus `owned=` for operations a library owns) and call
   `ctx.checkpoint(config)` first. The config is a `Value` with only the
   search's own knobs. Name the winner as you go with `ctx.choose`; return
   `None` or `NoWinner(reason)` and never finish the run. Enforced by
   `libs/vs-core/tests/test_search_ending.py`.
4. **Make every child flow restartable**: `@flow(snapshot=S)` calling
   `ctx.checkpoint(...)` first, or a resume on changed code raises
   `FlowCodeChanged`. Unenforced: no search test resumes on changed code.
5. **Declare each agent question once as a `Role`** in `_roles.py`
   (`libs/vs-core/src/vs_core/flows/_roles.py:Role`): template, reply model,
   workspace, access, turn time, `corrections` (fixes of an invalid reply),
   `lost_turns`, charge, and whether it may wait for evaluations. The reply is
   the role's own model; core wraps it for a waiting role (#2169). `agent=`
   puts a second question to another role's agent
   (`src/vibesys/orchestration/multi/search/_roles.py:ROUTER`). Only `model`
   changes per run. Enforced by `libs/vs-core/tests/test_roles.py`.
6. **Give every agent its host role** in `src/vibesys/<x>_roles.py`: system
   prompt `<role>_system.j2`, tools, and the evaluation tool for a role that
   waits. Enforced by `tests/vibesys/orchestration/test_search_roles.py`.
7. **Name operations in the search's namespace**: one
   `StrategyOperations(namespace=<search name>)`, kinds `<namespace>.<op>`.
   Kinds are persisted; never rename one. Enforced by
   `libs/vs-core/tests/test_strategy_operations.py`.
8. **Send typed prompt contexts, never prompt text**: a `RolePromptContext`
   with a `template` literal in `_prompts.py`, the template in
   `core_policy/prompts/`, run-level variables from the core policy's
   `run_prompt_variables` (or `<x>_core.py`). Render every template over
   its contexts as `tests/vibesys/orchestration/single/test_prompts.py` does;
   `tests/architecture/test_prompt_reply_fields.py` bans typed reply fields.
9. **Open workspaces only as resources and settle through shared policy**:
   `async with ctx.workspaces.open(...)`; handle `NotOpened`, a `Declined`
   checkpoint and `Stopped`; `keep` an `Assessment` from
   `vibesys.candidates.api` or `vibesys.serial_search.api`. Enforced by
   `tests/vibesys/orchestration/test_search_faults.py` (every attempt settled,
   one `Finish`, under any fault or stop).
10. **Admit every finished task in an order independent of finish order**:
    fold each value a group yields, `TaskStopped` included, and admit
    concurrent results in slot order as evolve's `Round.finish` does. Enforced
    by `tests/vibesys/orchestration/evolve/test_round.py`.
11. **Derive the budget** (`budget` in `_budget.py`) from the config and each
    role's `turn_chain(corrections=, lost_turns=)`. Unenforced: only evolve's
    budget has a test (`tests/vibesys/orchestration/evolve/test_config.py`).
12. **Publish and project**: `ctx.publish(snapshot)` after each fold;
    `project_run_snapshot` in `core_policy/` maps it to the
    `IterationTree` clients navigate; declare `IterationSupport.PROJECTED`.
    Enforced by `tests/vibesys/api/test_iteration_contract.py` and the fault
    test, which projects after every step.
13. **Keep `launch.py` options and reply models strict**: an undeclared key is
    refused (#2399). Enforced by `tests/quality/test_models_reject_unknown_keys.py`.
14. **Wire and register**: `<x>_core.py` builds the plan (with
    `search.with_models(...)` for `agent.toml` overrides) and the projector;
    `plugin.py` exports `REGISTRATION`; add it to `BUILT_INS` in
    `src/vibesys/api/catalog.py`; map the `--outer-loop` choice in
    `src/entrypoints/cli/loops.py`. Enforced by
    `tests/architecture/test_orchestration_layout.py`.
15. **Add `tach.toml` modules and edges** for `search`, `core_policy`, `launch`,
    `<x>_roles` and `<x>_core`, including
    `vibesys.<x>_core -> vibesys.orchestration.<x>.search`, each search exposing
    only `api`. Enforced by `scripts/check_tach.py`.

## Testing

- Tell each decision as a story (`tests/vibesys/orchestration/single/test_stories.py`):
  a `tests/vibesys/orchestration/<x>/_host.py` drives `vs_core.api.testing.FlowRun`;
  `tests/vibesys/vibesys_flows.py:across_restarts` reruns it with restarts.
- List a new plugin in `SEARCHES` of
  `tests/vibesys/orchestration/test_search_faults.py` and
  `tests/vibesys/orchestration/test_search_roles.py`; both fail until it is.
- Property-test pure helpers: `tests/vibesys/orchestration/evolve/test_settlement.py`.
- Add a role-override test like
  `tests/vibesys/orchestration/single/test_role_overrides.py`: `agent.toml`
  keys address the plugin's role ids (`single-implementer`), and any other key,
  legacy ids included, is refused naming it. Unenforced: nothing requires one.
- Pin agent-facing text: `tests/vibesys/orchestration/single/test_system_prompts.py`
  (system prompts), `tests/vibesys/golden/helpers.py:assert_exact_text` goldens
  (`tests/vibesys/orchestration/issue_queue/test_prompt_goldens.py`).
- Add the search's world to `_WORLDS` in `tests/system/crash/test_crash_walks.py`.
  Unenforced: `_WORLDS` is keyed by world, not plugin id.

## Known failure modes

- A slot that finished other than by a clean settlement was never admitted, and
  the run stalled (#2214, #2219):
  `tests/vibesys/orchestration/evolve/test_round.py`,
  `tests/vibesys/orchestration/test_search_faults.py`.
- A write turn whose checkpoint core declined was treated as a reply (#2178);
  match `Declined`. The fault menu declines at random:
  `tests/vibesys/orchestration/test_search_faults.py`.
- A strategy's prompts lacked the domain pack (#2141):
  `tests/vibesys/orchestration/domains/test_domain_sections_in_prompts.py`.
- A refactor that changes a decision's id or payload breaks journal
  deduplication on restart; `dynamic` pins its decisions in
  `tests/vibesys/orchestration/dynamic/test_decision_transcript.py`.
  Unenforced: the other searches have no decision transcript.

## Sources of truth

- [flows.md](../../../docs/contributing/flows.md): the flow vocabulary (its
  `role.retry.corrections` is `Role`'s `corrections`).
- `libs/vs-core/src/vs_core/api/search.py` and `api/testing.py`: what a search
  and its tests may import.
- `libs/vs-core/src/vs_core/flows/_roles.py`: `Role` fields, defaults and caps.
- `src/entrypoints/cli/constants.py` (`--outer-loop` choices),
  `src/vibesys/api/catalog.py` (built-ins), `tach.toml` (module graph).
