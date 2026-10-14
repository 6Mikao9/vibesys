# Evaluators

Open this when a task needs an accuracy or benchmark program beyond a short
task-local script, or when changing a package under `resources/evaluators/`,
the result protocol in `sdk/vs-evaluator/`, or its reader
`libs/vs-evaluator-protocol`.

## Task-local or packaged

- **Task-local**: the commands live in `.vibesys/tasks/<task>/` and the
  manifest names them with `command = [...]`. Use this for checks and
  workloads that only one task needs. `[evaluator] source = "<dir>"` copies a
  task-local evaluator tree into the workspace under `_evaluator/`
  (`src/vibesys/inputs/_manifest.py:EvaluatorInput`).
- **Packaged**: a directory under `resources/evaluators/<name>/` with a
  `vibesys.evaluator.toml`, requested by exact `[evaluator] name` and
  `version`; task commands then use `entrypoint` and `args`. Use this when
  several tasks share the evaluator (the stack, queue, map and priority-queue
  scenarios share one package each). The runtime records the package's
  `sha256:` content digest with the run.

`resources/` is staged into the wheel as `vibesys/_resources`
(`packaging/resources_packaging.py`, `tests/packaging/test_resources_packaging.py`),
so packaged evaluators reach installed users; `resources/README.md` still says
otherwise.

## The package manifest

`libs/vs-runtime/src/vs_runtime/_evaluator_packages.py:EvaluatorPackageMetadata`
is the schema; `resources/evaluators/stack/vibesys.evaluator.toml` is a small
real one, and `resources/evaluators/README.md` explains the tokens.

- `entrypoints` maps a public name to an argv prefix. Task `args` are appended
  without a shell. `${PACKAGE_ROOT}` expands to the package directory;
  `${PROJECT_ROOT}` in a task argument expands to the candidate root in the
  run environment.
- `toolchains` asks the run environment for Go or Rust.
- `[tools.<name>]` with `kind = "cargo-git"` installs a Rust binary from a
  full commit SHA with `--locked`; reference it as one whole argv element
  `${TOOL:<name>/<bin>}`. List the Debian packages its build needs in
  `system_packages`; a Docker target installs exactly those.
- `protocol_version` in this file is the package-metadata schema version, not
  the result protocol version the benchmark speaks.

The bundled collection is loaded as a whole when any task resolves a package,
and a duplicate `name` plus `version` is an error, so bump `version` when you
fork a package for an incompatible change.

## The result protocol

`sdk/vs-evaluator/PROTOCOL.md` is the authoritative definition. A benchmark
with `result_protocol = 2` receives `--vs-output <path>` and writes JSON lines
there: one `hello` declaring its metrics, then one `result` row or an `error`.
The run's objectives must name metrics the evaluator declares as required
(`libs/vs-evaluator-protocol/src/vs_evaluator_protocol/measurement.py:check_objectives`).
Conformance fixtures in `sdk/vs-evaluator/fixtures/` hold the reader
(`libs/vs-evaluator-protocol/tests/test_vs_evaluator_protocol_fixtures.py`) and
the Go SDK (`sdk/vs-evaluator/vseval/fixture_test.go`) to the same corpus.

## Whose fault a failure is

Since #1430, a failed benchmark is the candidate's, the machinery's
(infrastructure, measured again within the submission bound), or ambiguous
(measured once more, then charged to the candidate). One table decides it from
how the stage ended and what the evaluator wrote:
`libs/vs-runtime/src/vs_runtime/_failure_classification.py`, tested in
`libs/vs-runtime/tests/test_failure_classification.py`. What an evaluator
author controls:

- **Write `hello` first, before any long setup.** A nonzero exit after
  `hello` is the candidate's; the same exit with no record at all is only
  ambiguous, so a broken candidate costs an extra measurement.
- **Report a candidate failure as an `error` record**, with what was measured
  before it stopped, rather than exiting without a record.
- **Do not catch and relabel infrastructure faults.** A lost node or a
  sandbox timeout has no exit status and is classified as infrastructure
  without the evaluator's help.
- A candidate that edits evaluator-owned files fails as its own fault
  (`libs/vs-runtime/src/vs_runtime/_trusted_evaluation.py`).
- Log patterns that prove a serving candidate died by its own fault at startup
  belong to the domain (`candidate_startup_causes` in
  `src/vibesys/domains/llm_serving/__init__.py`), not to a task.

## Go SDK and module versions

Go evaluators require the published `github.com/uw-syfi/vibesys/sdk/vs-evaluator/vseval`
module by version, never through a local `replace`, because the package runs
from a copy where a relative path does not resolve
(`tests/evaluators/test_evaluator_go_modules.py`). To change the SDK and a
consumer together, use an uncommitted `go.work` as `sdk/README.md` describes,
then tag the SDK and bump each consumer's `go.mod`. Check the version in the
consumers' `go.mod` files; `sdk/README.md` names an older one.

## Benchmark design

- Rendezvous all workers before the measurement window opens, and complete at
  least one full workload batch per worker, so scheduler delay cannot produce
  an empty or partial-mix sample (#2065, `resources/evaluators/unordered-map/DESIGN.md`).
- Gate the benchmark on accuracy unless the measurement is meaningful without
  it (`requires_accuracy`, default true, in
  `src/vibesys/inputs/_manifest.py:BenchmarkCommand`).
- For serving tasks, declare a short fixed `[profile]` workload that skips the
  benchmark's preflight and warmup gates, so profiles exist below the gate
  ([docs/running-vibesys.md](../../../../docs/running-vibesys.md)).
