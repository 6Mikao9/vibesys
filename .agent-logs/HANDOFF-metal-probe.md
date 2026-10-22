# Metal probe confinement

Branch: `fix/metal-probe-confinement`, based on origin
`demo/flint-restart-5` at `ce6b555ee6f0b078b58623d98c6357db9dd1931c`.
The base already contains the ancestor-directory metadata fix `3c9d2789d`.
First implementation commit: `1f1f8da9e`, within 40 minutes of starting.
No PR. No changes to the experiment bundle, candidate repository, live run,
its journal, prepared environments or shared tool caches.

## Evidence

Read-only source: `logs/core-events.jsonl` for
`20261022-125146-7b54ad05-lithos-metal-20261022-125146`.
The raw selected events are retained locally in
`.agent-logs/metal-probe/exact-denials.json`, ignored and uncommitted because
journal contents contain machine-local identifiers. Paths below use
`<DARWIN_CACHE>`, `<DARWIN_TEMP>`, `<UV_CACHE>` and `<PREPARED_ENV>`.

- Dynamic implementer, sequence 154, 12:54:16.140553 UTC:
  `error: failed to open file <UV_CACHE>/sdists-v9/.git: Operation not permitted (os error 1)`.
  Task setup attempted `uv sync --locked`, which needs to mutate a shared cache.
- Implementer, sequence 177, 12:54:48.251563 UTC:
  `error: Operation not permitted (os error 1) at path "<DARWIN_TEMP>/.tmpb3dSSy"`.
  It was using the prepared task interpreter. This scratch denial is unintended;
  it does not justify writes to the prepared interpreter itself.
- Implementer, sequence 182, 12:54:56.914227 UTC:
  `failed to create directory <PREPARED_ENV>/lib/python3.12/site-packages/iniconfig-2.3.1.dist-info: Operation not permitted (os error 1)`.
  This denial protects the environment used by trusted evaluations.
- Dynamic profiler, sequence 567, 13:02:19.766805 UTC, reads the failed warm-server
  probe log. It contains `sandbox_extension_issue_file failed` for
  `<DARWIN_CACHE>/com.apple.metalfe`, `<DARWIN_CACHE>/com.apple.gpuarchiver`,
  and `<PREPARED_ENV>/bin`, followed by:
  `MSL compile failed: error: unable to open output file '<DARWIN_CACHE>/com.apple.metalfe/1MZPGHAX6E610/monolithic_metal.pcm': 'Operation not permitted'`.
  The same log is read again at sequence 1175; these are repeated observations
  of one failed startup, not separate successful or failed measurements.
- Run-state denials appear for both roles: profiler sequences 129, 140, 170
  (`.vibesys/state/runs`, `.vibesys/state/local`) and implementer sequences
  152 and 1305. They are expected trust-boundary enforcement.
- Process inspection/control denials (`ps`, `pkill`, process cwd inspection)
  also occur. This fix does not grant those unrelated capabilities.

`/tmp/flint-profile-516a/result.json` reports revision `516a34cd0609befe186e4bbe3af7b2ce50d9fd76`,
prepared mode, a successful native build, failed startup during warmup
`compile_pipelines`, and zero measured requests. Its complete server scope was
under `lockf -k /tmp/flint-gpu.lock`.
`/tmp/flint-profile-47cf1983/status.json` reports a different blocked request:
no trusted measurement interface was exposed, no server started, and no GPU
work executed. That file does not establish a Metal compiler failure.

## Dependencies and scope

The complete compile/dispatch path needs the following capabilities:

1. Read executable/toolchain/framework/driver bytes and metadata, map signed
   code, fork/execute and create threads. The preceding ancestor-metadata fix
   remains necessary for task shell entry. Existing rules already provide these.
2. Writable Metal frontend module cache (`com.apple.metalfe`, including
   `monolithic_metal.pcm` and `modules.timestamp`); driver/shader cache
   (`com.apple.metal`, including device-specific function/library data and
   version-specific archive bookkeeping); and binary archive cache
   (`com.apple.gpuarchiver`). Bundle/version-specific nesting stays inside the
   same private cache namespace.
3. Temporary storage: `TMPDIR`, `TMP`, `TEMP` for tools such as uv, and libc's
   `confstr(_CS_DARWIN_USER_TEMP_DIR)` for native clients. The cache equivalent
   is `confstr(_CS_DARWIN_USER_CACHE_DIR)`. Bare environment variables named
   `DARWIN_USER_TEMP_DIR` or `DARWIN_USER_CACHE_DIR` do not override confstr.
   `TMPDIR` alone does not redirect Metal's caches.
4. Mach/XPC lookup for `com.apple.MTLCompilerService`, Darwin's
   `com.apple.bsd.dirhelper`, and GPU support services (for example
   `com.apple.gpumemd.source` and, depending on the driver, `com.apple.cvmsServ`).
   Existing unrestricted `mach-lookup`/`mach-per-user-lookup` cover these;
   no new Mach permission is added.
5. IOKit GPU user clients and shared-memory/surface support. This machine
   exposes `AGXAcceleratorG15X` / `AGXDeviceUserClient`; other drivers use
   IOAccelerator/IOGPU/IOSurface client variants. Existing `iokit-open` and
   `ipc-posix-shm` cover them. No new IOKit permission is added. The scalar
   dispatch verifies this machine's path, not every macOS driver variant.
6. Cache-scoped `file-issue-extension` for
   `com.apple.app-sandbox.read-write`: MTLCompilerService has its own
   confinement and receives extensions from Metal. The policy uses
   `require-all` with the extension class and `require-any` over only the two
   private directories. It grants no extension on the shared prepared
   interpreter's `bin` directory. In-memory source compilation needs no broad
   source-file extension grant.

Apple's libc [confstr implementation](https://github.com/apple-oss-distributions/Libc/blob/main/gen/confstr.c)
shows that cache/temp paths come from dirhelper. The installed
`com.apple.MTLCompilerService.sb` shows the separate service's temp and
extension permissions. Chromium's upstream
[GPU profile](https://github.com/chromium/chromium/blob/main/sandbox/policy/mac/gpu.sb)
independently identifies the three Metal caches, compiler XPC service and
GPU support clients. Actual redirection was checked against the installed
macOS libraries, rather than inferred from those sources alone.

## Implementation and design

Owner: `vs_sandbox.host_sandbox.SeatbeltSandbox` and its macOS builder.
Every host agent role uses this renderer; no concrete compute-backend branch,
new module, dependency edge, run-state schema, prompt or task change is added.

The builder discovers the unsuffixed temp/cache roots through bounded,
injectable `CommandProbe` calls to `getconf`. Unavailable, unsuccessful,
relative or root-directory responses fail closed with the offending key,
without a shared-cache fallback. It creates mode-0700 directories with a
stable suffix derived from the canonical workspace path and refuses symlink
namespace entries. `SeatbeltSandbox.env` injects that
`DIRHELPER_USER_DIR_SUFFIX` at process launch, and points all three temporary
environment variables into the suffixed temp directory. Native dirhelper and
Metal then choose separate per-workspace directories. Scratch/cache data
persists across turns as ordinary Darwin cache/temp data; it is not run state.

The pure renderer grants only these two namespaces and their read-write
extensions, after ancestor restrictions and before operator/project denies.
The namespace entries cannot be unlinked by the agent, preventing replacement
with symlinks during a subsequent launch. Children remain removable. Shared
per-user Metal caches receive no write grant, so the exact-subpath exception
for shared `com.apple.metalfe` is unnecessary.

Mechanism: filesystem confinement and inherited OS cache/temp discovery had
separate sources of truth. `/tmp` grants did not make Darwin scratch writable;
Metal selected a shared cache outside the writable view and could not provide
compiler-service extensions. The new environment and rendered grants derive
from the same workspace namespace. The sweep covered the only Seatbelt builder,
all host role launches, host run-environment selection, protected project state,
remote-job prefix construction, and the separate Linux/Docker mechanisms.
Only the macOS renderer/builder needs this cache mechanism.

Alternatives: broad `/var/folders` or per-user-cache writes would expose trusted
state and other sessions; `TMPDIR`/XDG-only redirection does not change confstr;
changing the candidate server's compilation flags would leave every other host
agent tool broken. Workspace-scoped dirhelper redirection hides that complexity
inside confinement. The public confinement API is unchanged except the optional
Darwin-root construction data on the existing Seatbelt implementation; callers
still use `env` and `wrap`. The builder owns filesystem discovery/preparation;
the renderer remains deterministic and process lifetime stays with its caller.

Shared uv/task-environment denials remain correct. Startup diagnostics now tell
the operator/agent to use a prepared environment, or put a separate environment
and `UV_CACHE_DIR` under `TMPDIR`. Never install packages into the prepared
interpreter. Missing/stale prepared environments require operator preparation
outside confinement. Run-state denials direct callers to run tools and supplied
artifacts instead of traversing `.vibesys/state`.

## Verification

- Deterministic regression exercises public `build_host_sandbox`, `env`, and
  `wrap` with `ScriptedProbe`, plus a filesystem-policy interpreter. It checks
  inherited-suffix replacement, temp variable agreement, writable private
  Metal/temp paths, denied shared caches/other sessions/trusted paths, distinct
  workspace namespaces, stable reuse, namespace-root protection, symlink
  refusal, and fail-closed Darwin discovery.
- Restoring production code to `ce6b555ee` makes the new regression fail on
  the unchanged inherited shared suffix. Head passes.
- Focused affected suites, at most two pytest workers: 130 passed, 13 platform
  skips. This includes vs-sandbox, vs-agent host launch, host-runtime and agent
  environment tests, and agent run-state confinement.
- Real Seatbelt control-boundary conformance: base and head each 3 passed,
  3 skipped (absent Docker/user-bus sockets, Linux-only abstract sockets).
  The separate generic resource conformance case fails at base and head:
  a declared read-only sibling resource is unreadable under ancestor blinding.
  This pre-existing failure is not repaired or hidden here.
- CPU-only real-profile checks: native/Python confstr temp/cache roots resolve
  to the private namespaces; Metal's own `MTLGetShaderCachePath` resolves under
  the private cache. `/var` versus `/private/var` spelling is canonicalized
  for comparisons. Private scratch writes succeed.
- Format, lint, test-isolation ratchet, docs citations/links and module-graph
  rendering check pass.
- Extra whole-repository file-length check fails on unchanged
  `vs_project/_state.py` at 2001 lines. Extra tach check reports pre-existing
  HostProbe/HostContention/evaluation/scripted-agent interface/dependency errors.
  Every named file and both graph/config definitions are byte-identical to base.

Real Metal base/head comparison on macOS 27.2 / Apple M3 Pro:
base reproduces the PCM permission denial and exits 3; head prints
`COMPILE_OK`, then `DISPATCH=42`, and exits 0. Exactly one command buffer
containing one GPU thread is submitted by this verification. Both executions
use the same tiny kernel and the live GPU lock. The head creates
`com.apple.metalfe/2G3PHWZUP0UW5/monolithic_metal.pcm`, `com.apple.metal` and
`com.apple.gpuarchiver` inside its private cache namespace. No shared Metal
cache write allowance is used.

One warning remains on head: an extension request for the executable/workspace
directory is denied. It does not prevent the in-memory compile or dispatch;
there is no reason to grant that broader read-write extension. The frontend
and archive-cache extension errors from base disappear.
A separate CPU-only probe confirms the empty private namespace root cannot
be removed (`PermissionError`) while writing a child succeeds.

Raw base/head SBPL, cache/temp environment keys, source, and result logs are
retained locally. Native probe source SHA-256:
`212b29eb9b1579522252b04e259213a8425965b5ca4b3794434047a4e707bb1b`.
Full process environments and credentials are not retained in these snapshots.

The probe is a standalone Objective-C executable using `newLibraryWithSource`,
MSL 4.0 / safe math, standard/tensor headers, and one scalar `uint` output.
The base invocation uses `--compile-only`, so it cannot dispatch even if a
warm compiler cache masks the failure. To obtain a reproducible cold-cache
failure without deleting or modifying shared caches, both builders receive
an inherited, dedicated dirhelper suffix. Its three empty Metal cache
subdirectories are prepared outside confinement. Without those directories,
Metal can skip the cache path and compile successfully, which is not a valid
reproduction of the reported existing-cache failure. The fixed builder must
replace the inherited suffix with its own workspace namespace.

With the base renderer, the cold frontend cache fails to create
`com.apple.metalfe/2G3PHWZUP0UW5/monolithic_metal.pcm` with `Operation not permitted`,
and the frontend/archive extension requests fail as in the journal. No GPU
command buffer is submitted by the base invocation. This varies the compiler's
module-cache hash from the reported `1MZPGHAX6E610` while exercising the same
failure mechanism. All invocations are under `lockf -k /tmp/flint-gpu.lock`;
lock contention is waited for with a bound, never bypassed. The lithos server
is never started. Local runner sources and raw logs remain under
`.agent-logs/metal-probe/`; they are not committed.

## Applying to the live run

This is not hot-reloaded into an already running VibeSys process or agent.
A newly launched framework process using the fix rebuilds agent confinement and
its environment; no persisted-state migration or fresh run ID is required by
this change. The existing run can pick it up on an operator-managed resume
once the separate SIGTERM-resume fix (R1) is integrated. Resume after SIGTERM is
currently broken independently; this branch does not claim to repair it.
A fresh launch using this fix is the alternative while R1 is unavailable.
Do not reinterpret failed profiler turns as measurements or rewrite journals.
