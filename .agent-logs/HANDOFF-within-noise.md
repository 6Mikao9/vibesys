# Diagnosis: why `within_noise`, base_samples 1, and no fast-forward

Evidence: read-only copy of the live run's core store (decoded to /tmp, removed at the end).
Code: 166d3a0b2.

## 1. Which code produced `within_noise`

`vs_core._comparison.compare` (libs/vs-core/src/vs_core/_comparison.py), not the strategy.

- It compares **one metric**: the headline (`tpot_ms_avg`). A `Sample` holds only the headline
  reading (`as_answer` docstring: "a sample holds the headline reading only"), so TTFT and
  `completed_requests` never enter a `Comparison`. TTFT's 44% gain is invisible to it.
- Noise came from a *declared* floor, not a default: the store's task terms carry
  `objectives[].noise` (tpot 0.05, ttft 0.0772, completed_requests 0.05, provenance
  "operator calibration", from the objective-policy migration), although `objectives.toml`
  has no `noise` field. `ComparisonPolicy.floor_of(tpot) = 0.05`.
- Formula (floor branch): `sigma = max(pooled, 0.05)`; `noise = r * sigma * sqrt(1/1 + 1/1)`
  = 0.0700 (matches the stored `noise: 0.0700`); critical = `VERDICT_NOISES` = 2, so a
  verdict needs |gain| > 14%. TPOT gain was 1.0% and 2.9%, so `within_noise`.
- `within_noise` is a *close call* (`close_call`): it makes core take more base and candidate
  runs (`remeasures_base`, `remeasures_candidate`) and the agent reads it as "not better".
  Nothing in the comparison says "better on another objective".

Two incompatible noise definitions live in one task:
- vs-core: 2 x sqrt(1/m+1/n) x max(pooled, floor) of the ratio (14% at 1/1 samples).
- product `MetricSpace.compare` (src/vibesys/metrics.py): flat `abs(base) * relative_noise`
  (5% for TPOT, 7.7% for TTFT), no sample-count scaling, no critical multiplier.
  The product space already judges the two candidates as better (TTFT 43% > 7.7%, TPOT within).

## 2. base_samples 1: yes, identity hashed the declaration

`GitTracker.measured_input` (libs/vs-project/src/vs_project/_git_tracker.py:393) hashes
`json.dumps(withheld)` (the sorted declaration) into the digest as well as the patch with the
paths excluded. Adding `unread = ["evidence/"]` changed `[]` -> `["evidence"]`, so the same base
commit `9b80582f` (no `evidence/` in its tree, identical patch) moved from
`sha256:378e2613...` to `sha256:9c6ab973...`.

Run data, samples of commit `9b80582f`:
- `378e...`: 3 trusted samples (49.63, 53.50, 53.59 ms)
- `9c6a...`: 1 sample (53.39 ms); every comparison after the declaration anchors here.

`base_samples` = samples with the anchor's `measured_input`, so 3 became 1. The declaration is
a *request to withhold*; the identity should be the content the evaluator receives. The patch
with the excludes already is that content, so the declaration line is redundant. A test
(`test_another_unread_declaration_is_another_measured_input`) pins the wrong behavior.

## 3. Why no fast-forward

- `MainLine.fast_forward` (src/vibesys/orchestration/dynamic/search/_main.py:213) runs only
  when a workstream closes (`WorkstreamDone`, `_run_state.py:1118`). Candidates `118529fe`
  and `626187c1` are checkpoints of attempt 49 (`dense-prefill-coverage-checkpoint`), which was
  still `phase: active`, `closure: null`, listed in `running`, and had no settlement. Main
  (`9b80582f`) was unchanged, `merges: []`. A measured checkpoint never advances main.
- Conditions at close: parent == main, settlement eligible and retained, accuracy and
  benchmark passed, and `_beats` (`MetricSpace.compare_metrics(...) is BETTER`). `_beats`
  would pass for these readings (TTFT better beyond 7.7%, TPOT within 5%), so the verdict
  was not the blocker; the close had not happened.
- The agent saw the vs-core `within_noise` on TPOT (1 sample a side) and no verdict at all for
  TTFT, so it had no signal that it had already dominated the base. That is the part the
  comparison mechanism owns.

## 4. Sweep: where a multi-objective result collapses to one label

Headline-only (never sees the other objectives):
- vs-core `compare`, `Comparison` (one metric), `comparisons_of`, `close_call`,
  `remeasures_base`, `remeasures_candidate`, `verdict_unreachable`,
  `comparison_settling`, `effect_verdict`/`effect_resolution` (the planner's `EffectVerdict`,
  `serial_search/_plan_view.py`), `MEASUREMENT_COMPARED` event (`run/integration.py`),
  server `ComparisonVerdict`.
- `candidates/_winner.improves_on` (`space.compare` on one `Measurement`) and
  `dynamic/search/_context.versus_parts` (`space.compare(metrics[0], best_part)`).

Multi-objective but collapsing (`MetricSpace.compare_metrics`): `{BETTER, WORSE}` ->
`INCOMPARABLE`; `{BETTER, WITHIN_NOISE}` -> `BETTER`; consumers read `is BETTER`:
`beats_input` (`_outcomes.py:462`), `_beats` (`_main.py:392`; fast-forward and merge
eligibility `MERGE_CANDIDATE_NOT_BETTER`), `_regresses`, `serial_search/_closing.py:73`.
`no_worse_metrics` and `MetricSpace.dominates` implement the same rule a second and third time.

So there are three dominance implementations (`compare_metrics`, `dominates`,
`no_worse_metrics`) over `MetricSpace`, and a vs-core single-metric statistic that neither
shares tolerance with nor sees other objectives.

## Fix plan (this branch)

1. Identity: `measured_input` hashes the evaluated content only (root + patch with excludes).
   A constant `[]` line keeps digests recorded before the change (declaration empty or
   withholding nothing) byte-identical; `378e...` is reproduced for the base commit.
2. One multi-objective comparison in vs-core: per objective `better | worse | same`, and
   `dominates` = no objective worse and at least one better. Used by vs-core's verdict and by
   the product's `MetricSpace` decisions, so there is one rule.
