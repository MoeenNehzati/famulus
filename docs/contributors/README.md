# Contributor Guide

This is the maintainer and skill-extension entrypoint for Famulus. Start here if you want to understand how the skill system is organized, how new skills are added, and how documentation and validation stay aligned with the live blueprints.

## Core Structure

The skill system is built around a small set of explicit authored surfaces:

- [`SKILL.md`](../../skills/skill-maker/SKILL.md) for trigger and usage guidance
- [`blueprint.yaml`](../../skills/skill-maker/blueprint.yaml) for dependencies, interfaces, and invocation constraints
- private runtime files, tests, schemas, and references for implementation

The canonical references for that contract live here:

- [references/blueprint/guide.md](../../references/blueprint/guide.md)
- [references/blueprint/schema.json](../../references/blueprint/schema.json)
- [references/blueprint/template.yaml](../../references/blueprint/template.yaml)
- [references/skill-guidelines.md](../../references/skill-guidelines.md)

## How Skills Stay in Sync

[`blueprint.yaml`](../../skills/skill-maker/blueprint.yaml) is the canonical machine-readable contract. Generated compatibility artifacts and generated `SKILL.md` blocks are refreshed through [skills/skill-maker/_rtx/_blueprint_syncer.py](../../skills/skill-maker/_rtx/_blueprint_syncer.py):

```bash
python3 skills/skill-maker/_rtx/_blueprint_syncer.py
```

Cross-skill script calls should go through the dispatcher boundary, not direct script reach-through:

```bash
dispatcher --caller-skill <caller> <callee> <interface-id> [args...]
```

## Validation and Enforcement

Famulus enforces the documentation and skill contracts through repo validators, the local pre-commit hook, and GitHub Actions:

- [`validators/runner.py`](../../validators/runner.py)
- [`.githooks/pre-commit`](../../.githooks/pre-commit)
- [`.github/workflows/python-tests.yml`](../../.github/workflows/python-tests.yml)

For hook order, CI behavior, and Python test-suite boundaries, see [TESTING.md](../../TESTING.md).

## Development-Facing Skill Areas

### Skill Making

These skills own the authoring conventions, scaffolding rules, and skill-system refactors.

<!-- BEGIN AUTO-GENERATED DOCS: skill-making-development-assistant -->
> Generated from live blueprints. Do not edit this block by hand.

- `hook-maker` — Design cross-host assistant hooks with one purpose and per-host bindings
- `install-assistant-tools` — Install or update launchers, wiring, hooks, and environment on a machine
- `refactor-skills` — Audit and refactor existing skills against local conventions
- `skill-health` — Reading, invalidating, migrating, or recording the local health state of Famulus skills
- `skill-maker` — Author new skills that conform to the repo's skill-writing guideline
- `update-skill-guidelines` — Change the skill-writing standard and its mechanical checks in lockstep
<!-- END AUTO-GENERATED DOCS: skill-making-development-assistant -->

### Coding

These skills focus on project scaffolding rather than the shared skill system itself.

<!-- BEGIN AUTO-GENERATED DOCS: coding-development-assistant -->
> Generated from live blueprints. Do not edit this block by hand.

- `initialize-tdd` — Scaffold a staged, approval-gated TDD project
<!-- END AUTO-GENERATED DOCS: coding-development-assistant -->

### General Development

These tools support repo work without being part of the authored skill-contract machinery.

<!-- BEGIN AUTO-GENERATED DOCS: development-assistant -->
> Generated from live blueprints. Do not edit this block by hand.

- `git-workflow` — Branch-safety checks and commit hygiene for any repo
<!-- END AUTO-GENERATED DOCS: development-assistant -->

## Where To Go Next

- [docs/scaffolding/README.md](../scaffolding/README.md) — long-form explanation of the scaffolding layer and why it exists
- [docs/contributors/documentation-system.md](documentation-system.md) — how doc generation and doc validation work
- [TESTING.md](../../TESTING.md) — hook order, CI behavior, and Python test-suite boundaries
- [references/blueprint/README.md](../../references/blueprint/README.md) — blueprint reference index
