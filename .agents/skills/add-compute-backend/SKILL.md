---
name: add-compute-backend
description: Add a compute backend (an accelerator platform such as cuda, rocm, trainium, metal or cpu) as a set of coordinated edits, with completeness tests that name any piece left out. Use when adding or changing a ComputeBackend member, a backend implementation in libs/vs-sandbox (cuda_backend.py, rocm_backend.py, LocalBackend), backend prompt fragments under src/vibesys/prompts/backend/, or per-platform skill references under resources/skills/*/references/platforms/.
metadata:
  kind: codebase
---

# Add a compute backend

A compute backend is the accelerator platform a run targets (`--backend`). It
decides how the agent's command runner is built (image, device forwarding,
environment), what agents are told about devices and data types, which
profiler `auto` picks, and which platform guidance bundled skills show. The
enum is `libs/vs-sandbox/src/vs_sandbox/compute_backends.py:ComputeBackend`,
re-exported as `vibesys.constants.ComputeBackend`; CLI choices, skill-metadata
validation and platform pruning derive from it.

## Builds on

- `software-design`: rule 14. A backend is a `<Variant>Backend` implementing
  `ComputeBackendImpl`, and passes that interface's contract suite.
- `testing`: rule 3 (one contract suite for Fakes and real implementations).

## When this applies

- A new `ComputeBackend` member, or a change to how one builds its runner.
- `libs/vs-sandbox/src/vs_sandbox/compute_backends.py` and the per-backend
  modules beside it; `src/vibesys/prompts/backend/`;
  `resources/skills/*/references/platforms/`.

## Steps

1. **Add the member and register a factory; let the tests list what is
   missing.** Register it in
   `libs/vs-sandbox/src/vs_sandbox/compute_backends.py:_register_defaults`.
   `libs/vs-sandbox/tests/test_compute_backend_registry.py` fails, naming the
   backend, until it has a factory; `tests/vibesys/test_compute_backend_wiring.py`
   fails until the product tables in steps 6 and 7 cover it.
2. **Choose the implementation.** A platform with device logic gets
   `<name>_backend.py` and a `<Name>Backend` exported from `vs_sandbox.api`,
   reading devices through an injected `AcceleratorDiscovery`. One with no
   device to manage binds `LocalBackend`, like `metal_backend` and
   `cpu_backend` in `libs/vs-sandbox/src/vs_sandbox/local_compute_backend.py`.
   Add the class to the factories in
   `libs/vs-sandbox/tests/test_compute_backend_contract.py`. Unenforced: real
   constructors probe hardware, so no test builds every registered backend.
3. **Declare a host-only platform** (no container reaches its accelerator) with
   `host_only=True`; run-environment selection reads `backend_is_host_only`,
   never the name. `libs/vs-runtime/tests/test_host_environment.py` pins the set.
4. **Pick the default Docker image** (the module's `_DEFAULT_IMAGE`); see
   `change-container-images`. Unenforced: only running the image shows it
   carries the platform's toolchain.
5. **Write the prompt fragments**: `src/vibesys/prompts/backend/<backend>/` with
   every fragment name, and a `ComputeBackendFragment` subclass in
   `src/vibesys/prompts/renderer.py` (prose via `write-agent-prompt`; an empty
   file is a deliberate skip). `tests/vibesys/test_backend_fragments.py` fails
   for a backend missing either.
6. **Pick the profiler `auto` uses** in `default_profiler_for_backend` and allow
   the backend on its definition (`add-profiler`);
   `tests/vibesys/test_compute_backend_wiring.py` checks the pair. Extend the
   backend map in `tests/vibesys/test_profilers.py:test_profiler_auto_resolution_exhaustive`.
7. **Add platform guidance** under `references/platforms/<backend>/` in every
   bundled skill that has a `platforms` directory. An agent sees only its own
   backend's; `tests/vibesys/test_compute_backend_wiring.py` fails for a missing
   directory or one named after no backend, which pruning would never hide.
8. **Update the prose list** of backends in
   [skill-metadata.md](../../../docs/contributing/skill-metadata.md).
   Unenforced: a prose restatement of `ComputeBackend` that no check compares.

## Testing

- Run `libs/vs-sandbox/tests/test_compute_backend_contract.py`,
  `tests/vibesys/test_compute_backend_wiring.py`,
  `tests/vibesys/test_backend_fragments.py` and `tests/vibesys/test_profilers.py`.
- Test discovery with `FakeAcceleratorDiscovery`
  (`libs/vs-sandbox/tests/test_accelerator_discovery.py`) and forwarding as a
  property over device sets (`libs/vs-sandbox/tests/test_docker_device_groups.py`);
  a real device run belongs in a real tier.

## Known failure modes

- Device groups added to a container with no devices forwarded (#1587,
  `libs/vs-sandbox/tests/test_docker_device_groups.py`).
- An `auto` profiler that did not fit the backend (#2130,
  `tests/vibesys/test_profilers.py`).
- A branch on a backend's name outside the wiring code; declare a capability
  instead, as `backend_is_host_only` does. Unenforced: needs judgment.

## Sources of truth

- `libs/vs-sandbox/src/vs_sandbox/compute_backends.py`: enum, protocol, registry.
- `src/vibesys/prompts/renderer.py:ComputeBackendFragment`: fragment names.
- `src/vibesys/orchestration/skill_selection.py:foreign_platform_names`.
- [sandboxing.md](../../../docs/contributing/sandboxing.md): run environment and
  command runner vocabulary.
