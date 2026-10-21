# Dynamic host candidate isolation

## Reproduction

Base: `dd818c7e5`. Through the dynamic plugin's public planning API, a
`RunEnvironmentView(paths=AgentPaths(), workspace_git_writable=True)` reports
`env_kind=host`, `parallel_candidate_obstacle=no_own_container` and raises:

```text
the dynamic loop cannot run on the 'host' run environment: it cannot open isolated candidate sandboxes (parallel candidate evaluation) because its agent does not run in a container of its own; choose a run environment that can, such as docker, slurm or modal
```

The exact local command and output are in `.codex/dynamic-host/reproduction.log`.
It calls `PLUGIN.core.plan` using the existing resolved-context test support;
there is no agent invocation, model download, or accelerator work.

## Design before implementation

Each dynamic workstream needs a separate workspace, independently scoped agent
confinement, and trusted candidate evaluation at its recorded revision. The
existing runtime already opens linked candidate worktrees and constructs an
agent environment for each. Host confinement can scope a Seatbelt profile to
that worktree; the framework's trusted evaluation remains outside that profile,
with the existing protected-input integrity check. Shared Git metadata stays
read-only to the agent. Seatbelt's documented broad reads, shared temporary
directories and host network remain limitations; this change promises no
container-equivalent host security boundary.

A host-only accelerator is one shared device. Trusted candidate accuracy and
benchmark commands must not overlap across worktrees. Agent coding may remain
concurrent. Arbitrary commands an agent runs outside trusted evaluation are not
an exclusive-device service; operators must coordinate other runs and external
GPU users (the experiment uses an outer `lockf`).

Alternatives, on one rubric:

| Alternative | Guarantees | Facts established at | Trusted surface | Migration cost |
| --- | --- | --- | --- | --- |
| A: independently confined host candidates plus exclusive evaluation admission | Separate worktrees and agent profiles; one trusted evaluation stage on the device at a time; concurrent coding | Host environment validates enforced confinement; runtime factory owns the shared admission gate | Existing worktree, host confinement and trusted executor owners | Small additive environment fact and executor input; existing container rules preserved |
| B: force all host-only dynamic concurrency to one | No overlapping workstreams, but a workstream still needs its own workspace and confinement | Product launch policy | Launch policy plus existing runtime | Small policy change, sacrifices coding throughput and still needs real host candidate capability |
| C: one shared workspace, sequential workstreams | No concurrency, weaker workspace lifetime and recovery isolation | Strategy-specific workspace handling | New special cases in strategy and restore/adoption machinery | Largest semantic change; risks cross-candidate state and drift |

Choose A. Candidate isolation is independent of owning a container. The host
environment must affirm enforcement before advertising its candidate capability;
unconfined or borrowed sessions remain refused. A shared runtime admission gate
covers root/input, candidate and agent-requested trusted stages, rather than
relying on the dynamic search's queue alone. Waiting is cancellable; cancellation
of running work drains its worker before admission is released. Container
executors retain their existing independent workspace locks and concurrency.

Design checkpoints:

- Owner/interface: `vs_runtime` owns environment capability and trusted execution;
  `RunEnvironmentView` gains a host-confinement fact and the trusted executor
  factory accepts a shared admission lock. No new module or cross-module edge.
- Direction/coupling: wiring passes typed facts downward; core/search never
  branches on a backend name. The shared resource belongs to the run's existing
  workspace factory, not a global or a strategy.
- Size: extend the existing narrow owners; no new orchestration subsystem.
- Second interface: B would expose host-specific width policy to dynamic;
  A hides scheduling enforcement beneath all trusted evaluation callers.
- Mechanism/sweep: support was inferred from container ownership, conflating
  confinement with execution placement. Searched capability derivation,
  workspace acquisition, operation offers, agent Git writability, all strategy
  wiring, and trusted executor creation. Those callers consume the corrected
  environment fact. `plain` (issue queue) is now admitted on safely confined
  hosts; `single`, `multi` and `evolve` select candidate worktrees rather than
  the exclusive root there. Evolve can retain concurrent coding. All share
  serialized host trusted evaluation. Container behavior is unchanged.
- Functional core: dynamic's pure queue still schedules measurements; the thin
  runtime shell owns admission and cancellation. Existing durable workspace and
  measurement intent/recovery remain authoritative; the lock is transient and
  recreated only after existing run recovery has settled prior work.

## Implementation and evidence

The host confinement classes declare whether they enforce candidate workspace
and protected-path isolation. Bubblewrap and Seatbelt do; Landlock does not.
`HostEnvironment.prepare` requires enforcement and advertises the validated
fact on `RunEnvironmentView`. An unconfined injection never gains the fact.
The old container-specific obstacle is now `NO_OWN_CONFINEMENT`; borrowed
agent sessions remain refused. There are no backend-name checks in a search.

`WorkspaceResourceFactory` owns one asynchronous admission lock for host
candidates, passed to every trusted executor, including the root. Container
executors keep separate locks. The gate covers trusted stages independent of
strategy queue width, so an explicit larger `evaluation_slots` cannot bypass
it. Existing command cancellation drains the worker before releasing the gate.

The new host-session regression fails at `dd818c7e5` on
`assert session.view.supports_parallel_candidate_evaluation` and passes at the
head. The property test pins a host-only case and varies confinement facts,
borrowed-session facts and diagnostic environment names. Factory tests cover
separate worktrees, all trusted stage types, waiting/running cancellation, and
a container control that permits two concurrent executors. They use public
APIs, owning-library Fakes and the deterministic simulator.

### Local scripted run

Command (from the worktree):

```sh
lockf -k /tmp/flint-gpu.lock uv run python .codex/dynamic-host/smoke.py
```

The script uses the real CLI parser and production launcher with:
`--outer-loop dynamic --backend metal --agent-backend scripted --profiler none
--max-rounds 2 --max-in-flight 2 --evaluation-slots 4 --max-run-seconds 180`.
Only the agent factory is supplied the existing `VALUE_WORKLOAD` responders
from `test_scripted_run_e2e`; the tiny accuracy and protocol-v2 benchmark are
from `dynamic_loop_harness`. The compute backend, environment selection, Git,
worktrees, state stores, command processes and trusted evaluation are real.
No LLM runs and no model weights are downloaded.

Run: `20261021-223030-d516645d-smoke-project2-20261021-223030`.
The first diagnostic says the Metal backend cannot be reached from Docker and
selects the host under Seatbelt. Event-log excerpt (UTC):

```text
22:30:47.620719 gate_finished  op:input/measure.1 accuracy  completed
22:30:47.626810 gate_finished  op:input/measure.1 benchmark completed throughput=1
22:31:03.687552 round_finished round-1 completed judge=pass throughput=4
22:31:12.263189 round_finished round-2 failed    scripted lost turn
22:31:13.481524 round_finished round-3 completed judge=pass throughput=4
22:31:18.953874 round_finished round-4 completed judge=pass throughput=7
22:31:20.742203 run_finished   completed
RESULT succeeded=true status=completed failure=null
```

The synthetic values above test orchestration, not Metal performance. The MLX
example is a target-model bundle with HTTP evaluators and no serving
implementation. The existing scripted agents have recipes for the queue, not
an LLM engine, so the permitted tiny scripted evaluator is used instead.
Available disk was 64 GiB before the run; no large download was needed.

A separate real Seatbelt probe creates two sibling workspaces. It permits an
own-workspace write and denies sibling reads and writes, protected-file writes
and hidden-state reads, each denied operation returning `Operation not
permitted`. The protected file and sibling source remain intact.

Raw logs and runnable smoke/probe scripts are local in
`.codex/dynamic-host/` (gitignored): `reproduction.log`,
`regression-base.log`, `smoke.log`, `smoke-events.jsonl`, and
`confinement-probe.log`.

### Checks

- `./scripts/check_format.sh`: pass, 2,707 files formatted.
- `./scripts/check_lint.sh`: pass.
- `./scripts/check_ci.sh`: absent at this base, so not run.
- `uv run tach check`, the stricter `scripts.check_tach` wrapper, and
  `scripts.check_tach_graph --check`: pass.
- Narrow host/wiring/factory tests: 38 passed.
- Runtime trusted-evaluation/workspace/request suite: 143 passed; it reports
  existing macOS `os.waitid` thread warnings in unchanged process code.
- Expanded factory tests with container controls and simulator determinism
  checking: 21 passed. The earlier nightly-depth factory run: 15 passed.
- Real-API confinement guards: 165 passed.
- Confinement tests: 10 passed, 2 platform skips.
- `scripts.check_test_isolation`: within the recorded baseline.
- Documentation links and citations: pass.
- Native macOS `./scripts/check_types.sh`: four diagnostics in unchanged code
  for Linux-only `os.waitid`, `signal.sigtimedwait` (two sites), and
  `os.pidfd_open`. `uv run ty check --python-platform linux`: pass.
- `uv run pytest tests/vibesys -n 6 --no-cov -q`: 4,655 passed,
  45 skipped, 49 failed in 395.30 seconds. All 49 failed node IDs also fail at
  `dd818c7e5` (49 failed in 87.25 seconds), confirmed with the detached base's
  source roots and test files. The replay and failure details are in
  `base-failures.log`; these include Linux-only profiler expectations,
  macOS-unavailable process/network mechanisms, and existing materialized-link
  and Slurm test failures.

### Remaining limits

- This does not validate the Flint serving engine or an actual MLX benchmark;
  those belong to the engine/bundle work. The scripted evaluator exercises
  the real Metal-selected host path without issuing GPU kernels.
- The admission gate is per run. Other runs, manual GPU commands and arbitrary
  commands agents launch must still be coordinated by the operator; the
  experiment's outer `lockf` coordinates runs on this Mac.
- Seatbelt retains its documented broad-read, shared-temporary-directory and
  host-network limits. Landlock is refused for parallel host candidates.
- The pre-existing host `[build]` path still refuses candidate builds when no
  confined command runner is available; this change never runs such a build
  unconfined. The target MLX bundle and the tiny scripted evaluator have no
  build stage.
- Container execution, device assignment and candidate isolation are unchanged.
  Existing unrelated dirty submodule checkouts were left untouched.

The implementation branch is `exp/flint-dynamic-host`. Integration uses a fresh
fetch and a normal merge into `exp/flint-lithos-metal`, preserving concurrent
bundle work; final pushed SHAs and wall clock are reported with the handoff.
