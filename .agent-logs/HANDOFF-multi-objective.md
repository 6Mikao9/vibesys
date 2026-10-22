# Multi-objective promotion and restart handoff

## Scope and outcome

Run: `20261022-142822-af5a8688-lithos-metal-20261022-142822`.
Task: `flint-m3pro-quick`.
Investigation base: `origin/demo/flint-restart-6`, `993d63fed214609c438421b02c1bd1703fadbfb0`.
Fix branch: `fix/multi-objective-promotion`.
The branch also merges `fix/agent-process-lifetime`, `1801d95d8f17f991b4fe77679ac9c74742249084`.

All implementation and verification used separate worktrees and temporary copies.
The live candidate checkout and live run state were read only. No evaluator,
agent, model, or GPU workload was launched. No PR was created. Only the fix
branch is to be pushed.

## 1. What dynamic search did at the investigation base

It was **scalar headline-only**, not lexicographic and not Pareto:

- `src/vibesys/orchestration/dynamic/search/_outcomes.py:assertion` calls
  `beats_input`, which reduced both candidate and input to their first metric.
  Passing the trusted gates and beating that scalar made a settlement eligible.
- `src/vibesys/candidates/_settlement.py:settle_for` separately retains a
  candidate that passed accuracy. Retention/buildability does not require a
  performance improvement; an inconclusive hypothesis is not automatically discarded.
- `src/vibesys/orchestration/dynamic/search/_main.py:MainLine.options` offered
  only the latest verified candidate of each hypothesis that beat main's headline.
  A planner explicitly schedules a merge; promotion alone does not move main.
- `MainLine.finished` advances main only after a merger's retained commit passes
  accuracy and its measured result does not regress or lose a passed benchmark
  check. The regression guard also used only the headline at the base commit.
- `src/vibesys/candidates/_run.py:choose_winner` chose among eligible retained
  settlements by the first metric. Dynamic search's `RunRecord.eligible_rounds`
  includes eligible merged mains and eligible individual candidates.
- `src/vibesys/metrics.py:MetricSpace.compare` used a directed scalar difference
  with tolerance `abs(baseline) * relative_noise`. Secondary objectives never
  broke a tie in these dynamic checks. `MetricSpace.dominates` already had the
  correct multi-axis tolerance rule, but those paths did not use it.

Core's statistical comparison is a separate mechanism:

- `libs/vs-core/src/vs_core/_comparison.py:sample_of` stores one headline `Reading`
  per sample. `compare` computes candidate mean / base mean on that metric.
- A declared noise floor uses pooled relative spread, floored by the declaration,
  and two standard errors for a verdict. Without a declared floor, Welch's
  statistic requires at least three samples on each side and nonzero observed noise.
- `as_answer` aggregates the headline over samples; other metrics remain the
  answering evaluation's own readings.
- `libs/vs-core/src/vs_core/_base_runs.py:_logged` records changed comparisons.
  These `measurement_compared` events did not decide dynamic promotion: the
  strategy separately applied its scalar `MetricSpace` check.

## 2. What happened to 0bf2a8a6

The observed durable state had not discarded or finally settled this hypothesis.

- `measurement_compared`, event sequence **1855**, names candidate
  `0bf2a8a6a1b3faf3c595494a3b082abb453447b0` against
  `9b80582f71896ff2f374608548752cb0880631e7`.
- TPOT: **50.36629032258063 vs 49.62709677419356 ms**;
  ratio **1.014894958529419**, one sample per side, noise `0.0`,
  verdict **inconclusive**. It is the sole comparison event for this candidate,
  not the sole comparison event in the entire run.
- `receipts/executions/8b27e031ca48f9def641bb2395d46802135741a08f5a0d97ada533978d57564f.execution.json`
  records the implementer's resumed reply: trusted accuracy PASS, benchmark PASS,
  two requests completed, zero timeouts, TTFT **18744.17 ms**, TPOT **50.3663 ms**.
  Accuracy evidence ID:
  `279c47763411ef534857e5b4780ddda4bbd2298931ee72648acf34b562a6c98e`.
  Benchmark evidence ID:
  `1dee664ef1d4a2daad484b86bc390b4f334bcc99f503ee68c04b095beda4dcca`.
- The agent subsequently retained
  `dfe27553d1d63951c8504472c749d65076a6b8fe`. The recorded diff is an
  evidence report; the receipt reports no implementation difference from 0bf2a8a6.
  Nevertheless, it is another exact revision and cannot inherit measured facts silently.
- Its workstream checkpoint was `implemented`, with no official measurement or
  closing; the judge had yielded on another evaluation. Its hypothesis had no
  finished rounds. Thus no final promotion decision had been made.
- Main was still `9b80582f...`, generation zero, with no merges.
- Planner prompts, including event sequence **3571**, reported no mergeable
  candidate. The combinable list then offered tiled-prefill and stream-overlap
  revisions, not 0bf2a8a6. This was an in-flight/catalog issue, not a Pareto verdict.
  `implemented_revisions` indexes submitted candidates, finished rounds, and
  verified parents; arbitrary older agent checkpoints were not included.
- A later planner explicitly scheduled integration using 0bf2a8a6's diff in
  an implement task after operator steering. That does not mean the framework
  had promoted it, offered it as a formal combine part, or moved main.

The copied run's own input baseline stores TTFT **29796.985 ms**. That exact
comparison gives about **37.1%** improvement; the brief's approximately 30.6 s
reference gives approximately **39%**. Both are well beyond the migrated tolerance.
The migration preserves the recorded numbers rather than replacing them.

## 3. Intended semantics, fix, and compatibility

The old running guide explicitly described headline-only checks. Other contracts
and documentation describe all declared objective axes as ranking axes:
`docs/contributing/wire-protocol.md` (`objective.objectives`),
`docs/contributing/openevolve.md` (multi-objective frontier), and
`MetricSpace.dominates`. An ordered list was not documented as a lexicographic
priority list. The headline reduction contradicted the requested multi-objective
semantics and was a bug.

The fix centralizes full-row comparisons in `MetricSpace.compare_metrics` and
`no_worse_metrics`:

1. Resolve every declared metric by name and use the objective declaration's direction.
2. Apply each axis's effective relative tolerance to its own baseline value.
3. Improvement requires at least one gain beyond tolerance and no regression beyond tolerance.
4. Missing axes and material tradeoffs do not establish dominance.
5. A merged result cannot hide a regression behind a gain on another axis.
6. Shared winner selection uses the non-dominated complete rows, with earlier
   sequence as its deterministic selection among alternatives.

Dynamic promotion, merge offers, merge safety, and the shared winner are fixed.
The sweep also found the same scalar input gate in `serial_search._closing`;
that gate now uses the same row policy. Informational combine-versus-best-part
summaries and original core statistical events remain headline diagnostics.
The running guide and planner merge wording now explain the full-row rule.

### Explicit migration required for this run's intended tolerance

The original task file has three objectives, in order:
`tpot_ms_avg:min`, `ttft_ms_avg:min`, `completed_requests:max`.
It has **no `[pareto]` table**, and the durable `TaskTerms.relative_noise` is
**0.0**. A standing prose instruction saying about 5% did not configure policy.
With zero tolerance the reported TPOT increase is a material regression, even
under correct Pareto comparison. The operator explicitly authorized a declared
5% floor with calibration provenance, rather than a hidden default.

Chosen path: a typed, versioned **objective-policy migration**, preserving old receipts.

- `proto/vibesys/core/v1/leaves.proto:ObjectiveNoise` is the authoritative
  validated record: schema version, revision, floor, effective relative noise,
  provenance, and typed baseline observations. `TaskObjective.noise` is optional
  and omitted when absent so old accepted decision encodings remain unchanged.
- `src/vibesys/dynamic_migration.py:migrate_objective_policy` is the pure transform.
  It prefers repeated baseline readings already held in the receipt ledger.
  Otherwise it uses supplied operator calibration. Its estimator is sample
  standard deviation divided by absolute mean, floored by the explicit declaration.
- Calibration supplied by the operator: TPOT **51.88 and 49.35 ms**;
  TTFT **33800 and 30300 ms**. The copied run held only one independent input
  sample at capture, so these supplied repeated observations were used.
- Effective tolerances: TPOT **5%**, TTFT **7.721914927%** (spread exceeds the
  floor), completed requests **5%**. These values and their justification are
  stored per objective. Applying a revised declared policy increments its revision.
- The migration rebuilds verified parents from exact checkpoint revisions with
  trusted accuracy and benchmark receipts. It does not transfer facts to a child,
  synthesize a settlement, advance main, or remove a queued evaluation.
- `scripts/migrate_dynamic_objectives.py` validates source resume authority,
  saves the original typed runtime record in `objective-policy-migrations/`,
  commits the new record with storage CAS and fencing, and runs the actual
  receipt-validating resume resolver again. An active writer is rejected.

### Verified on the live-state copy

The source was copied into temporary portable state and a separate temporary
`VIBESYS_STATE_HOME`; all machine-local receipts were copied there too.
The actual `resolve_core_resume` passed before and after migration, preserving
**162 accepted decision receipts** in the captured source. A subsequent pure
flow update exercised changed-code replay/restart: the queue was identical and
0bf2a8a6 remained in the resulting parent catalog.

On the migrated copy, 0bf2a8a6 is:

- a trusted, offered **build-on parent**;
- a formal **combine part**;
- **better** by full-row Pareto comparison with the input;
- **mergeable** under the revised per-objective policy.

TPOT is within tolerance; TTFT is better beyond tolerance; completion count ties.
The original `measurement_compared` event remains inconclusive as historical
statistical telemetry. The new policy verdict is derived from the original
per-objective evidence; no receipt is recolored or rewritten.

The code remains able to decode old state. To obtain the requested classification
and recover older in-flight checkpoints, this run needs the explicit migration
on its backup/copy before the one combined-code restart. Replacing code alone
would preserve the original zero tolerance and would not achieve that result.

A repeatable operator invocation, after backing up and stopping the run, is:

```sh
VIBESYS_STATE_HOME="$COPY_STATE_HOME" uv run python -m scripts.migrate_dynamic_objectives \
  --project "$COPY_PROJECT" --run-id "$RUN_ID" --floor 0.05 \
  --calibration "$CALIBRATION_JSON" \
  --provenance 'Operator repeated base calibration: TPOT 51.88 vs 49.35 ms; TTFT 33.8 vs 30.3 s. Revise with later baseline evidence.'
```

The calibration file maps objective names to arrays in their recorded units:

```json
{"tpot_ms_avg":[51.88,49.35],"ttft_ms_avg":[33800.0,30300.0]}
```

The migration itself launches no execution resources. The real live run has not
been migrated, stopped, resumed, or otherwise changed by this investigation.

## 4. Can planners and implementers see TTFT?

Yes, the evidence contains all metrics; automatic comparison telemetry is scalar.

- `libs/vs-runtime/src/vs_runtime/_polling_evaluation.py` records every benchmark
  row metric. `libs/vs-evaluation/src/vs_evaluation/agent_models.py:EvaluationStageOutcome.of`
  includes `metrics=evidence.metrics` in both evaluation tool results and resumed snapshots.
  The cited implementer receipt explicitly includes TTFT 18744.17 ms.
- `libs/vs-runtime/src/vs_runtime/_evidence_operations.py:reading_mapping` decodes
  every metric into the strategy reading. `round_of` preserves the entire row;
  `_context._history_row` passes it to the planner. `portfolio.j2` loops over all
  input, main, merge, profile, and completed candidate metrics.
- Pending candidates have no finished-round metric row in planner history. That
  explains the missing candidate numbers in this run's structured planning row.
- Mergeable/combine-source summaries and combine-versus-best-part verdicts are
  headline-only summaries. They are not the complete evidence or promotion rule.
- A separate presentation issue: the polling executor sets a direction only on
  the headline, and `reading_mapping` defaults missing directions to `max`.
  The captured planner baseline therefore labeled TTFT `better: max`, although
  the task says `min`. The fix uses the task's declared directions for policy,
  so that legacy display default cannot reverse TTFT optimization.

## Design and verification

Owner: `MetricSpace` owns the pure comparison; candidate/dynamic policy consumes it.
The migration owns only explicit policy addition and evidence reprojection. I/O
remains in its thin script using `Project`, `StateStore`, and the receipt ledger.
The alternative of extending scalar headline selection would retain the defect.
No upward dependency was added. The new migration module has a declared interface
and graph edges. No new I/O implementation or private-library import was needed.

Enforcement is through public-API property regressions: the reported case is pinned
with `@example`, alongside generated objective directions, metric orderings,
noise boundaries, tradeoffs, missing axes, retained-parent/merge offers, winner
selection, per-axis calibration, policy revisions, and preservation of queue,
history, evidence and receipts. The two original policy regressions fail against
993d63fed and pass with the fix. A migration property uses the owning library's
Fake evidence ledger; the captured real-state copy additionally exercises R1.

Validation results are recorded below.

### Validation record

- `./scripts/check_format.sh` and `./scripts/check_lint.sh`: passed.
- Original policy/property/prompt affected selection: **930 passed, 1 skipped**;
  the skip was an empty parameter set in an existing prompt-template test.
- Migration/restart/core-value/preflight selection after the migration additions:
  **148 passed**. Final combined product, migration, preflight, proto-value,
  schema, and process-lifetime selection: **893 passed**, using `-n 2`.
- Proto runtime and code-generator tests: **778 passed**, using `-n 2`.
- New policy properties were run against the base source: **both failed** on the
  pinned secondary-objective improvement and hidden-regression cases. They pass
  at the fixed head.
- Changed-file `ty` checks, test isolation, persisted-schema compatibility,
  documentation links/citations, graph-document freshness, proto layer checks,
  Buf lint/additive compatibility, and generated Go core-package compilation passed.
- Full `check_tach` still reports pre-existing graph/interface failures involving
  `vs_sim.host_probe`, `HostContention`, `run_benchmark_objectives`, and scripted
  agent dependencies. The same failures were reproduced at 993d63fed; this change
  introduces no new reported graph violation.
- `check_file_length` reports the unchanged `vs_project/_state.py` at **2001**
  lines; it is also 2001 lines at 993d63fed. It was not modified.
- The full repository `ty` run reports existing errors in receipt-integrity test
  helper typing, platform-specific signal/process APIs, and other untouched test
  files. Changed files pass targeted type checking.
- The public-value roundtrip suite exposed missing old fixtures for
  `EvaluationLease` and `EvaluationTiming`; those fixtures were completed while
  adding fixtures for the two new proto-backed policy values.

The sanitized machine-readable copy verification is
`.agent-logs/resume-verification.json`. It records byte-identical receipt files,
unchanged core evaluation/history/queue, the successful preflight, the successful
changed-code replay, and the per-axis/aggregate availability verdicts.
