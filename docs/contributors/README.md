# Contributor Guide

This is the maintainer and skill-extension entrypoint for Famulus. Start here if you want to understand how the skill system is organized, how new skills are added, and how documentation and validation stay aligned with the live blueprints.

For a compact map of which authoring, refactoring, blueprint, standards, and
certification skill to use, start with the
[Skill Development Quickstart](../quickstarts/skill-development.md).

## Core Structure

The skill system is built around a small set of explicit authored surfaces:

- [`SKILL.md`](../../skills/skill-maker/SKILL.md) for trigger and usage guidance
- [`blueprint.yaml`](../../skills/skill-maker/blueprint.yaml) for the module boundary, exports, access, and discovery
- `blueprints/*.yaml` for behavioral sources, intrinsic interfaces, dependencies, and process bindings
- private runtime files, tests, schemas, and references for implementation

Start with these architecture and contract references:

- [docs/officina/skill-blueprints.md](../officina/skill-blueprints.md)
- [references/blueprint-schema/schema.json](../../references/blueprint-schema/schema.json)
- [references/blueprint-schema/template.yaml](../../references/blueprint-schema/template.yaml)
- Layered [node standards](../../references/node-standards/node.standard.yaml), queried through `refactor-node`

## How Skills Stay in Sync

The module and contained-source blueprints are the canonical machine-readable
graph. Generated `SKILL.md` blocks and repository indexes are refreshed through
`skill-maker`'s exported sync interface. Check whether they are current:

```bash
dispatcher --caller-skill skill-certifier \
  skill-maker._rtx.interface.sync-blueprints --check
```

Run it without `--check` only when intentionally refreshing the generated
artifacts. Do not reach past the interface to the file behind it: the syncer is
private `_rtx` content, and a bare `python3` runs outside the managed runtime,
so importing `officina` fails before the syncer does anything.

Cross-skill script calls should go through the dispatcher boundary, not direct script reach-through:

```bash
dispatcher --caller-skill <caller> <callee>.interface.<name> [args...]
```

## Validation and Enforcement

Famulus enforces the documentation and skill contracts through repo validators, the local pre-commit hook, and GitHub Actions:

- [`repo_checks.py`](../../repo_checks.py)
- [`.githooks/pre-commit`](../../.githooks/pre-commit)
- [`.github/workflows/python-tests.yml`](../../.github/workflows/python-tests.yml)

For hook order, CI behavior, and Python test-suite boundaries, see
[docs/testing.md](../testing.md).

## Development-Facing Skill Areas

### Assistant Development

These skills own assistant modules, standards, and their lifecycle.

<!-- BEGIN AUTO-GENERATED DOCS: assistant-development -->
> Generated from live blueprints. Do not edit this block by hand.

- `distill-to-rutters` — An existing Markdown skill instruction should be transformed into transparent Rutters and an operable Voyage dispenser
- `hook-maker` — Design cross-host assistant hooks with one purpose and per-host bindings
- `refactor-node` — Refactor whole repository nodes or owned sub-scopes by gateway language
- `regenerate-blueprints` — An existing skill blueprint needs regeneration, whether requested directly or required by another skill
- `relocate-nodes` — Registered Officina nodes or their owned files must be moved while mechanically updating blueprint ownership, references, generated artifacts, and callers
- `skill-certifier` — Fresh certificates are requested for one or more Officina nodes
- `skill-drift` — Whether Officina node certificates are current or stale, or asks for canonical node hashes
- `skill-maker` — Author new skills that conform to the repo's skill-writing guideline
- `update-standards` — Change canonical standards and keep their pinned closures aligned
<!-- END AUTO-GENERATED DOCS: assistant-development -->

### Software Development

These skills support general software projects and repositories.
For task-oriented routing, see the
[Software Development Quickstart](../quickstarts/development.md).

<!-- BEGIN AUTO-GENERATED DOCS: software-development -->
> Generated from live blueprints. Do not edit this block by hand.

- `ci-debug` — GitHub Actions CI is red, matrix failures need isolated repair, or repeated full reruns make remote diagnosis inefficient
- `git-workflow` — Branch-safety checks and commit hygiene for any repo
- `initialize-tdd` — Scaffold a staged, approval-gated TDD project
- `semantic-integration` — Integrating substantially diverged Git branches and merge or rebase is inadequate because it produces broad structural conflicts, or because mechanical application would place source changes into structures the target architecture has replaced and thereby lose their intent
<!-- END AUTO-GENERATED DOCS: software-development -->

## Where To Go Next

- [docs/officina/scaffolding/README.md](../officina/scaffolding/README.md) — long-form explanation of the scaffolding layer and why it exists
- [docs/contributors/documentation-system.md](documentation-system.md) — how doc generation and doc validation work
- [docs/testing.md](../testing.md) — test commands, suite policy, hooks, CI, and parallel execution
- [references/blueprint-schema/README.md](../../references/blueprint-schema/README.md) — blueprint reference index
