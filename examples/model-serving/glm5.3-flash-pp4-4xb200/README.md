# GLM-5.3-Flash NVFP4 PP4 4xB200 From-Scratch Input

Use:

- `--input examples/model-serving/glm5.3-flash-pp4-4xb200`
- `--interface service`, with an operator service that starts the
  candidate's `vibesys_serve.sh --port PORT` from the candidate root, a fresh
  server per evaluation, on 4 B200 GPUs in one NVLink domain

This input provides no serving engine: the candidate builds a prefill server
for `nvidia/GLM-5.3-Flash-NVFP4` (KDA linear attention, DSA sparse attention,
NVFP4 MoE) across 4 B200s from scratch, and writes the `vibesys_serve.sh`
that launches it. Kernel libraries are allowed (FlashInfer, DeepGEMM,
`deep_select`, `causal-conv1d`, TileLang, and vLLM's compiled ops and kernel
wrappers); vLLM's and SGLang's engine, scheduler, KV-cache manager, model
runner and API server are not, and the accuracy checker scans for them. It is
the from-scratch counterpart to the vLLM-repository task
`glm5.3-flash-pp4-b200-vllm`, with the same benchmark, checker, reference
docs and objectives.

The operator provides the large inputs, which are not in this repository:
the checkpoint at revision `da920bb0b9f4a06727223a349e55468e38352348`,
enwik9, the trace `sessions_2ps.csv`, the patched `session_runner`, and
`accuracy_checker/reference.json`. `.vibesys/tasks/default/README.md`
("Interface") lists the flags the operator appends to each command.
