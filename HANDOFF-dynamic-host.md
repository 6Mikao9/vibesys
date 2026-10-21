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
  workspace acquisition, operation offers, agent Git writability, dynamic and
  evolve wiring, and trusted executor creation. Those callers consume the
  corrected environment fact; evolve consequently gains isolated host children.
- Functional core: dynamic's pure queue still schedules measurements; the thin
  runtime shell owns admission and cancellation. Existing durable workspace and
  measurement intent/recovery remain authoritative; the lock is transient and
  recreated only after existing run recovery has settled prior work.

## Verification and final handoff

Pending implementation and local evidence.
