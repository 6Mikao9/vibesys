# Runtime lease failure investigation

Run: `20261022-142822-af5a8688-lithos-metal-20261022-142822`.
Incident code: `demo/flint-restart-6`, `993d63fed`.
Fix branch: `fix/runtime-commit-resilience`, based on
`origin/fix/multi-objective-promotion`, `b0503eb10c3e6c27eb0e71323fe1d6e5e78ff581`.

## Finding

The final exception describes an already halted runtime, not a commit-confirmation
deadline. The preserved server traceback contains the earlier failure:

1. `_Loop._settle()` calls `CoreRuntime.settle()`.
2. Between synchronous steps, `settle()` calls `_keep_lease()`.
3. `_keep_lease()` calls `_renew()` -> `_renew_now()` -> `CoreRuntime.renew()`.
4. The state store returns `None` from `renew()`. `CoreRuntime.renew()` sets
   `_halted = True` and raises `RuntimeCommitError("runtime lease renewal rejected")`
   at incident `_core_loop.py:665`.
5. While failure cleanup yields, the heartbeat task calls `renew()` on that
   halted shell. Its initial guard raises
   `RuntimeCommitError("runtime inactive, busy or commit unconfirmed")` at
   incident `_core_loop.py:653`.
6. `_settle()`'s `finally: _raise_if_lease_lost()` surfaces the heartbeat error,
   obscuring the earlier renewal rejection. `run()` also raises a failed
   heartbeat during its cleanup. The chained server traceback preserves both.

The store's renewal rejection tests ownership, epoch, the persisted time
watermark, and expiry. The runtime makes renewal time non-decreasing and at
least its commit watermark. The ownership history strongly excludes a normal
competing takeover: the saved runtime record is epoch 1, the successful policy
migration takes a new lease, and the resumed process holds storage epoch 3.
The migration script takes one lease and releases it without increasing the
epoch on the incident code. Thus the migration accounts for epoch 2, leaving
no intervening successful cooperating takeover before the incident.

The supported diagnosis is an expired lease after a delayed renewal. A
synchronous store operation, strategy transition, publication, or commit
observer can block this event loop; the heartbeat is a task on the same loop.
The old code renews between steps but cannot renew during one long synchronous
step. Once the 60-second lease expires, even its unchanged owner is rejected.
The deterministic regression reproduces this through the production runtime.

**Limit of attribution:** no surviving trace measures the blocking operation
or fsync latency. Disk pressure and competing I/O are plausible triggers, but
the evidence does not prove which fsync, lock wait, CPU operation, or scheduling
pause consumed the time. The original portable lease document was replaced by
the migration/resume; the pristine backup contains the machine-local run state,
not that original portable lease. There is no historical process/file-access
audit proving whether another process merely read the project at 15:53Z.

## Evidence and timeline

All times below are UTC on 2026-10-22.

- The policy migration's `v1-28086/before.json` preserves the original typed
  runtime record: storage revision 28086, core revision 27744, envelope epoch 1,
  last committed core time **15:51:14.054724**, and last recorded host renewal
  **15:50:58.427768**. Recorded host renewal can lag the actual small lease
  document; it is not an exact last-renewal timestamp.
- Agent events continue independently: the last implementer phase finishes at
  **15:52:43.407143**. That does not prove the core loop was scheduling normally;
  agent telemetry is also produced from worker activity.
- Core events 4330-4332 are `run_ending`, resumable, at **15:53:02.099834**;
  accuracy `gate_finished`, "evaluation cancelled", at **15:53:10.313866**;
  and `run_failed` at **15:53:11.458678**.
- Product `drive_core_run()` emits `run_ending` from its `finally` after the
  runtime fails. Evaluation cancellation is teardown, not evidence that an
  operator stopped the run or that accuracy caused the initial failure.
- The pristine control inbox contains only two steer commands, at
  **14:29:15** and **15:19:17**, both settled. There is no stop command there.
- The old server log retains the complete chained renewal traceback described
  above. It shows no CAS conflict, `RuntimeCommitUncertainError`, timeout,
  failed fsync, ENOSPC, or storage-write exception in this failure chain.
- The saved binary run head independently records epoch 1, original pid 77664,
  a failed/resumable lifecycle, and the same final generic RuntimeCommitError.
- During the initial read-only inspection, pid 65669 was alive, its new server
  log was empty, and its storage lease was epoch 3. Open-file inspection found
  that server holding the run log and state-store lock; its agent processes
  also had their expected agent-home files open. This is current evidence,
  not a historical audit of 15:53Z.
- The available disk measurement during investigation was about 17 GiB, with
  the data volume at 99% utilization. The operator reported about 22 GB at the
  incident and later about 18 GB. These readings establish pressure, not an
  fsync-duration measurement.

Evidence locations are intentionally expressed without personal paths:

- `<backup-root>/lithos-metal-state-0918/runs/<run-id>/logs/core-events.jsonl`
- `<old-instance-root>/720d65519c24/server.log`
- `<project-root>/.vibesys/state/runs/<run-id>/objective-policy-migrations/v1-28086/before.json`
- `<project-root>/.vibesys/state/runs/<run-id>/core-store/lease.json`

The backup moved during this investigation; use the operator's updated backup
root. No backup or copied run state was written under the synced records tree.
The bundle, backup, and live run state were only read. No live-run control,
signal, restart, deployment, or GPU command was issued.

## Bug decision and fix

An actual takeover or ambiguous commit must stop this shell: blindly retrying
can duplicate external work. An expired lease with the same persisted owner
and epoch is different. Expiry makes ownership available to a successor; it
does not prove a successor acquired it. Under the existing store transaction,
the old owner can safely compare its epoch and extend it before resuming work.

The fix is in `vs-project`, shared by `FakeStateStore` and `LocalStateStore`:

- `renew()` checks matching host, epoch, and non-decreasing time atomically,
  and can extend that unchanged epoch after expiry.
- `verify()`, commits, quarantine, fenced mutations, and ordinary dispatch
  still require an unexpired lease. Expiry alone grants no work authority.
- `acquire()` still transfers ownership only when absent/expired, increasing
  the epoch. If takeover wins the transaction first, the old renewal fails.
  If renewal wins first, acquisition sees a live lease and fails.
- Explicit `release()` advances the epoch as well as ending the lease, so
  every retained token is permanently revoked. This includes tokens from
  before an earlier renewal, even when their old expiry equals release time.
- Uncertain or failed writes, CAS conflicts, backward timestamps, and
  takeover continue to fail closed. No candidate state or external request is
  blindly retried. Late renewal itself changes no storage revision or payload.

This fixes transient scheduler/storage stalls that reach renewal before another
host takes over. It does not promise progress under indefinitely blocked I/O,
continual stalls, actual disk exhaustion, or ambiguous durable writes.

## Resume compatibility

No persisted fields, protobufs, envelope versions, receipts, strategy state,
request identities, configuration, or method signatures change. Existing
version-1 leases and version-2 store documents remain readable; existing older
store migration remains in place. Old readers can also read the new documents.
Epochs remain increasing; explicit release now consumes an epoch, so callers
must not assume they are consecutive. The runtime already requires increasing
epochs rather than adjacency and supports published run epochs independently
of storage lease epochs.

No deployment into the live process was attempted. A running interpreter does
not acquire this fix just because a branch is pushed or source is changed.

## Design checkpoint

- Owner: `vs-project` owns atomic lease semantics; the runtime retains its
  existing heartbeat/upkeep and commit-before-I/O behavior.
- Interface: unchanged signatures, with an explicit late-renewal contract and
  permanent release revocation; no new knobs or public types.
- Direction: runtime -> project store; no new dependency edge or import.
- Coupling: no new caller knowledge. Both implementations share the one
  transition implementation and run the same contract tests.
- Size: the existing store remains the deep owner of its lock, documents, CAS,
  watermark, and fence; callers need no additional internals.
- Alternative: reacquiring a new epoch in the runtime would require reconciling
  all in-flight work and published epochs. An atomic unchanged-epoch renewal
  hides more and preserves already authorized request identities.
- Mechanism/sweep: renewal conflated epoch ownership with unexpired execution
  authority. Reviewed acquire, release, verify, commit/quarantine, fenced
  mutations, runtime renew, upkeep, heartbeat, dispatch, and publication.
  Execution checks keep expiry; release revokes the epoch. The generic error
  masking during cleanup is documented above and is not changed here.
- Functional core/durability: lease decisions stay in the existing shared
  transaction implementation. A confirmed atomic lease write restores
  authority; uncertain writes remain errors. Durable run intent and replay
  behavior are unchanged.

## Verification

- Base reproduction: the final new tests run against the base store
  implementation produce **3 failures**: late renewal against the Fake and
  local store, and the production runtime commit-stall scenario. The runtime
  test pins an 84-second stall at commit 5 under a 60-second lease and also
  generates stalls of 60-180 seconds at commits 1-10. The base runtime reproduces
  the same chained renewal rejection followed by the generic inactive error;
  the generated cases use virtual time.
- Focused fix selection: **7 passed**. The store contract properties cover
  expiry, unchanged revision/payload, takeover by the same or a different host,
  and release revocation of both current and older tokens. The runtime asserts
  terminal completion, one host epoch, and exactly one workspace execution.
- Affected tests at **nightly depth, two workers: 438 passed**. This covers all
  state-store contract, durability, migration, mixed-release, permissions, copy,
  and write-cost tests; runtime shell, preflight, execution fencing, dispatch,
  publications, queue, heartbeat, receipt integrity, signal-resume, parking,
  and blocked recovery; the system run loop; product resume integrity and
  committed views; and the pinned-regression quality check.
- `./scripts/check_format.sh`: passed.
- `./scripts/check_lint.sh`: passed.
- Targeted `ty check` over all four changed Python files: passed.
- Commit-stall regression with `--sim-determinism-check`: passed.
- `check_test_isolation`: passed. `git diff --check`: passed.
- `check_tach_graph --check`: passed. `check_tach` reports existing interface
  and dependency failures involving host probes, HostContention, benchmark
  objectives, and the dynamic scripted agents. Every named file and
  `tach.toml` is unchanged from `b0503eb10`.
- `check_file_length` reports the untouched `_state.py` at 2001 lines against
  its 2000-line ceiling; the base file has the same 2001 lines. Neither base
  quality problem was expanded or suppressed by this fix.
- An initial all-package test run was stopped in favor of the affected selection
  above, to limit unrelated work and disk activity. It is not claimed as a
  completed validation. Test scratch is removed after verification.

Small local verification logs are kept in this worktree's ignored `.agent-logs`;
they contain no copied run-state tree. No PR was opened. Only the requested fix
branch is pushed; no main or experiment branch is pushed.

Affected-test command:

```sh
uv run pytest libs/vs-project/tests/test_*state_store*.py \
  libs/vs-runtime/tests/test_core_shell.py \
  libs/vs-runtime/tests/test_core_preflight.py \
  libs/vs-runtime/tests/test_core_execution_boundary.py \
  libs/vs-runtime/tests/test_core_dispatch.py \
  libs/vs-runtime/tests/test_core_publications.py \
  libs/vs-runtime/tests/test_core_queue.py \
  libs/vs-runtime/tests/test_lease_heartbeat.py \
  libs/vs-runtime/tests/test_resume_receipt_integrity.py \
  libs/vs-runtime/tests/test_resume_sigterm.py \
  libs/vs-runtime/tests/test_run_park.py \
  libs/vs-runtime/tests/test_blocked_recovery_closure.py \
  tests/system/run_loop \
  tests/vibesys/run/test_resume_integrity.py \
  tests/vibesys/run/test_core_commit_view.py \
  tests/quality/test_regression_properties_are_pinned.py \
  --depth=nightly --no-cov -n 2 -q --basetemp=.agent-logs/pytest
```

## Current run risk and operator actions

Pid 65669 runs `b0503eb10`, whose lease/runtime implementation is unchanged from
the incident code. It remains exposed to this same delayed-renewal failure.
Successful migration/resume repaired the previous interruption, not its cause.

Freeing disk by removing known expendable, inactive artifacts elsewhere and
reducing competing builds, tests, copies, or other heavy disk writers should
reduce the chance of another long stall. Keep the machine awake and avoid
suspending the run process. These measures lower plausible triggers; they do
not guarantee prevention. Do not delete active run files or agent state, or
create another large full-state copy on this volume.

There is no evidence that closing a passive desktop viewer is necessary for
fencing correctness. The viewer's read path uses `observe()`, which does not
take the store lock, fsync state, acquire a lease, or advance CAS. Closing an
I/O-heavy client may reduce load; closing a merely connected viewer is not a
demonstrated remedy. Do not run a second resume or state migration against the
live project. A cooperating second writer cannot steal an unexpired lease,
but after expiry it can acquire the successor epoch and correctly prevent the
old process from renewing.

The durable remedy requires deploying the fix on a later authorized restart
or resume. No such action was taken in this investigation.
