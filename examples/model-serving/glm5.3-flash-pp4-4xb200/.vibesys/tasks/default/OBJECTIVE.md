# Objective - GLM-5.3-Flash NVFP4 prefill, pipeline-parallel on 4x B200 (from scratch)

Build a server for `nvidia/GLM-5.3-Flash-NVFP4` (revision `da920bb0b9f4a06727223a349e55468e38352348`)
and serve it on 4x NVIDIA B200 in one NVLink domain, on a
20-minute replay of coding-agent sessions at N = 500 concurrent sessions.
**First meet a 99th-percentile time to first token (TTFT) of at most 6 s, then
maximize the new prompt tokens per second.**

The workload is closed-loop, so throughput is nearly saturated before latency
is fixed: with zero TTFT the replay admits 41,444 rounds in the window, 120.3k
new tokens/s, and no server can exceed that. The known design below reaches
118.8k (98.8% of the ceiling). Latency is the lever: p99 TTFT is the primary
objective, and lower TTFT on long rounds is also where the last percent of
throughput comes from.

The four GPUs only run prefill: every request asks for one output token
(decode runs elsewhere in the modeled system).

**The repository starts empty** except `README.md` and `requirements.txt`. No
serving engine is provided. You write the whole server: checkpoint loading
and placement across the 4 GPUs, the model's forward pass, the KV cache and
linear-attention state, the prefix cache, the scheduler, the pipeline across
stages, and the OpenAI-compatible HTTP endpoint. You also write
`vibesys_serve.sh` at the repository root: the evaluation runs
`vibesys_serve.sh --port PORT` from the repository root and waits for
`GET /v1/models` before it sends anything. Until that script serves the model,
every evaluation fails.

## Workload

`benchmark/session_replay.py` replays `sessions_2ps.csv`
(`reference/1_contract.md` §2.2) with request-factory's `session_runner`:

- 2,457 coding-agent sessions arriving at 2 per second for 20 minutes, at
  most 500 active at once; a later session waits for a free slot.
- Closed loop: a session's next round is sent after the previous round's first
  token plus the trace's tool wait. Its prompt is the previous prompt, the real
  output token, then new tokens: 95% of a prompt is the session's own prefix.
  The rounds served in 20 minutes average about 74k prompt tokens, 3.5k new.
- Prompts are token IDs drawn from enwik9, sent to `/v1/completions` with
  streaming, `temperature=0`, `max_tokens=1`, `return_token_ids=true`, and
  usage reporting. Contexts reach 1M tokens.
- A fresh server per evaluation: every run starts with empty caches.
- Before the replay, a screening step sends a few lone 16k and 32k prompts and a
  session-shaped follow-up; it rejects a broken server, or one whose idle 16k
  TTFT already exceeds 60 s, in seconds. It also sends a 32,768-token prompt
  twice and requires the repeat to report cached tokens: the replay client runs
  that same check before it starts, so a prefix cache that resumes only at
  blocks longer than 32,768 tokens cannot be evaluated.

## Metrics

- `p99_ttft_s` (primary, minimize): p99 TTFT over the rounds whose first token
  arrives within 20 minutes of the first request. The goal is 6 s or less.
- `new_tokens_per_s` (second, maximize): the trace's new tokens of those
  rounds divided by 1,200 s. It counts work the workload adds, independent of
  the cache.
- `slo_met` (1 when p99 TTFT <= 6 s, else 0) and `slo_margin_s` (6 s minus
  p99 TTFT; negative when the SLO is missed).
- Reported for diagnosis: p50/p90/max TTFT, rounds/s, computed tokens/s,
  `prefix_hit_share`, rounds over 60 s, `fraction_of_sim_target` (against
  118.8k), `fraction_of_workload_ceiling`, and the screening TTFTs.

A run that misses the 6 s SLO is still scored, with `slo_met = 0`; compare
candidates by p99 first. A run is **rejected** (an error, not a score) when any
request fails, when any round comes up short of its prefix
(`prefix_shortfall_tokens > 0`, a missing or wrong output token id), when the
prefix cache does not work (screening), or when p99 TTFT exceeds 60 s. The run
stops early once one of these is certain (`benchmark/README.md`).

## Correctness

`accuracy_checker/checker.py` compares the greedy first token and top-10
logprobs against a reference build (vLLM TP4 with expert parallelism, same
checkpoint): lone prompts of 1k to 128k tokens, session rounds on a cached
prefix, and session rounds sent cold. Only near-ties of the reference may
flip; see `accuracy_checker/README.md`.

## Rules

- **No serving engine.** Do not import, run, copy, vendor or install the
  engine, scheduler, KV-cache manager, model runner, model code or API server
  of vLLM or SGLang, or TensorRT-LLM. That includes `vllm serve`,
  `python -m vllm.entrypoints...`, `vllm.v1.core`, `vllm.v1.worker`,
  `vllm.engine`, `vllm.model_executor.models`, `vllm.models.*` model files,
  `vllm.distributed`, `vllm.config`, and anything under `sglang`.
- **Kernel libraries are allowed**: FlashInfer, DeepGEMM (`deep_gemm`),
  `deep_select`, `causal_conv1d`, TileLang, Triton, CUTLASS/CuTe DSL, PyTorch,
  `sgl_kernel`, and vLLM's compiled ops and kernel wrappers: `vllm._custom_ops`,
  the `vllm._*_C` extensions, `vllm.kernels`, `vllm.vllm_flash_attn`,
  `vllm.third_party`, `vllm.triton_utils`, `vllm.tilelang_utils`,
  `vllm.cute_utils`, `vllm.scalar_type`, `vllm.utils.flashinfer`,
  `vllm.utils.deep_gemm`, `vllm.model_executor.layers.quantization.utils`, and
  any vLLM `ops` package (for example `vllm.v1.attention.ops`,
  `vllm.model_executor.layers.mamba.ops`, `vllm.models.glm5next.nvidia.ops`).
  The evaluation environment provides a vLLM install for these; do not install
  another. `transformers` and `safetensors` may be used for the tokenizer,
  config and weight files.
- The accuracy checker scans the repository for engine imports, launches,
  installs and vendored engine directories (`accuracy_checker/engine_scan.py`)
  and fails the check on any finding. The scan is not the whole rule: copied
  or renamed engine code is also disallowed.
- Do not change the workload, the benchmark, the accuracy checker or anything
  under `.vibesys/`. Do not detect the benchmark or special-case its prompts,
  sessions or request ids.
- The server must run the real model on every prompt: no canned or
  prompt-independent outputs, no skipped layers, no cached answers across
  evaluations.
- Keep the checkpoint and its NVFP4/BF16 weights as shipped, an FP8 KV cache
  for the sparse-attention layers, a maximum context of 1,048,576 tokens, and
  no MTP or speculative decoding.
- The server must listen on `127.0.0.1:<PORT>` from `vibesys_serve.sh --port PORT`
  and speak the vLLM-compatible subset of the OpenAI API that the task's
  clients use (`benchmark/session_replay.py`, `accuracy_checker/common.py`):
  - `GET /v1/models` lists `nvidia/GLM-5.3-Flash-NVFP4`;
  - `POST /v1/completions` takes a token-ID `prompt`, `max_tokens`,
    `temperature = 0` and `ignore_eos`;
  - streamed (`stream`, `stream_options.include_usage`, server-sent events
    ending in `data: [DONE]`) with the generated ids in each choice's
    `token_ids` when `return_token_ids` is set;
  - non-streamed with `logprobs = 10` and `return_tokens_as_token_ids`
    (tokens named `token_id:<id>` in `logprobs.tokens` and
    `logprobs.top_logprobs`);
  - `usage.prompt_tokens` and `usage.prompt_tokens_details.cached_tokens`,
    reported truthfully.
- Prefix-cache contents must come from this run: the evaluation starts a fresh
  server; do not persist or preload KV across server starts.

## What may change

Everything outside `.vibesys/`: the server's language and structure, the
parallel layout on the 4 GPUs, the layer partition, the scheduler, the KV-cache
and prefix-cache design (including CPU offload of prefix KV), CUDA graphs, and
kernels in Python, Triton, TileLang, CuTe DSL, CUDA (built or JIT-compiled by
your own code) or the allowed libraries above. `requirements.txt` lists what
the starting environment has; add what you need there and in
`vibesys_serve.sh`.

## A known-good design to beat

A simulator handoff (`reference/README.md`, frozen design `glm53_flash_pp4_b200_v1`)
serves this exact workload at **118.8k new tokens/s, TTFT p50/p90/p99
0.16/0.44/4.94 s**, at stage busy 92/88/91/91%. Its main points:

- **Layout.** PP4, whole layers per stage, split [12,11,11,11] (stage 0:
  embedding + dense layers 0-2; the slowest stage). One block pool shared by
  the pipeline, sized by the stages with 3 sparse-attention (DSA) layers: 61.3M
  tokens. Each request holds 6 blocks for its linear-attention (KDA) states.
- **Prefix cache.** Exact hits on the previous round's whole prompt, resuming the
  KDA state at its last token (no block-boundary recompute); whole-session LRU
  eviction; HBM holds the active contexts at 500 sessions.
- **Scheduler** (on stage 0, `reference/6_scheduler.md`): at most 4 microbatches
  in flight; a chunk's progress is committed when it is scheduled; started
  prompts shortest-remaining-first and queued prompts shortest-first; budget =
  min(load budget 4,096 tokens rising to 32,768 as the backlog grows from 1M to
  3M tokens, backlog / 4 clamped to [512, 32,768]); a request waiting 60 s goes
  first; whole footprint reserved at admission, no preemption.
- **Kernels** (`reference/README.md`, "Core kernels"): DeepGEMM `mega_mhc` and
  `fp8_mqa_logits`, FlashInfer trtllm sparse MLA over the FP8 cache,
  `recurrent_kda`, `trtllm_fp4_block_scale_moe` and `mm_fp4`, CUDA graphs with
  no host gaps inside a stage.

With a plain chunked-prefill scheduler and HBM-only cache, the simulator
overloads at 500 sessions (TTFT p99 22 s, `reference/5_prefix_cache.md` §4):
a first working server will likely miss the SLO (`slo_met = 0`) until
scheduling and the cache improve. `reference/` holds the per-stage, per-microbatch and scheduler targets
(`data/*.json`) for diagnosing where a candidate falls short. The targets are
not a ceiling: faster kernels or schedules are welcome.
