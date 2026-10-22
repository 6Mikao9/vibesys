# Profiler probe access diagnosis

## Outcome

The immediate blocker is a VibeSys macOS Seatbelt bug, shared by every host
agent role. The task directory exists and the agent starts in the correct
candidate worktree. Seatbelt denies metadata on its two nearest ancestor
directories. macOS `/bin/sh` needs that metadata for `cd`, even when entering
an allowed directory inside the workspace, and reports `Not a directory`.

The fix grants `file-read-metadata` on those exact ancestor directory entries,
after the ancestor-tree deny. It grants no directory contents, child metadata,
or writes. Operator-root and protected-project restrictions still come later.
No candidate, evaluator, run-state schema, admission policy, or GPU lock changed.

Branch: `fix/profiler-probe-access`, based on origin
`fix/profile-admission-order` at `213a3f40a2a3dbb061dee5456887a77da604eb18`.
The inspected live code is `4b6d00cf4`, candidate
`47cf1983ab53220bccbf5d036b9af43c6ba30de6`.
No PR, GPU commands, or writes to the experiment bundle or candidate fork.

## Journal evidence

All times below are UTC on 2026-10-22. The source is `logs/core-events.jsonl`
of run `20261022-112814-078d2611-lithos-metal-20261022-112814`.

The full exact commands/results for both turns are saved locally beside this
file in `profiler-probe-exact-journal.json` (42 events). The compact selection
is `profiler-probe-failures-exact.json`. These two files remain ignored and
uncommitted because the original events contain personal paths and a hostname.
This handoff uses `<WORKSPACE>`, `<BUNDLE>` and `<EVALUATOR_CACHE>` in place of
those original absolute paths; the command text below is otherwise preserved.

First turn: `inv:profiler:000015.base-warm-prompt-length-discriminator:0`.
It requested a counterbalanced warm 16/544-token timing probe. Sequence 2180
confirms the candidate revision and its isolated worktree cwd.

At 12:00:55.744343, call 2186 / result 2187:

```sh
/bin/zsh -lc ./.vibesys/tasks/flint-m3pro/setup_env.sh
```

Exit 1, exact error:

```text
./.vibesys/tasks/flint-m3pro/setup_env.sh: line 4: cd: ./.vibesys/tasks/flint-m3pro: Not a directory
```

At 12:01:04.820468, call 2198 / result 2199:

```sh
/bin/zsh -lc 'bash "$PWD/.vibesys/tasks/flint-m3pro/setup_env.sh"'
```

Exit 1. Bash gets through setup's directory calculation, but the script it
executes still has a `/bin/sh` shebang. The resulting errors are:

```text
<WORKSPACE>/.vibesys/tasks/flint-m3pro/check_host.sh: line 6: cd: <WORKSPACE>/.vibesys/tasks/flint-m3pro: Not a directory
Traceback (most recent call last):
  File "<stdin>", line 6, in <module>
  [system Python 3.9 pathlib read_text/open frames]
FileNotFoundError: [Errno 2] No such file or directory: '/reference/checkpoint.json'
Host check failed:

```

At 12:01:17.128352, call 2209 / result 2210:

```sh
/bin/zsh -lc "/bin/sh -c 'cd .vibesys/tasks/flint-m3pro && pwd'; /bin/bash -c 'cd .vibesys/tasks/flint-m3pro && pwd'; rg -n 'setup|load_prepared' .vibesys/tasks/flint-m3pro/benchmark/task_env.py | tail -20"
```

The result begins with `/bin/sh: line 0: cd: .vibesys/tasks/flint-m3pro:
Not a directory`, then Bash successfully prints `<WORKSPACE>/.vibesys/tasks/flint-m3pro`.
Earlier sequence 2205 reports `True` for the directory and launcher existence.

At 12:01:21.463706, call 2211 / result 2212:

```sh
/bin/zsh -lc 'bash .vibesys/tasks/flint-m3pro/check_host.sh && /usr/bin/python3 .vibesys/tasks/flint-m3pro/benchmark/task_env.py setup'
```

It prints `Host check passed.` for the Bash invocation, then the nested
`task_env.prepare` invokes `/bin/sh` and repeats the directory/manifest failure,
ending with:

```text
Host check failed (.vibesys/tasks/flint-m3pro/check_host.sh). Fix every missing item it printed, then retry.
```

The diagnostic reply appears at about 12:01:42; final event 2222 at
12:01:43.259470 records `outcome: blocked`, no measurements, no server/GPU work.

Second turn: `inv:profiler:000022.base-single-repeat-control:0`. This is a
single uninstrumented quick replay request, not another 16/544-token probe.
At 12:10:15.988649, call 3052 / result 3053, with output read at 3054 / 3055:

```sh
/bin/zsh -lc "mkdir -p /tmp/flint-base-47cf1983-one-replay; cat <EVALUATOR_CACHE>/manifest.json 2>/dev/null; ps -axo pid,ppid,%cpu,%mem,etime,command | rg 'monolith.serve|session_runner|lockf.*flint-gpu' > /tmp/flint-base-47cf1983-one-replay/processes-before.txt; /usr/bin/python3 <BUNDLE>/resources/evaluators/request-factory/adapter.py --engine <EVALUATOR_CACHE>/bin/session_runner -- .vibesys/tasks/flint-m3pro/benchmark/run.py --workload quick --vs-output /tmp/flint-base-47cf1983-one-replay/result.vs --output-json /tmp/flint-base-47cf1983-one-replay/metrics.json > /tmp/flint-base-47cf1983-one-replay/replay.log 2>&1"
```

Result 3053 reports `zsh:1: operation not permitted: ps` (exit 1). The
semicolon-separated adapter command still ran. Result 3055 reads its log:
the same `check_host.sh: line 6: cd: ...: Not a directory`, missing
`/reference/checkpoint.json`, empty `Host check failed:` diagnostic, and
`Host check failed (.vibesys/tasks/flint-m3pro/check_host.sh). Fix every missing item it printed, then retry.`
The diagnostic reply is at 12:10:29.372846; final event 3063 at
12:10:30.391018 records one launcher attempt and zero replays.

## Receipts

Workspace bindings `7a4b2e9f05ed...` and `3cda4e715158...` both record
`read-only-revision` of candidate `47cf1983...` with distinct attempt workspaces.
Session bindings `557c84d5ab61...` and `8ced1447e316...` record role
`dynamic-profiler`, access `write-artifacts`, and the matching dispatched turns.
Session-access receipts `95eeed1fd2e5...` and `62bcd73a4e16...` record settled
read-only source grants with `violation: null` and `unrestorable: []`.
This is not a source-access settlement failure or a wrong revision/cwd.
The evidence ledger records trusted evaluations separately from these direct
agent tool commands; a completed agent turn does not establish a measurement.

## Code trace and classification

- `src/vibesys/orchestration/dynamic/search/_roles.py:PROFILER` owns the
  read-only revision / write-artifacts role. Its task is observation, so inability
  to enter a readable task directory is not intended profiler policy.
- `libs/vs-runtime/src/vs_runtime/_run_environment.py:HostEnvironment` selects
  host execution for Metal and requires confinement. Its command runner is
  separate from agent confinement. `vs_sandbox/local_shell.py:LocalShellRunner`
  runs framework commands with a fixed workspace cwd; it isolates nothing.
- `libs/vs-agent/src/vs_agent/session_launch.py:confine_to_sandbox` transforms
  agent CLI launches through the workspace confinement. The agent's `execute`
  tool and descendant task scripts inherit Seatbelt. Invoking a file named a
  trusted adapter from inside an agent does not move it outside confinement.
- `libs/vs-sandbox/src/vs_sandbox/host_sandbox.py:SeatbeltSandbox.profile`
  broadly allows reads, denies parent/grandparent trees, then carves out the
  workspace. The omitted exact-ancestor metadata allowance causes the bug.
- Fork `.vibesys/tasks/flint-m3pro/setup_env.sh` uses `set -eu`, computes `HERE`
  with `/bin/sh` `cd`, invokes `check_host.sh`, then `task_env.py`.
  `check_host.sh` uses `set -u` only: failed `HERE` assignment does not stop it,
  so its Python check opens `/reference/checkpoint.json`. Because the Python
  exception goes to stderr but its captured stdout is empty, the final
  `Host check failed:` has no missing-item explanation. That misleading
  diagnostic belongs to the fork, not VibeSys host preflight.
- `benchmark/task_env.py:prepare` always invokes that host check with
  `/bin/sh`, locates the locked environment, then runs `uv sync --locked` under
  `/tmp/flint-task-env.lock`. The environment lock is distinct from the GPU lock.
- `benchmark/run.py` enters the task environment before `main`; `main`
  re-executes under `lockf -k /tmp/flint-gpu.lock` before launching the server.
  Accuracy likewise uses the GPU lock. Keep it per command; never double-wrap
  the adapter or bypass its private lock-held flag. This lock did not cause
  either reported failure: they occur earlier. A separate turn at 12:08:34
  (2922 / 2923) ran `lockf -k -t 1 /tmp/flint-gpu.lock xctrace list templates`
  and got `lockf: /tmp/flint-gpu.lock: already locked`, exit 75. That is real
  contention, not a permissions error, and says nothing about ownership now.
- `ps` denial is a separate process-inspection restriction; it does not show
  absent host prerequisites or missing task inputs.

## Fork-side follow-up (described only)

There is a second task-config gap: `prepare` always runs `uv sync`, but Seatbelt
intentionally denies writes to the shared task-env and uv caches. The fork's
own handoff documents that no write allowance was added. An existing cache
may be read/executed, but this diagnosis did not run uv or measurements there
and does not claim setup will succeed after fixing directory entry alone.

The fork should give confined probes a read-only prepared-environment entry:
validate host prerequisites and use `load_prepared` for the exact `uv.lock`
hash, failing with an explicit operator-setup instruction if unavailable.
Keep cache creation/sync in the existing unconfined trusted setup. This avoids
granting agents write access to shared environments used by trusted evaluations.
Separately, make both shell directory assignments fail immediately with the
offending task path and preserve subprocess diagnostics in host-check errors.
Do not change the candidate fork or the live task manifest to apply these here.

## Design and resume compatibility

Owner: the Seatbelt policy renderer; public interfaces and module graph unchanged.
Data flows from the workspace policy to SBPL to the launch wrapper as before;
no new dependency or resource owner. The pure renderer gains a narrow rule;
the existing shell remains responsible for process lifetime. Durable intent,
receipts, fingerprints, reply schemas, workspace modes and saved core state
are untouched. Resumed sessions build their confinement from this code, so
the fix needs no saved-state migration. Already running processes keep their
old profile; applying this requires a later operator-managed restart/resume,
which this task did not perform. Failed turns stay recorded as failed evidence;
do not rewrite the journal or automatically claim/replay their results.

Mechanism sweep: the ancestor-tree denial is emitted in one Seatbelt renderer
used by all host agent roles. Bubblewrap and Landlock use different mechanisms;
no sibling instance of this SBPL denial was found. Alternatives considered:
forcing Bash only masks failures in nested `/bin/sh` scripts; allowing ancestor
tree reads exposes sibling workspaces. Literal metadata hides more with an
unchanged interface. The regression checks enforce the narrow allowance and
the later operator/protected-path denies.

## Verification

- Deterministic property regression through public `SeatbeltSandbox.wrap`,
  using a Fake filesystem policy interpreter, no time/process/thread/socket API.
  It pins the profiler/task case and varies attempt and task names. It checks
  task entry, ancestor metadata, denied sibling metadata/content/writes,
  read-only task inputs, hidden run state and operator-root precedence.
- With production `host_sandbox.py` restored to base `213a3f40a`, the new
  suite reports 1 failed / 1 passed: `NotADirectoryError` at task entry. With
  the fix it reports 2 passed.
- Separate manual CPU-only macOS reproduction: old policy `/bin/sh` exit 1
  (`Not a directory`), Bash exit 0; allowing only one ancestor still fails;
  allowing metadata on both succeeds. With the full generated fixed profile:
  task entry exit 0, sibling read exit 1, protected task write exit 1.
- Affected deterministic suite (two workers): 39 passed across task entry,
  Seatbelt agent state, operator-root protection, and agent run-state confinement.
- `./scripts/check_format.sh` and `./scripts/check_lint.sh`: passed.
- `check_test_isolation`, `check_doc_citations` for the changed page, and
  `check_doc_links`: passed.
- Additional whole-repository `check_file_length`: fails on unchanged
  `libs/vs-project/src/vs_project/_state.py` (2,001 lines, ceiling 2,000).
  `git show 213a3f40a:libs/vs-project/src/vs_project/_state.py | wc -l`
  confirms the base has the same 2,001 lines. No waiver or unrelated edit added.
- Additional `check_tach`: fails on existing interfaces/dependencies in
  `vs_sim.host_probe`, `vibesys.metrics.HostContention`, the host evaluation
  import and dynamic scripted-agent imports. Every reported file and
  `tach.toml` is unchanged from base. `check_tach_graph --check` passes.
