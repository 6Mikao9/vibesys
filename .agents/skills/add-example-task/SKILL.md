---
name: add-example-task
description: Add or change a runnable example under examples/, a task bundle in .vibesys/tasks/<task>/ (OBJECTIVE.md, vibesys.input.toml, accuracy checker, benchmark, reference/), or an evaluator, task-local or a shared package under resources/evaluators/ built on the SDK in sdk/. Use when touching examples/, examples/registry.toml, tests/examples/, resources/evaluators/, sdk/vs-evaluator/, or a task's [evaluator], [accuracy], [benchmark], [profile], [resources] or [environment] tables.
metadata:
  kind: codebase
---

# Add an example task

Terms used below:

- **Project**: a candidate repository VibeSys optimizes. An **example** is a
  project kept in this repository under `examples/<category>/<name>/`.
- **Task** (or task bundle): one optimization problem in that project, in
  `.vibesys/tasks/<task>/`: `OBJECTIVE.md` (goal and rules agents read),
  `vibesys.input.toml` (the manifest), and the files its commands use
  (accuracy checker, benchmark, optional `reference/` and `Dockerfile`).
  Variants sharing one codebase are one project with one task each.
- **Evaluator**: the programs that judge a candidate. The **accuracy** command
  passes or fails it; the **benchmark** command measures it. An evaluator is
  task-local (commands under the task directory) or a shared **evaluator
  package** under `resources/evaluators/<name>/`, requested by exact name and
  version and shipped inside the VibeSys wheel.
- **Trusted inputs**: files the evaluator reads. Agents must not be able to
  edit them, or a candidate could grade itself.

## Builds on

- `software-design`: parse at the boundary; the manifest and the evaluator
  record stream are closed, validated contracts.
- `testing`: public-API tests, property tests, no real time in deterministic
  tiers. Also load `write-vibesys-tests` for test placement and Fakes.

## When this applies

- A new or changed directory under `examples/`, or an entry in `examples/registry.toml`.
- Any file in `.vibesys/tasks/<task>/` of an example.
- A package under `resources/evaluators/`, or the result protocol and SDKs in
  `sdk/vs-evaluator/` and its reader `libs/vs-evaluator-protocol`.
- Not the domain prompt packs in `src/vibesys/domains/`: load `write-agent-prompt`.

## Steps

1. **Lay out the project and register it.** Put tasks only in
   `.vibesys/tasks/<task>/`; a root `vibesys.input.toml` is rejected
   (`src/vibesys/inputs/_manifest.py:reject_root_manifest`). Add a `[[example]]`
   entry to `examples/registry.toml`; the failure message prints the entry
   (`tests/examples/test_example_registry.py:test_every_example_directory_is_registered`).
   A submodule under `examples/<category>/repositories/` needs
   `external_repo = true`
   (`tests/examples/test_example_registry.py:test_submodule_examples_are_registered_as_external_repos`).
   The entry fields are `src/entrypoints/example_catalog.py:ExampleEntry`.
2. **Write the manifest against its model.**
   `src/vibesys/inputs/_manifest.py:InputManifest` is the schema; unknown keys
   fail. Each command is either a task-owned `command` argv or an `entrypoint`
   of a packaged `[evaluator]` plus `args`, never both. Pick one benchmark
   result contract: `[benchmark.result]` scrapes one JSON field, or
   `result_protocol = 2` makes VibeSys pass `--vs-output <path>` and read a
   validated record stream (`src/vibesys/inputs/_manifest.py:BenchmarkCommand`).
   Run `vibesys validate <project> --task <task>`; CI runs the same entry point
   for every registered task
   (`tests/examples/test_example_registry.py:test_example_validates`).
3. **Keep every file a command reads under the task directory.** `.vibesys/`
   is read-only to agents, so paths written `${PROJECT_ROOT}/.vibesys/tasks/<task>/...`
   are protected; a checker at the project root is not
   (`tests/examples/test_example_registry.py:test_task_inputs_are_read_only_for_the_agent`).
   Every `${PROJECT_ROOT}/...` path in a command must exist
   (`tests/examples/test_example_registry.py:test_command_path_references_exist`).
4. **Put example-specific rules in `OBJECTIVE.md`.** Trusted evaluation checks
   only accuracy and speed; the judge rejects a candidate that breaks any other
   rule the objective states (`src/vibesys/orchestration/dynamic/core_policy/prompts/review.j2`),
   so write candidate rules there (where the engine lives, what may not be
   delegated, precision limits). Text true of a whole domain goes in the domain
   pack instead (`write-agent-prompt`). Unenforced: telling task text from
   domain text needs a reader.
5. **Declare what the run needs, not where it runs.** `[agent] domain` is a
   `src/vibesys/constants.py:DomainName`. `[resources]` is a portable compute
   request (`libs/vs-project/src/vs_project/_manifests.py:RunResourceRequest`):
   counts and an accelerator backend, never a provider, partition or image;
   SkyPilot runs require it. A file named `Dockerfile` in the task directory
   selects the Docker run environment, so name a service image file anything
   else (`tests/vibesys/test_kubernetes_runtime.py`). Model weights come from
   `reference/model` or `reference/meta.json`
   (`tests/examples/test_model_serving_weight_metadata.py`).
6. **Build a shared evaluator only for reuse across tasks.** Follow
   [references/evaluators.md](references/evaluators.md) for the package
   manifest, the result protocol, failure attribution and the Go SDK. A
   package's metadata is validated whenever any packaged task resolves, so one
   broken package fails every packaged example
   (`tests/examples/test_example_registry.py:test_example_validates`).
7. **Try it from the catalog, never in place.** `vibesys example new <example> <dest>`
   copies it into a fresh repository (`tests/entrypoints/test_example_cli.py`);
   `vibesys example` imports no run engine (`tests/architecture/test_light_entrypoints.py`).

## Testing

- The registry module covers structure for every task with no Docker, GPU or
  weights; needing those never exempts an example. A static check that fails
  today goes in the entry's `known_failing`, a strict xfail
  (`tests/examples/test_example_registry.py`).
- Test example-specific logic in `tests/examples/test_<example>_*.py`, loading
  the task through `tests/examples/example_task.py:load_example_task`. These
  run in the deterministic tier: a new real subprocess, thread or clock fails
  `tests/quality/test_real_apis_confined.py`.
- A test that compiles a candidate and runs a real evaluator on it carries the
  `native_build` marker, which the default run deselects; list its file in the
  `examples_native_evaluator_tests` group of `.repoctl/checks.toml`, as
  `tests/examples/test_prioqueue_evaluator.py` is.
- Test a Go or Rust evaluator package with `go test` beside its code; CI runs
  it in the native job when the package changes (`.github/workflows/test.yml`).
- A test file inside `examples/**/.vibesys/` is outside pytest's `testpaths`
  and runs only if a group in `.repoctl/checks.toml` names it; add it to the
  `examples_tests` group or move the test to `tests/examples/`.
- After renaming or deleting an example, every reference to its path in docs,
  workflows and scripts must still resolve
  (`tests/examples/test_example_registry.py:test_example_paths_referenced_outside_examples_exist`).

## Known failure modes

- A task without `reference/model` or `reference/meta.json` failed at startup in
  every run environment (#1118; `tests/examples/test_model_serving_weight_metadata.py`).
- An evaluator tool's native build needs worked only on base images that
  happened to ship them; declare them as `system_packages` (#1828;
  `libs/vs-runtime/tests/test_bundled_evaluator_system_packages.py`).
- A task-level `Dockerfile` that built a service image silently selected the
  Docker run environment and broke the documented launch (#1098;
  `tests/vibesys/test_kubernetes_runtime.py`).
- Benchmark workers that start measuring before all are ready report a skewed
  or partial-mix sample; rendezvous before the measurement starts (#2065;
  `.github/workflows/test.yml` runs the package's `benchmark_test.go`).
- Example-specific contract text in a shared prompt reached runs of other
  examples; it moved to that example's `OBJECTIVE.md` (#2146). Unenforced:
  needs a reader, see `write-agent-prompt`.
- A candidate broke a rule the objective implied but did not state; state
  every rule (#1152). Unenforced: completeness needs a reader.
- Test files under `examples/model-serving/qwen3.5-397b-a17b-mi300a-bespoke/`
  and `qwen3.5-9b-mi210`'s `benchmark/test_profile.py` are named by no check
  group, so CI never runs them. Unenforced: mapping arbitrary check-group
  command lines to the files they collect would breed false positives.
- A local `replace` in an evaluator's `go.mod` builds in CI and fails where
  VibeSys runs the package (`tests/evaluators/test_evaluator_go_modules.py`).

## Sources of truth

- `src/vibesys/inputs/_manifest.py`: the task manifest schema and how commands resolve.
- `src/entrypoints/example_catalog.py`: the registry format and catalog listing.
- [docs/contributing/examples.md](../../../docs/contributing/examples.md): registry fields and static checks.
- [docs/running-vibesys.md](../../../docs/running-vibesys.md): task layout, task Dockerfiles, `vibesys example`.
- `sdk/vs-evaluator/PROTOCOL.md`: the evaluator result protocol.
- `libs/vs-runtime/src/vs_runtime/_failure_classification.py`: whose fault a failed stage is.
