---
name: add-domain
description: Add an optimization domain (a problem space such as llm-serving, database or kernel-writing) as a set of coordinated edits, with a completeness test that names any piece left out. Use when adding or renaming a domain, adding a modality to one, changing DomainDefinition, the DOMAINS registry or the DomainName enum, or adding role prompt files under src/vibesys/domains/.
metadata:
  kind: codebase
---

# Add a domain

A domain tells the agents what kind of system they optimize and what "good"
means there; an input bundle picks one with `[agent].domain`. It is four pieces
that must agree: a `src/vibesys/constants.py:DomainName` member (the hyphenated
name bundles use), a package `src/vibesys/domains/<module>/` exporting
`DEFINITION` (a `src/vibesys/domains/base.py:DomainDefinition`), an entry in
`src/vibesys/domains/registry.py:DOMAINS`, and Markdown role files in the
package's `prompts/`. The CLI choices and skill-metadata validation derive from
`DomainName`.

## Builds on

- `software-design`: rule 8 (one source of truth). A fact about a domain goes
  on `DomainDefinition`, not into an `if domain is DomainName.X` branch in a caller.
- `testing`: completeness as a property over the enums and the tree.
- `write-agent-prompt`: the role files' prose.

## When this applies

- A new or renamed domain, a new modality, a new `DomainDefinition` field.
- `src/vibesys/domains/` and the `DomainName` enum.

## Steps

1. **Add the member, the package and the entry; let the test list what is
   missing.** Copy `src/vibesys/domains/generic/` (underscores where the name
   has hyphens), set `name`, and add it to `DOMAINS`.
   `tests/vibesys/orchestration/domains/test_domain_registry_complete.py` fails,
   naming the domain, until the pieces agree.
2. **Write role files only where the domain has something to say.** A role
   file is `<role>.md` for a `src/vibesys/domains/base.py:DomainRole`; a
   missing one injects nothing, and `single_agent.md` defaults to implementer
   plus judge. Modalities are declared in `modalities` and `default_modality`,
   with partials under `prompts/modality/<name>/`. The variables role files may
   branch on are in [domains.md](../../../docs/contributing/domains.md).
   `tests/vibesys/orchestration/domains/test_domain_registry_complete.py`
   rejects any other file name and any undeclared modality directory, which
   the renderer would silently ignore.
3. **Decide which profilers fit.** A domain in no `ProfilerDefinition.domains`
   (`src/vibesys/orchestration/profilers.py:PROFILER_DEFINITIONS`) gets only
   `none`. `tests/vibesys/test_profilers.py:test_profiler_auto_resolution_exhaustive`
   covers every domain; teach its expected-value helper any new `auto` rule.
4. **Record the prompt snapshots.**
   `tests/vibesys/orchestration/domains/test_prompt_snapshots.py` fails for a
   domain it has no hash for. Run it with `UPDATE_PROMPT_SNAPSHOTS=1`, read the
   rendered sections, then commit the hashes.
5. **Fence distinctive vocabulary** out of neutral packs with a `DomainLeakCheck`
   in `tests/vibesys/orchestration/domains/test_prompt_contracts.py:DOMAIN_LEAK_CHECKS`.
6. **Scope domain-only product skills** with a `domains` rule in their
   `.vibesys.toml` ([skill-metadata.md](../../../docs/contributing/skill-metadata.md),
   the `add-bundled-skill` skill);
   `tests/entrypoints/test_skills_wiring.py:test_all_repository_skill_metadata_is_valid`
   validates every sidecar.
7. **Update the prose lists** of domains in
   [domains.md](../../../docs/contributing/domains.md) and
   [skill-metadata.md](../../../docs/contributing/skill-metadata.md).
   Unenforced: prose restatements of `DomainName` that no check compares.

## Testing

- Run `tests/vibesys/orchestration/domains/` and `tests/vibesys/test_profilers.py`.
- `tests/vibesys/orchestration/domains/test_domain_sections_in_prompts.py`
  checks, for every `DomainName` and strategy, that each role prompt carries
  the domain's sections and no other domain's text.
- Render a role file's run-dependent branches both ways (a gate present only
  with a benchmark command), as `tests/vibesys/orchestration/domains/test_domain_packs.py` does.

## Known failure modes

- Domain text outside the pack: strategy templates that named a domain (#2141)
  and modality text kept in shared templates (#2146);
  `tests/vibesys/orchestration/domains/test_domain_sections_in_prompts.py`.
- A domain fact hard-coded in a caller: the CLI writes llm-serving's default
  modality in `src/entrypoints/cli/loops.py` although `default_modality` holds
  it. Unenforced: telling a policy branch on `DomainName` from a misplaced fact
  needs judgment.

## Sources of truth

- `src/vibesys/domains/base.py`, `src/vibesys/domains/registry.py` and
  `src/vibesys/domains/rendering.py`.
- [domains.md](../../../docs/contributing/domains.md): what a domain is, and
  the role-file variables.
