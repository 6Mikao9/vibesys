# Evaluation admission investigation

Base: `f70745460476c9aa099b1d50c943aa596400189c`.
Branch: `fix/profile-admission-order`. No PR. No device commands.

## Evidence and diagnosis

Read-only evidence: the supplied run's `logs/core-events.jsonl`,
`receipts/evaluation-jobs`, `receipts/operations`, and `core-evaluation-tries`.
No experiment files were changed. No private machine identifiers are reproduced here.

The reported trace does **not** establish FIFO overtaking:

- The input starts accuracy at journal sequence 28 (11:28:33Z), then benchmark
  at 393 (11:34:30Z), ending at 768 (11:38:58Z).
- The first candidate validation was submitted at 11:33:24.941295Z;
  three more validations arrived at 11:34:14Z, 11:37:12Z and 11:38:10Z.
- The profile was submitted at 11:39:08.271049Z. Its durable request has one
  `profile` stage and no sequence admission lease.
- The first candidate's durable request has purpose `local-validation`, stages
  `accuracy, benchmark`, and no sequence lease. Its benchmark starts at
  sequence 1348 (11:48:42.425930Z), ending at 1716 (11:53:20.963030Z).
  Thus this command predates the profile in submission FIFO; it is not proof
  that a later official sequence overtook the profile.
- Queue-tool observations report one executor lease running (including at
  sequence 1778), not two device commands. Search slots and executor leases
  are different counts. The profile is already bound in the search queue;
  subsequent planner receipts show it in `measuring` rather than `waiting`.

Code owners: `search/_queue.py` applies planner ranking before binding a
workstream. Agent validations submit directly to the executor. The ranking
is not an executor priority field. `_polling_evaluation.py` admits PROFILE,
ACCURACY and BENCHMARK through the same `EvaluationAdmission` FIFO. No
exclusive or whole-host profile lease exists.

A separate starvation mechanism exists: `_polling_evaluation.py` reserves a
sequence lease after a completed nonfinal member and lets every successor
bypass `acquire`. A queued independent job can then wait through arbitrarily
many successors or an indefinite pause before the successor is submitted.
The supplied requests have no such lease, so this mechanism must not be
presented as the proven cause of this particular trace.

## Fix and code references

There are two reproducible admission defects, independent of the older backlog
shown in the supplied trace:

1. At the base, `PollingEvaluationExecutor.submit` starts a task and `_admit`
   registers its FIFO position later, inside that task. An adversarial virtual
   schedule (seed 42) starts later submissions first. Queue order was task-start
   order rather than submission order.
2. A sequence keeps a nonfinal member's slot after completion. Successors skip
   admission entirely, even with older independent jobs waiting. A delayed
   successor can keep the whole pool idle indefinitely when every slot is
   retained. A stream of successors can overtake the waiting job without bound.

The executor now registers resource jobs synchronously with submission, before
creating their tasks (`libs/vs-runtime/src/vs_runtime/_polling_evaluation.py:157`).
`EvaluationAdmission.enqueue` and `wait` separate registration from the wait for
capacity (`libs/vs-runtime/src/vs_runtime/_evaluation_admission.py:61`).
`acquire` remains the combined API for other callers.

On completion, a retained member parks its lease
(`_polling_evaluation.py:394`). A parked lease remains reserved while
uncontended. A waiting job reclaims idle reservations immediately, whether it
arrives before or after the member completes (`_evaluation_admission.py:106`).
A sequence whose reservation yielded enqueues its next member behind existing
waiters. Cancellation releases both active and registered-but-unstarted jobs.
Queue advancement wakes other waiters so spare slots can fill under reordered
wakeups.

The shared pool owns all of this. PROFILE, accuracy, benchmark, official
sequences and agent validations use the same FIFO. Running requests keep their
chosen stage order and finish their stages before yielding. There are no
strategy-specific priorities, fixed gate orders or new caps. Planner
`evaluation_order` still governs the strategy queue (`search/_queue.py:164`);
it does not reorder executor submissions or preempt an earlier validation.
This correction does not promise that a newly submitted profile skips the
older validations observed in the live run.

Mechanism sweep: the only runtime pool consumer is the polling executor, with
`acquire` also exposed for other resource callers. Searched all runtime
admission/acquire/lease paths, PROFILE capture, host serialization, retry
recovery, dynamic stage-list construction and scope cancellation. Host command
serialization remains a separate per-run lock; PROFILE requests no special
exclusive lease. Agent validation bypasses the strategy ranking, but goes
through the same executor pool.

## Design checkpoint

- Owner: `vs-runtime`'s existing evaluation admission pool; no new module.
- Interface: add nonblocking registration, registered waiting and idle parking;
  keep combined acquisition and idempotent release. The pool hides contention
  handling from its caller.
- Direction: existing runtime-to-evaluation/core/simulator dependencies only;
  no new imports across product modules or libraries, no Tach edges.
- Coupling: no strategy data or backend names enter the pool.
- Size: lease settlement is a separate executor method, keeping its lifecycle
  loop within lint complexity limits.
- Alternative: releasing every lease after every member would also prevent
  starvation, but discard uncontended retention. Explicit idle reservations
  preserve that fast path while fixing the mechanism once.
- Mechanism: registration was deferred to background task execution; retained
  successors bypassed FIFO. Both now go through the same pool owner.
- State/I/O: admission state remains process-local in the existing async pool;
  durable requests remain the recovery intent. No new clock, process, thread,
  network call or persisted contract is introduced.

## Regression and verification

New public-API property:
`libs/vs-runtime/tests/test_profile_admission_fairness.py:89`.
It uses `Sim`, `FakeEvaluation`, `FakeCommands` and `FakeWorkspaces`, with
Hypothesis-generated task schedules, 1–3 slots, mixed PROFILE/accuracy/benchmark
jobs, and arrivals both during and between official sequence members.
It asserts submission FIFO, progress without a successor being submitted,
subsequent sequence completion and complete release. Pins include one slot
with a lone profile (seed 0) and three slots with mixed work (seed 42).
Every wait has the simulator's hang guard; no real time or threads.

Copied the final regression into a separate detached `f70745460` worktree and
ran it against that worktree's runtime sources: **failed**, as expected:
`waiting-0 finishes without waiting for an idle sequence's successor did not
hold after 1000000 loop turns`. Both explicit cases failed at the base. The
same test passes at the fixed head. During development, the seed-42 case also
exposed submission reordering after idle yielding alone was implemented;
registering in `submit` fixes it.

Updated `test_official_sequence_rejoins_fifo_between_jobs` to hold an older
validation while a sequence successor submits, verify that the successor
queues behind it, then drain all earlier validations before the successor.
Both possible official gate orders are generated and pinned. Existing mixed
sequence tests retain their capacity and chosen stage-order assertions;
only cross-job adjacency is intentionally removed.
Existing cancellation, scope close and durable restart tests also pass.

Successful checks on the final product code:

- `./scripts/check_format.sh`
- `./scripts/check_lint.sh`
- `uv run python -m scripts.check_test_isolation`
- `uv run --locked --group proto buf lint`
- Core value bindings regenerated with `buf.gen.core.yaml`; only the lease
  description changed in generated code.
- **107 passed**, `--depth=nightly --no-cov -n 2`, across:
  `test_profile_admission_fairness.py`, `test_evaluation_slot_admission.py`,
  `test_host_profile_capture.py`, `test_retrying_evaluation.py`,
  `test_evaluation_requests.py`, `test_local_measurement_admission_deadline.py`,
  `test_polling_stage_output.py`, `test_polling_evaluation_clock.py`.
- **25 passed**, core contract values and the property-regression pin check.
- Seeds 0 and 42 are pinned in the final property run. An earlier adversarial
  seed-42 admission run passed all 12 tests; simulator determinism replay was
  also checked during development.

Additional checks found existing base failures, reproduced separately at
`f70745460`; they were left outside this fix:

- `scripts/check_tach.py`: existing host-probe/public-interface and dynamic
  model dependency violations. No new edge is introduced by this fix.
- `scripts.check_file_length`: `libs/vs-project/src/vs_project/_state.py` is
  already 2001 lines, over its 2000-line limit.
- `test_runtime_record_schema_version.py`: the base's golden already lacks
  Q1c EvaluationLease/EvaluationTiming and their containing fields.
- `test_real_apis_confined.py` deterministic-test scan: pre-existing real-impl
  uses in `test_shallow_history.py` and `test_resume_off_run_branch.py`.
  The new regression adds none of the reported violations.

## Resume compatibility and limits

No protobuf fields, field numbers, JSON payloads, versions, request identities
or stage lists change. The proto comment and its generated Python description
now document fair retention. Saved `EvaluationLease(key, retain)` values still
validate unchanged, including retained nonfinal members. Retry recovery still
replays an unsubmitted durable request under its original identity
(`_retrying_evaluation.py:203`), through the corrected submission path.

Computed the complete effective persisted runtime shape at base and head:
both have SHA-256
`517daa90363dd0c860bf37a1db9d8f9878eca1bc09ddd7f437e64b6845fe6160`.
The stale checked-in golden was not regenerated or used to claim a migration.
No saved run data needs rewriting. Restart tests exercise reacquisition and
scope cleanup using the existing saved contracts.

FIFO here means registration order in the current executor, including recovery
submissions; it does not reconstruct a previous process's lost in-memory queue
order or introduce new durable queue metadata. Existing recovery dispatch
continues to determine the order in which old requests are resubmitted.

The live experiment was not restarted or modified. No device commands ran.
Only the requested fix branch was pushed; no PR was created. The initial
root-cause note was pushed as commit `3c0fec246` before implementation, within
the first-commit deadline. The completion commit contains this handoff and the
verified fix.
