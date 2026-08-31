---
name: skill-maker
description: >-
  Use when the user asks to create a personal skill or change an existing personal skill's intended behavior or public interface in the shared skills directory. Do not use for behavior-preserving refactoring, blueprint regeneration, certificate work, or standards maintenance.
---


<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Executable Interfaces:

Call `famulus.invoke` with required `caller` (caller skill), `interface`, `version`, and `arguments`; optional `dry_run` defaults to false. Compact uses ordered `positionals` plus an option mapping; ordered raw argv uses `positionals: []` plus every argv token in list `options`. Never mix forms.
- `skill-maker._rtx.interface.sync-blueprints` — Validate every skill blueprint and either check or refresh generated SKILL.md interface blocks and the runtime-dependency manifest.
  - Caller: `skill-maker`
  - Version: 1
  - Alternative: `sync`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {}, "positionals": [], "stdin": null}
    Required options: []; positional arity: 0..0; stdin: forbidden
  - Alternative: `check`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--check": true}, "positionals": [], "stdin": null}
    Required options: ["--check"]; positional arity: 0..0; stdin: forbidden
- `standards.interface.query-standard` — Query one explicit standard and its complete pinned import closure.
  - Caller: `skill-maker`
  - Version: 1
  - Alternative: `standard-and-options`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--facts-json": "JSON", "--query-json": "JSON", "--refs-json": "JSON", "--repo-root": "PATH", "--view": "requirements|context|evidence|remedies|full"}, "positionals": ["standard-path"], "stdin": null}
    Required options: []; positional arity: 1..1; stdin: forbidden

<!-- END BLUEPRINT INTERFACES -->
## Research option when creating a skill

Before creating a skill, ask whether to research current documentation and
comparables unless the user already stated a preference. If yes, research
first; if no, use only the conversation and repository context.

## Git Safety

Before editing any skill file, verify the repo containing that file is on a
named branch (`git symbolic-ref HEAD` from the repo root). If it fails, check
out a named branch first.

## Standards retrieval

Before authoring, query `standards.interface.query-standard` with
`task.kind=author-skill` and `--view requirements`, selecting one canonical root
per component:

| Component being authored | Root standard |
|---|---|
| Parent skill module, including its module-facing gateway role | `references/node-standards/instruction-module.standard.yaml` |
| Registered instruction behavioral source, including the typical `<skill>.source.gateway` | `references/node-standards/instruction-behavioral-source.standard.yaml` |
| Python runtime module | `references/node-standards/python-module.standard.yaml` |
| Registered Python behavioral source | `references/node-standards/python-behavioral-source.standard.yaml` |

For a schema-minimum skill with `sources: {}`, query only instruction-module.
A typical registered `<skill>.source.gateway` requires both instruction roots.
A Python runtime child adds python-module; each registered Python source adds
python-behavioral-source. Query neither absent components nor inferred targets,
owners, or languages.

If selected work touches test files or their fixtures or helpers, query
`references/node-standards/code-testing.standard.yaml` as an additional
independent root with `task.kind=author-skill`. Set
`task.optimizes-test-performance` true for performance work and false otherwise.
Test artifacts are collected or executed by configured test or validation runner.
Markdown-only means no executable test file, fixture, or helper changes. Test
code that validates Markdown remains test code.

Each root returns its complete pinned import closure; never query imported
documents separately.
Follow up under the original component root with exact returned `document` and
`ref` pairs. Establish `task.affects-executable-behavior` from the proposal and
supply known `node.is-personal-override` and
`node.is-repository-validator` facts. Enrich and rerun requirements for other
established missing facts; report unresolved facts. Freeze that root and fact
set for context, evidence, and remedy follow-ups.

| Current decision | Request | Use |
|---|---|---|
| Author behavior | `--view requirements` | Apply `requirements.true`; inspect the missing facts in `requirements.unknown` and rerun. |
| Interpret or apply indexed context | `--view context --refs-json JSON` | Request a relevant `context_index` entry or requirement; read only returned context. |
| Choose verification | `--view evidence --refs-json JSON` | Map checks, tests, and assurances to selected refs; perform `semantic_reviews`, open only returned artifacts, and preserve limitations. |
| Repair a violation | `--view remedies --refs-json JSON` | Follow only returned remedies and procedures. |
| Unusual extraction | `--query-json JSON` | Use the generic filter/projection described by `--help`. |
| Debug extraction | `--view full` | Inspect the complete projection only. |

`--refs-json` contains exact `document` and `ref` pairs copied from the
requirements result. Request only information needed for the current decision,
and cite consequential requirement IDs.

For a repository validator, set `node.is-repository-validator=true` on the
Python-module query and request the relevant remedy under that root and fact
set; do not reproduce its procedure here.

## Referencing other skills

When this skill needs to mention another skill in documentation:

- use the skill name only, with an explicit requirement marker such as
  `**REQUIRED SUB-SKILL:** Use ...` or `**REQUIRED BACKGROUND:** Use ...`
- do not use `@.../SKILL.md` links to another skill file, because they force
  file loading instead of naming the dependency cleanly
