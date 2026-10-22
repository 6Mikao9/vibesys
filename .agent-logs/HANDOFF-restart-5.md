# Flint restart 5

## Branches and scope

- Fork: `vibesys-playground/lithos-metal`, branch `flint-probe-env`, based on
  `47cf1983ab53220bccbf5d036b9af43c6ba30de6` (`flint-profile`). Pushed head:
  `516a34cd0609befe186e4bbe3af7b2ce50d9fd76`.
- Integration: `demo/flint-restart-5`, based on origin `demo/flint-restart-4`
  at `f70745460476c9aa099b1d50c943aa596400189c`.
- Clean merge of `3c9d2789dcf2e7fbbc0776d1007d37a3ddaff06d`, which already
  contains `213a3f40a2a3dbb061dee5456887a77da604eb18`. Both requested fixes
  are ancestors. The lithos-metal submodule pins the fork head above.
- No PR, main/exp push, GPU command, live resume, or live-process action.
  The experiment checkout was read only. New work is in a separate VibeSys
  worktree. Every new commit ends with the requested coauthor trailer.

## F2 behavior and design

The existing `benchmark/task_env.py:prepare` owns environment selection for
both `main` (setup/accuracy) and `enter_task_environment` (benchmark). It now
accepts `FLINT_TASK_ENV_MODE=prepared` explicitly through its environment input.
It validates host prerequisites, computes the current candidate's exact
`uv.lock` SHA-256 cache path, and calls the existing `load_prepared`. It never
invokes uv, acquires the task-environment lock, creates cache directories, or
repairs the shared environment in this mode. Both environment tools must exist
and be executable. Missing/partial environments fail with an explicit
"Operator setup required" message and the unconfined setup command.
Unknown mode values fail before any command runs.

The default remains `sync`; trusted operator setup and evaluation still run
the original host-check/locked-sync path. Operators prepare changed lockfiles
outside confinement with `FLINT_TASK_ENV_MODE=sync`; agents set
`FLINT_TASK_ENV_MODE=prepared` on their probes. No implicit fallback or new
shared-cache write grant was added. Automatic writable-cache detection was
rejected because ordinary access checks do not reliably describe Seatbelt's
write policy, and a probe must not silently select cache mutation.

Mechanism sweep covered both entrypoints, `build.py` (already a prepared-only
consumer), both shell directory assignments and both host Python captures.
`setup_env.sh` and `check_host.sh` now stop immediately on failed directory
resolution and name the task path. The host check captures Python stderr with
stdout so manifest/pack exceptions remain inside its final failure report.
No measurement, accuracy, model, RF replay, GPU locking, or launch policy changed.

The behavior stays inside the existing task module and shell boundary; there
is no new product module, public function, import edge, durable state, or
resource owner. Inputs flow through the existing injected command runner.
The read-only branch reuses the existing lock-addressed loader rather than
adding a second cache layout or exposing a second preparation API.

The task README documents the prepared interpreter and an executable warm
16/544-token TTFT recipe. Its command holds `lockf -k /tmp/flint-gpu.lock`
through the entire server context, warms once, then streams raw-token requests
in counterbalanced order. It extracts the interpreter from setup's final stdout
line so host-check messages cannot become part of the executable path. This
recipe is documented only; no GPU probe was executed.

## Fork verification

- CPU-only task suite: **64 passed**, through `python -m unittest discover -s
  .vibesys/tasks/flint-m3pro/tests -v`, using Python 3.12 with NumPy.
- New regressions cover exact and changed lock hashes, no sync or cache-file
  changes in prepared mode, missing tool requirements, partial
  environments, failed host checks, unknown modes, immediate shell directory
  failures, and preserved manifest/pack subprocess diagnostics.
- Ran the final regression tests against original `47cf1983a` task code in
  a temporary tree: the five added test methods fail as expected (5 failed
  subcases and 3 error subcases). They pass with the fix. Existing ambient
  interpreter import wiring was included in that baseline reproduction.
- Ruff E9/F/I and formatting for changed Python files: passed. `sh -n` for
  both shell entrypoints and `git diff --check`: passed.
- An initial run on system Python 3.9 could not import existing Python 3.10+
  annotations, NumPy, or tomllib. The supported Python 3.12 run above passes;
  no product compatibility change was made for the test interpreter.

## Integration verification

- `./scripts/check_format.sh`: passed (2,784 files formatted).
- `./scripts/check_lint.sh`: passed.
- Focused affected suite: **706 passed**, two workers, 44.61 seconds. It covers
  runtime admission/fairness, sequence progress, host profile offer/capture,
  retry recovery, evaluation requests/deadlines, polling stage output/clock;
  Seatbelt task entry, agent home and operator-root boundaries; and the entire
  dynamic-search directory, including its prompt and transcript goldens.
- Supplemental nightly-depth suite: **113 passed**, two workers, 9.96 seconds.
  It repeats the affected admission/profile/retry/request/polling tests with
  nightly property depth and adds agent run-state confinement.
- `check_persisted_schema_compat.py --base-ref origin/demo/flint-restart-4`:
  passed. No persisted schema migration is introduced.
- Test-isolation ratchet, module graph consistency, documentation links and
  citations for the changed confinement page, and diff whitespace: passed.

One integration-only test correction was necessary. The old
`test_agents_stay_queued_between_an_official_measurements_jobs` required the
starvation behavior that the admission fix intentionally removes. Its updated
public-API scenario now requires all older validations to finish before the
official successor is even submitted, then verifies that the successor also
finishes. There is no additional product-code change. The updated regression
fails at restart-4 (agent-0 cannot finish before successor submission after the
simulator's bounded million-turn guard) and passes at this head, including
nightly depth. The merged fairness property generalizes the same behavior over
schedules, capacity, mixed evidence kinds and sequence arrival points.

### Pre-existing failures, separately verified

An extra, broader attempt over all runtime and confinement library tests plus
dynamic search was interrupted after stalling in Slurm root-proof tests. Its
partial result was 3,200 passed, 30 skipped and 57 failed; this is not a clean
full-suite result. The final focused results above are complete.

Re-ran the 54 completed non-root-proof failure cases against unchanged
`f70745460` in another temporary worktree: **53 failed identically, one passed**.
That one was the obsolete sequence-order assertion corrected above. The 53
pre-existing failures consist of:

- Two runtime generated-value roundtrips missing EvaluationTiming/EvaluationLease
  cases, and the already-stale runtime persisted-shape golden.
- macOS/platform assumptions in bubblewrap probing, imported socket setup,
  Seatbelt conformance and Linux `/proc/self/fd` directory durability.
- Four Docker Fake user-remap/exec cases failing on an empty uid response.
- Slurm executor Fake stage-evidence and recovery cases reporting absent
  stage artifacts or wrong resulting states.

A separate bounded base run of the four involved Slurm root-proof cases
reproduced three failures; the fourth passed in isolation. These tests and
implementations are unchanged from restart-4. The original broad-run stall is
therefore reported separately rather than treated as a successful test run or
as a newly diagnosed host-probe regression. No unrelated test repair, stale
schema-golden regeneration, or platform workaround is included.

Raw diagnostic logs: `restart5-tests.log`, `restart5-base-tests.log`,
`restart5-base-root-proof.log`, `restart5-focused.log`, `restart5-nightly.log`,
`restart5-sequence-base.log`, `restart5-fork-tests.log`,
`restart5-fork-base.log`, `restart5-resume.log`, and
`restart5-task-prompt.log`, under the temporary evidence parent outside Git.
The copied state and render artifacts are also outside Git.

## Exact task and resume compatibility

The task loader explicitly selected this worktree's `flint-m3pro-quick`.
Rendering its loaded manifest retains `[profile]`. Its profile command is the
request-factory adapter calling `benchmark/run.py --profile --workload quick`.
The production host-environment capability path reports Metal, local profile
execution, `captures_profile=True`, and prefill/decode timing fields. The dry
planner render offers profiling, per-revision fact coverage and combination
guidance, hardware-rate bounds, and the shared-host sentence. Processes are
refused by the probe's Fake launcher; no evaluator command executes.

Repeated I4's compatibility check on a fresh copy of persisted live data for
`20261022-112814-078d2611-lithos-metal-20261022-112814`. All decoder/preflight
work uses that copy; the source checkout and machine-local journals are read only.

- Storage revision 19467, RuntimeRecord schema 1, RunEnvelope schema 42:
  `RuntimeRecord`, `RunEnvelope`, and `FlowState` decode successfully.
- Strategy declaration and durable operation capabilities match.
- One DynamicResume and 27 WorkstreamResume checkpoints decode.
- 236 flow-journal steps, 485 publications and 3,879 core events decode.
- Production `resolve_core_resume` accepts the copied project/run and returns
  the decoded record unchanged. No actual resume was attempted.

This is persisted-state/preflight compatibility, as in I4. It does not rewrite
saved candidate revisions or the run's trusted-input baseline (`47cf1983a`).
The integration's new submodule pin does not mutate the existing run.

Local raw logs and copied state are outside the committed tree because they
contain machine-local details. Only this sanitized handoff is committed.
