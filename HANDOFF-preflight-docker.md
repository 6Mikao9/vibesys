# Metal preflight and dynamic concurrency

## Result

Metal on macOS already launches the run's agents and candidates on the host.
The `uses_docker: true` preflight field was incorrect. No execution-placement
change is needed. The fix is `cef9d357f` on `exp/flint-preflight-docker`.

Preflight command:

```sh
uv run vibesys preflight \
  --project examples/model-serving/repositories/lithos-metal --task flint-m3pro \
  --outer-loop dynamic --backend metal --local \
  --cli-provider codex --model gpt-6.1-sol
```

Before: valid settings, Metal, `uses_docker: true`, cached agent image.
After: valid settings, Metal, `uses_docker: false`, agent image check says
`not needed: this run's environment starts no Docker container`.

## Root cause and design

`src/vibesys/api/request.py:run_environment_uses_docker` built the requested
spec directly. The requested default is Docker; the launcher resolves that
spec against the backend and platform first. A host-only backend on macOS
resolves to `HostEnvironment`, whose `uses_docker` fact is false.

The helper now takes `RunRequest` and uses the existing `_run_environment_of`
resolver, shared with the host-shortfall check. Both preflight branches and
per-container login validation consume that resolved fact. No new backend-name
branch, module, dependency edge, confinement rule or execution option is added.
The alternative of resolving only in preflight would leave login validation
with the same stale default; resolving at the fact's owner covers both.

The production launch trace is unchanged: resource assembly resolves the spec;
`HostEnvironment` makes local command runners; the workspace factory reuses
that environment for candidate worktrees; the provider client receives
`use_docker=False` and `require_host_sandbox=True`. The CLI integration uses
host Seatbelt confinement, rather than a separate provider container.

The sweep also checked reader resolution, task-image selection, provider
client construction, candidate acquisition, and profiler validation. Profiler
validation reads the requested adapter, but host and Docker expose the same
profiler defaults/support facts; no additional behavior difference was found.

## Verification

The new public preflight regression fails before the product change at
`assert report.uses_docker is False` (actual: true), and passes afterward.
A property test pins Metal with the default selection and varies every compute
backend and absent, Docker, host and Modal selections. The tests use the CLI
and request APIs and the existing scripted command probe; no patching or real
processes are added to deterministic tests.

- `./scripts/check_format.sh`: pass, 2,707 files formatted.
- `./scripts/check_lint.sh`: pass.
- `uv run tach check`: pass, all modules validated.
- Targeted tests: **112 passed**, 2 existing pytest rewrite warnings, 5.39 s.
  Paths: `tests/entrypoints/test_cli_run_options.py`,
  `tests/entrypoints/test_preflight_causes.py`,
  `tests/vibesys/api/test_request.py`,
  `libs/vs-runtime/tests/test_host_environment.py`, and
  `libs/vs-runtime/tests/test_workspace_resource_factory.py`.
- `uv run python -m scripts.check_test_isolation`: within recorded baseline.
- `tests/quality/test_real_apis_confined.py`: **165 passed**, 2 existing
  pytest rewrite warnings, 14.48 s.

## Scripted host run

Run ID: `20261021-225715-81fb425d-smoke-project2-20261021-225715`.
Completed on 2026-10-21 at 22:57:38 UTC with
`succeeded=True`, `status=completed`, `failure=None`.

The CLI-built request used `--outer-loop dynamic --backend metal --local
--agent-backend scripted --profiler none --no-skills --max-rounds 2
--max-in-flight 2 --evaluation-slots 4 --max-run-seconds 180`.
The production launcher, backend, host environment, Git worktrees, state
stores and trusted command processes were real. Only the agent client factory
provided the existing `VALUE_WORKLOAD` scripted responders. The evaluator was
the tiny `queue.py` accuracy/protocol-v2 benchmark from the dynamic E2E harness.
No model, weight pack, Flint benchmark or GPU kernel ran.

A barrier held the first two implementer turns until both entered their
separate candidate workspaces. The entry log proves this sequence:

```text
coding_start candidate A active=1
coding_start candidate B active=2
coding_end   candidate B active=2
coding_end   candidate A active=1
```

A shared nonblocking OS file lock wrapped every accuracy and benchmark
process. Overlap would fail immediately and record `OVERLAP`. All eight
trusted stages alternated start/end with no overlap, despite four evaluation
slots. Root/input and candidate evaluations were both covered. The run's
summary was:

```text
PROOF coding_peak=2 evaluation_peak=1 docker_invocations=0 stages=8
```

The agent factory also asserted `use_docker=False` and
`require_host_sandbox=True`. A Docker executable placed first on the run's
PATH recorded and refused any invocation; it was never called.
`docker ps` before and after contained exactly the same five pre-existing
containers. No new container was started or left behind. No smoke process
remained after completion.

The existing macOS `os.waitid` watcher-thread errors appeared in unchanged
`vs_sim` process code, as in the earlier dynamic-host handoff; the run still
completed and its command processes exited. The scripted scenario deliberately
includes lost agent turns, which the dynamic loop handled. This evidence
validates orchestration and execution placement, not actual Codex memory use,
provider execution or Flint performance. Host confinement's guarantees remain
those documented in the earlier handoff and confinement tests.

Runnable script and raw evidence are local, gitignored, under
`.codex/preflight-docker/`: `smoke.py`, `smoke.log`, `smoke-events.jsonl`,
`coding.json`, `evaluations.jsonl`, `docker-before.log`, `docker-after.log`,
`regression-base.log`, `preflight-head.json` and check logs.

## Recommended experiment launch

```sh
uv run vibesys \
  --project examples/model-serving/repositories/lithos-metal --task flint-m3pro \
  --outer-loop dynamic --backend metal --local \
  --cli-provider codex --model gpt-6.1-sol \
  --max-in-flight 2 --evaluation-slots 1
```

Drop `--max-in-flight 1`; serialization belongs to trusted evaluation, while
coding workstreams can run concurrently. Start with two coding workstreams on
this 12-core, 36 GB Mac. The approximately 23 GB served model leaves roughly
13 GB for macOS, concurrent agent CLIs and other processes; core count alone
is not a sound reason to use a wider search. Two is a conservative starting
recommendation, not a measured memory guarantee. Keep evaluation slots at one
so extra evaluations do not wait inside the executor while their deadlines
run. Host admission still enforces serialization if a larger value is used.

The task's evaluator already uses `lockf -k /tmp/flint-gpu.lock`. Do not put
another acquisition of that same lock around the whole launcher: its child
evaluator would then wait for the parent-held lock. Other GPU users must honor
the task's lock. No real experiment was launched by this task.

Integration is a fresh fetch and a plain merge into `exp/flint-lithos-metal`,
with no force push or main push. Final pushed SHAs are reported in chat.
