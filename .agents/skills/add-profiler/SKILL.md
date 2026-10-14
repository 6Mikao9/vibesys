---
name: add-profiler
description: Add a profiler the profiler agent can call (ncu, rocprof, headroom, a native CPU profiler) as a set of coordinated edits, with a completeness test that names any piece left out. Use when adding or changing a ProfilerKind or ProfilerDefinition, a bundled profiler MCP server under resources/profilers/, a profiler prompt under src/vibesys/prompts/shared/profilers/, or the profiler auto-selection policy.
metadata:
  kind: codebase
---

# Add a profiler

A profiler gives the profiler agent evidence-gathering tools through an MCP
server (a tool server the agent's CLI calls). A run picks one with
`--profiler`, or `auto` picks from the domain, the compute backend and what the
run environment can execute. A profiler is a
`src/vibesys/run/contracts.py:ProfilerKind` member; a `ProfilerDefinition` in
`src/vibesys/orchestration/profilers.py:PROFILER_DEFINITIONS` (domains,
backends, the executable it needs and where it runs, tools that need a GPU
driver); a server at `resources/profilers/<kind>/server.py`, staged into the
agent's workspace; and a prompt `profilers/<kind>.j2` in
`src/vibesys/prompts/shared/profilers/`. Directory, server and prompt names
derive from the kind ([extending-profilers.md](../../../docs/contributing/extending-profilers.md)).

## Builds on

- `software-design`: rule 10 (distrust external boundaries). An agent's tool
  call is untrusted input; failures come back as structured results.
- `testing`: rule 7. Test a tool server with synthesized calls, not agents.

## When this applies

- A new `ProfilerKind`, a changed `ProfilerDefinition`, or `auto` selection.
- `resources/profilers/` and `src/vibesys/prompts/shared/profilers/`.

## Steps

1. **Add the member and its definition; let the test list what is missing.**
   `tests/vibesys/orchestration/test_profiler_registry_complete.py` fails,
   naming the kind, until every member but `auto` and `none` has a definition,
   a server and a prompt, and every package and prompt has a definition.
2. **Declare compatibility on the definition**, and keep `auto` choices in
   `src/vibesys/orchestration/profilers.py:resolve_profiler_choice`, never in
   callers. `tests/vibesys/test_profilers.py:test_profiler_auto_resolution_exhaustive`
   covers every domain, backend and requested kind.
3. **Declare the executable and its site** (`ProfilerTool`: the agent's shell
   or the GPU workload) so run start refuses a profiler the environment cannot
   run (#2131, `tests/vibesys/run/test_profiler_runnable.py`). The agent image
   must carry it; see `change-container-images`.
4. **Build the server on `StrictFastMCP`**
   (`resources/profilers/_common/strict_arguments.py`) so every tool refuses an
   argument it does not declare (#2363, #2387);
   `tests/vibesys/loops/test_profiler_strict_arguments.py` checks every server.
5. **Withhold tools whose hardware is missing.** List tools that need a GPU
   driver in `driver_tools` and make `build_server` drop them when passed
   `withheld` (#2185, #2188). The completeness test checks both;
   `tests/vibesys/orchestration/test_profiler_tool_offer.py` checks when a run
   withholds them.
6. **Put a native CPU profiler's mechanism in `libs/vs-runtime`**, behind a
   stdlib-only module the server imports on the agent's interpreter (as
   `libs/vs-runtime/src/vs_runtime/cpu_profilers.py`), and wire its host check
   into `src/vibesys/run/profilers.py:native_profiler_preflight`. Unenforced:
   the preflight runs real host programs, so no deterministic test covers it.
7. **Write the prompt** with `write-agent-prompt`, using the variables in
   [extending-profilers.md](../../../docs/contributing/extending-profilers.md);
   `tests/architecture/test_prompt_resource_contract.py` renders every packaged prompt.

## Testing

- Call tools in process through `build_server`, as
  `tests/vibesys/loops/test_ncu_profiler.py` does; server tests live in
  `tests/vibesys/loops/`, never in the shipped `resources/profilers/`.
- A missing executable, a malformed report or a failed analyzer is a tool
  error, never a normal reply: `tests/vibesys/loops/test_profiler_failure_contracts.py`.
- Read workload-written JSON with
  `resources/profilers/_common/capture_runtime.py:read_json_file`, and add each
  new decoder to `tests/system/test_hostile_json.py` (#2410). Unenforced: that
  corpus lists decoders by hand.

## Known failure modes

- Offered where it cannot run: an `auto` profiler that did not fit the backend
  (#2130, `tests/vibesys/test_profilers.py`); driver tools offered in a GPU-less
  editor (#2185, #2188, `tests/vibesys/orchestration/test_profiler_tool_offer.py`).
- A misspelled optional argument silently replaced by its default, because
  FastMCP drops unknown keys (#2363, #2387,
  `tests/vibesys/loops/test_profiler_strict_arguments.py`).
- Profiler timings presented as the scored result; the prompt must keep the
  trusted benchmark separate. Unenforced: needs a reader of the prompt.

## Sources of truth

- `src/vibesys/orchestration/profilers.py`: definitions and `auto` policy.
- `src/vibesys/run/profilers.py`: binding to the run environment.
- [extending-profilers.md](../../../docs/contributing/extending-profilers.md).
