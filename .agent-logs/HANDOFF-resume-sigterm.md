# Resume after SIGTERM

Root-cause note (investigation ongoing).

The reported old-to-new resume failure is triggered by a persisted operation payload
change, not by a null evaluation observation sequence. All 32 recovery blockers are
completed `dynamic.render_role_artifacts` operations. Their saved decision digests
fail `operation_for` with `Mismatch(DIGEST)` after decoding at restart-5.

Between `4b6d00cf4` and `ce6b555ee`, `_Planning` gained `combinable=()`. Re-encoding
an old planner request includes this new default. For the first blocked receipt,
removing only `request.context.combinable` from its decoded canonical decision
reproduces its stored SHA-256 exactly. Its observation sequence is 0, not null.
Every record with an observation in the failed state has a non-null sequence.
The Q1c timing/lease fields are not the source of these blockers.

The preflight checked decodability, declarations and operation schemas, but not
the integrity of decoded decision receipts. It accepted a state whose decision
proofs would fail on recovery. A pre-SIGTERM copy also omitted shutdown behavior.
A matching-code restart does not introduce this payload/digest mismatch.

A second, independent defect turns the blocked recovery's shutdown into a crash.
Core ends an unanswered session close with a synthetic `blocked-at-close:`
observation. A later inspection refines its release facts and replaces that event
identity with the executor identity while retaining the synthetic failure status.
Replaying that executor observation then conflicts at the same sequence. This
mechanism can occur without a code upgrade whenever shutdown ends an unanswered
request and inspections subsequently repeat the release proof.

Planned fix: validate operation receipt proofs in read-only resume preflight and
name the failed proof and the need for matching code or explicit migration;
preserve synthetic-end provenance when later observations refine release facts.
Do not guess new digests or replay already-completed effects. Evidence sources
were read-only; no experiment state or bundle files were changed.
