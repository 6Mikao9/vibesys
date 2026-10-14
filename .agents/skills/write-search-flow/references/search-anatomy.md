# Anatomy of a search

Open this when you need to find where a piece of a search lives, or which names
connect the command line, the plugin and the flow.

## Names: command line, plugin, search

The operator picks the outer loop with `--outer-loop` (values in
`src/entrypoints/cli/constants.py`) and, for the `agent` and `profile-guided`
loops, the inner loop with `--inner-loop` (`single-agent` or `multi-agent`).
`src/entrypoints/cli/loops.py:_agent_policy_descriptor` and its siblings turn
that choice into a plugin id plus a flat options mapping. The plugin id selects
a registration in `src/vibesys/api/catalog.py`; the registration's plan runs
one `@search` flow.

| `--outer-loop` | Plugin ids | `@search` name | Package |
| --- | --- | --- | --- |
| `agent` | `single-agent`, `multi-agent` | `single`, `multi` | `single/`, `multi/` |
| `profile-guided` | `profile-guided-single-agent`, `profile-guided-multi-agent` | `single`, `multi` | same |
| `dynamic` | `dynamic` | `dynamic` | `dynamic/` |
| `evolve` | `evolve` | `evolve` | `evolve/` |
| `plain` | `plain` | `plain` | `issue_queue/` |

The `@search` name is also the namespace of the search's persisted operation
kinds and the prefix of its role ids (`single-implementer`), so renaming it
breaks every recorded run and every `agent.toml` role override.

## Files of one search

Paths are relative to `src/vibesys/`. `<x>` is the package folder.

| File | What it holds |
| --- | --- |
| `orchestration/<x>/search/_search.py` | The root flow under `@search(...)`: measure or seed, start tasks in groups, fold results, `ctx.choose`, return. |
| `orchestration/<x>/search/_config.py` | The config model (a `Value`): only the search's own knobs. |
| `orchestration/<x>/search/_roles.py` | One `Role` per kind of agent question, and `ROLES`. |
| `orchestration/<x>/search/_operations.py` | `StrategyOperations` for the namespace; `owned=` codecs when a library owns a system the search writes to. |
| `orchestration/<x>/search/_prompts.py` | Typed prompt contexts (`RolePromptContext` subclasses) with a `template` literal. |
| `orchestration/<x>/search/_budget.py` | `budget(config)`: attempts, turns, parallelism and refunds, derived from the config and the roles. |
| `orchestration/<x>/search/_*.py` (others) | Child flows (`@flow(snapshot=...)`) and pure helpers: the record the root folds, settlement, admission. |
| `orchestration/<x>/search/api.py` | The public surface, with `__all__`. |
| `orchestration/<x>/core_policy/` | Host-side pure policy: `requirements_for` (which assessments make a candidate eligible), `run_prompt_variables`, `project_run_snapshot`, and `prompts/` (the `.j2` templates, including `<role>_system.j2`). |
| `orchestration/<x>/launch.py` | The launch options: the config plus the task's inputs (`TaskInputs` in most searches), which reject unknown keys; the wiring hands the search only its config. |
| `orchestration/<x>/models.py` | Reply models the agents answer with. |
| `orchestration/<x>/plugin.py`, `__init__.py` | `REGISTRATION` (and `PLUGIN`), loaded lazily from `__init__`. |
| `<x>_core.py` | Wiring: builds the `CorePlan` (search with the operator's models, config, facts, limits, prompt variables, requirements), binds operations to their production owners, and the projector that reads the published record. |
| `<x>_roles.py` | The host's `AgentRole` per agent: system prompt, tools, capabilities. `issue_queue_core.py` declares its agents inline. |

Shared pieces a search builds on, rather than copies:

- `vibesys.candidates.api`: measuring the input (`measure_input`), task terms,
  settlement policy (`settle_for`), winner ranking, `turn_chain` and
  `limits_for` for budgets.
- `vibesys.serial_search.api`: the designer role, plan flow, gates and round
  bookkeeping that `single` and `multi` share.
- `vibesys.hypothesis`: the hypothesis ledger the serial searches and `dynamic`
  publish.
- `vibesys.core_wiring`: the operation catalog and evaluation inputs every
  `<x>_core.py` uses.

## Worked examples

- Smallest serial search: `src/vibesys/orchestration/single/search/_search.py:single`,
  one round per task in a group of one.
- Concurrent tasks with a planner: `src/vibesys/orchestration/dynamic/search/_search.py:dynamic`,
  two groups waited on together with `ctx.first`.
- Ordered admission of concurrent children:
  `src/vibesys/orchestration/evolve/search/_round.py:Round` and
  `src/vibesys/orchestration/evolve/search/_search.py:run_round`.
- A search that writes to a system another library owns (an issue board):
  `src/vibesys/orchestration/issue_queue/search/_search.py:issue_queue`, with
  `owned=BOARD` and a rounds flow that keeps its own snapshot.
- A role that asks an existing agent a second kind of question:
  `src/vibesys/orchestration/multi/search/_roles.py:ROUTER` (its `agent` is the
  orchestrator).
- A child flow that always settles its workspace, including on stop:
  `src/vibesys/orchestration/evolve/search/_child.py:child`.
