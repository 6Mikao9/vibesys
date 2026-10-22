## Evidence-led optimization method

Choose from measured end-to-end evidence, not technique popularity. Quantify
the reference gap and each mechanism's defensible gain. While the gap exceeds
2x, prefer material bottlenecks or needed prerequisites over single-digit tweaks.

The optimization floor is hardware-specific. Read
`references/platforms/<backend>/floor.md` before selecting a mechanism. On
`cuda` and `rocm`, check continuous batching, fused attention, and graph capture;
on `trainium`, `metal`, and `cpu`, follow that platform's floor instead. Skip a
floor item only for a stated objective incompatibility, not because another
profiled cost is currently larger.

{% if workspace_sources %}
Treat the pinned checkout(s)—{% for source in workspace_sources %}`{{ source.dest }}/` ({{ source.name }}){% if not loop.last %}, {% endif %}{% endfor %}—as the implementation starting point. Direct the implementer to inspect and adapt relevant paths; require concrete evidence before replacing seeded components.
{% endif %}

On the first planner turn, before any measurement, state a hardware limit for
each scored metric. A bound does not wait for a benchmark or a profile. Read
rates from `serving-systems/references/platforms/<backend>/hardware.md` and the
floor from `references/platforms/<backend>/floor.md`. Do not copy a candidate's
speed into a bound.

Decode bytes per token are the active parameters times the bits per parameter,
counting only the experts one token activates, plus the embedding rows read and
the KV bytes of the context, for the batch in question. At batch 1 the decode
bound is memory bandwidth divided by those bytes. A wider batch reuses weights,
so the throughput bound rises until compute or KV traffic dominates; say which
regime the range assumes. Bound prefill by weight reuse and compute at the
benchmark's prompt lengths. TTFT is queueing plus prefill, not the decode bound.

Keep the current-architecture ceiling separate when the present scheduler,
batching, or kernels cannot reach the hardware limit. That ceiling is a target
for a design change. It is not a substitute for the hardware limit. Translate
terminal throughput into required step time and useful active batch; queued
concurrency is not useful model work.

After a trusted baseline, after an architecture change, and on a plateau, use
`serving-systems/references/tooling/performance-modeling.md` to calibrate a
prediction of the current design against end-to-end wall time and an
observer-controlled profile. That prediction needs measurement. A missing
benchmark is not a reason to leave the bound unwritten. Report each trusted
measurement as a fraction of its bound. Revise a bound only with independent
evidence: a measured hardware rate, a byte count from the code, a
microbenchmark, or a profile. A measurement that beats the bound means the
bound is wrong. A plateau far below the bound is a reason to check the bound,
then the code.

Attribute before optimizing. Compare throughput on isolated requests with
throughput under the benchmark's real load. A gap is a cost that appears only
under load: batch composition, scheduler, host input preparation, garbage
collection, pipeline-parallel bubbles, or long-context kernels. Assign an
experiment to the largest unattributed gap before any micro-optimization.
Size each candidate in ms per step against the measurement noise; a change
smaller than the noise cannot be confirmed alone, so batch such changes into
one experiment. When the parts of one serving design (a scheduler, a
prefix cache with exact resume, stage kernels) each measure within noise or
worse alone, do not drop them: if the run can combine retained candidates,
combine those parts into one candidate and measure them together.

Require production-path activation tied to the claimed removed operation,
frequency, bytes/launches, or boundary. Telemetry in token/layer/request loops
must not add synchronization or large rescans. A KV-layout change that still
reconstructs dense logical KV before attention is not paged-attention compute.

Streaming is part of the measurement contract: preserve model-token accounting
and one logical delta record per generated model token even when writes are
coalesced. Live exact cohorts may share contemporaneous model work; completed
output/token replay for later arrivals is model bypass, not engine work.
