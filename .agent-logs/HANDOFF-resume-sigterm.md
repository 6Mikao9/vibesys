# Resume after SIGTERM

Branch: `fix/resume-after-sigterm`, created in a new worktree from
`origin/demo/flint-restart-5` at `ce6b555ee6`.

The experiment bundle, reported run directories and launch/server logs were only
read. No experiment was resumed, no candidate or GPU command ran, and no source
state was repaired. Only this fix branch is to be pushed; no PR is created.

## Root cause

Two defects form the reported failure chain.

1. **The upgrade changes accepted command identity.** All 32 recovery blockers
   in the failed record are completed `dynamic.render_role_artifacts` operations,
   not evaluation submissions. The proof `operation_for` rejects their decision
   receipts with `Mismatch(DIGEST)` after decoding under restart-5.
   Commit `24b7fa6b4` added `_Planning.combinable=()` between the starting code
   `4b6d00cf4` and restart-5. Re-encoding an old planner request inserts the new
   default in its context. Removing only `request.context.combinable` from the
   first decoded decision reproduces its saved SHA-256 exactly. Every one of
   these operations had already completed, so replaying its effect is unsafe.
2. **Late release proofs lose synthetic-end provenance.** While stopping the
   blocked recovery, core ends an unanswered session-close request at its bound.
   Its synthetic failure has event identity `blocked-at-close:` and sequence 0.
   A deadline inspection reports a never-begun refusal, also sequence 0, proving
   release and a complete empty child list. `_refine` kept core's failure status
   but replaced its synthetic identity with the executor's identity. A later
   root inspection repeats the same executor observation. Without the synthetic
   marker, core compares its retained failure with that refusal and raises a
   sequence conflict. The receipt actually contains `status=rejected`,
   `sequence=0`, `accepted=false`, `released=true`, and the diagnostic
   `no begun marker: the effect never started`.

There is no null-sequence evaluation observation in this evidence. Every saved
record with an observation has a non-null sequence. The null sequence of the
newly dispatched inspection is normal: its own observation has not committed.
The conflict concerns its target's two different facts at sequence 0.
`EvaluationTiming`, leases and admission timing do not explain the 32 blockers.

The journal records the original cancellation at 12:46:14–15 UTC, the resumed
start at 12:47:38 UTC, adoption at sequence 4215, and the final failure at 4217
at 12:48:52 UTC. Those times distinguish shutdown of the original host from
shutdown of the blocked recovery.

**Old-to-new code triggers the observed recovery blockage.** A matching-code
reload does not insert `combinable` and preserves the digest. Separately, the
synthetic-end sequence defect can occur on matching code whenever the same late
release proof is inspected twice after shutdown. The affected ledger, recovery
and inspection implementations are identical at `4b6d00cf4` and `ce6b555ee`.
The deterministic regression reproduces that defect without any schema upgrade.
SIGTERM alone does not imply failure: the matching-code property resumes after
cancelling controls at generated evaluation submission and poll boundaries.

The old preflight checked decodability, declarations and operation schemas, but
not the integrity of decoded accepted receipts. Its acceptance therefore did
not prove recovery would accept the saved decisions. A pre-SIGTERM snapshot also
cannot exercise synthetic outcomes produced during shutdown.

## Fix and behavior

- Core preserves the synthetic event identity while refining its release facts.
  Repeated inspections, including after serialization/reload, keep its failure
  disposition and release evidence instead of crashing on the sequence.
- Resume validation checks every accepted decision receipt through core's
  existing canonical proof API. Both standalone preflight and the production
  shell startup enforce the check before acquiring a writer lease or committing
  a recovery epoch. Rejected decisions do not grant accepted authority.
- A failed proof produces `CoreResumeError` with `core_resume_invalid`, the
  receipt identity, failed proof field and an explicit matching-code/migration
  instruction. The product maps it to a `ConfigurationError` at
  `resume_resolution`, before attaching its evaluation bridge.
- Preflight uses `StateStore.observe`, avoiding a writer lock and durability
  repair. The earlier `load` call could materialize a lock even during a probe.

This deliberately implements the requested **actionable refusal** for incompatible
state. It does not migrate or silently rehash historical decisions. The reported
failed state is now rejected early; it is not automatically made resumable.
Recovering that state requires a separate explicit migration validated against
its original accepted payloads, receipts and replay identities, or a compatible
unmodified checkpoint. Because the failed resume already reserialized some old
values under the newer models, blindly running old code on that final record is
not a demonstrated repair. Do not edit digests or retry completed effects.

A copy of the actual failed store, revision 24292, was imported into an isolated
test Project for production preflight. Restart-5 accepts its content. This branch
rejects `plan.1/orchestrator.render.1.digest` with the diagnostic above. The copied
record and source file remained byte-for-byte unchanged. No actual resume was
attempted.

## Design

The pure ledger owns disposition and release provenance; runtime resume validation
owns decoding/integrity checks; product wiring owns configuration diagnostics.
Public runtime/core APIs and persisted shapes are unchanged. Dependencies remain
shell-to-core through the existing public proof API, with no new graph edge,
I/O interface, migration or backend-specific branch.

The mechanism sweep covered synthetic close endings, release refinements,
repeated inspections, all lifecycle classes, standalone preflight, direct shell
startup and the product startup path. The latter bypasses `CoreRuntime.resume`,
so enforcing the check only in that factory would have missed actual resumes.
Rejecting unverifiable identity was chosen over a new migration that guesses
historical command identity; preserving provenance was chosen over ignoring
sequence conflicts, which would weaken ordinary observation validation.

## Verification

New tests cover nested added defaults with generated prompt text, matching-code
old and new shapes, refusal before lease acquisition through both startup entry
points, read-only preflight without a pre-existing lock, product diagnostic
mapping, and repeated late release proofs across all lifecycle classes and
serialized reloads. A composed property uses the production shell/core, a durable
Fake store, recoverable executor receipts, virtual time and a Fake SIGTERM source
at evaluation submission and generated polling points.

At unchanged `ce6b555ee`, the final new tests produce **5 failures and 1 pass**:
receipt preflight, read-only locking, direct startup validation, product diagnostic
mapping and repeated release proofs fail; matching-code evaluation SIGTERM/resume
passes. Head verification results follow below.

All pytest invocations used at most two workers. One final baseline invocation
briefly overlapped the head suite's last worker; it exited before the overlap was
noticed. Remaining test runs were kept sequential.

Raw test logs and copied evidence are outside Git because they contain local
paths. No private hostnames, usernames, personal paths or raw run payloads are
included here.

Head verification:

- Final affected suite at nightly depth: **1,046 passed**, two workers. Covers
  request ledger/recovery/deadlines/proofs, blocked closure, job reobservation,
  ledger compaction, runtime shell/dispatch/execution boundaries and preflight,
  the new tests, generated stop/drain/resume stories, server signal-to-cancel
  wiring, product startup ownership/composition and legacy-resume entrypoints.
- Format and lint scripts: passed, with 2,788 files checked for formatting.
- Targeted type checking of all four changed product/library files: passed.
- Test-isolation ratchet, graph consistency, purity against restart-5,
  persisted-shape compatibility and diff whitespace: passed.
- Full module graph validation and file-length validation fail identically at
  base and head. The graph failures are existing interfaces/edges involving
  host probes, contention metrics, dynamic scripted code and benchmark wiring.
  The existing `vs-project/_state.py` file has 2,001 lines against its 2,000-line
  ceiling. Neither check reports a new failure from this branch. No unrelated
  file, allowlist or lint suppression was changed to hide them.
- Additional final nightly run with `--sim-determinism-check`: **18 passed**,
  covering all new tests and the regression-example pinning quality checks.
- The product real-API confinement guard passes. The deterministic-test
  real-API guard reports existing uses in `test_shallow_history.py` and
  `test_resume_off_run_branch.py`; none of the new tests is flagged. The same
  two violations reproduce at unchanged restart-5.
