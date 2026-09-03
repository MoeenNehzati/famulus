# Contributor Guide

This is the maintainer and skill-extension entry point for Famulus. Famulus
combines model-interpreted instructions with machine-executable components;
start here to understand how those components are organized, how new behavior
is added, and how documentation and validation stay aligned with live
blueprints.

For a compact map of which authoring, refactoring, blueprint, standards, and
certification skill to use, start with the
[Skill Development Quickstart](../quickstarts/skill-development.md).

## Core Structure

The skill system is built around a small set of explicit authored surfaces:

- [`SKILL.md`](../../skills/skill-maker/SKILL.md) for model-interpreted trigger
  and usage guidance
- [`blueprint.yaml`](../../skills/skill-maker/blueprint.yaml) for the module boundary, exports, access, and discovery
- `blueprints/*.yaml` under each module root for that module's behavioral
  sources, intrinsic interfaces, dependencies, and process bindings
- an optional `_rtx/` child module for owned machine-executable behavior
- private runtime files, tests, schemas, and references for implementation

Contributors should use the most formal adequate representation for each part
of the behavior: deterministic code for mechanically decidable operations,
structured contracts for formal facts, and free-form instructions for the
semantic remainder. When behavior crosses an ownership boundary, declare the
dependency and use an exposed interface rather than relying on physical access
to another node's internals. The
[Architectural Principles](../officina/architectural-principles.md) state these
rules in full.

Start with these architecture and contract references:

- [Blueprints](../officina/blueprints.md)
- [Blueprint schema](../../references/blueprint-schema/schema.json)
- [Blueprint authoring template](../../references/blueprint-schema/template.yaml)
- [Layered node standards](../../references/node-standards/node.standard.yaml),
  queried through `refactor-node`

## How Skills Stay in Sync

The module and contained-source blueprints are the canonical machine-readable
graph. Generated `SKILL.md` blocks and repository indexes are refreshed through
`skill-maker`'s exported sync interface. Check whether they are current:

```json
{"caller":"node-certify","interface":"skill-maker._rtx.interface.sync-blueprints","version":1,"arguments":{"positionals":[],"options":{"--check":true},"stdin":null},"dry_run":false}
```

Run it without `--check` only when intentionally refreshing the generated
artifacts. Do not reach past the interface to the file behind it: the syncer is
private `_rtx` content. Use the documented dispatcher route from the repository
environment rather than running that private file directly. Host agents use
the shared `famulus_dispatcher` MCP server's `invoke` tool for this object.

Cross-skill behavior should go through the dispatcher boundary, not direct
invocation of another skill's private scripts:

```json
{"caller":"<caller>","interface":"<callee>.interface.<name>","version":1,"arguments":{"positionals":[],"options":{},"stdin":null},"dry_run":false}
```

## Validation and Enforcement

Famulus enforces the documentation and skill contracts through repo validators, the local pre-commit hook, and GitHub Actions:

- [`repo_checks.py`](../../repo_checks.py)
- [`.githooks/pre-commit`](../../.githooks/pre-commit)
- [`.github/workflows/python-tests.yml`](../../.github/workflows/python-tests.yml)

For hook purpose, activation, order, and side effects, see
[Repository Git Hooks](git-hooks.md). For CI behavior and Python test-suite
boundaries, see [Repository Testing](../testing.md).

## Development-Facing Skill Areas

### Assistant Development

These skills own assistant modules, standards, and their lifecycle.

<!-- BEGIN AUTO-GENERATED DOCS: assistant-development -->
> Generated from live blueprints. Do not edit this block by hand.

- `distill-to-rutters` — An existing Markdown skill instruction should be transformed into transparent Rutters and an operable Voyage dispenser
- `hook-maker` — Design cross-host assistant hooks with one purpose and per-host bindings
- `node-certify` — Fresh certificates are requested for one or more Officina nodes
- `node-drift` — Whether Officina node certificates are current or stale, or asks for canonical node hashes
- `refactor-node` — Refactor whole repository nodes or owned sub-scopes by gateway language
- `regenerate-blueprints` — An existing skill blueprint needs regeneration, whether requested directly or required by another skill
- `relocate-nodes` — Registered Officina nodes or their owned files must be moved while mechanically updating blueprint ownership, references, generated artifacts, and callers
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
- `dev-activation` — A developer needs an assistant or editor to run against one Famulus checkout without discovering globally installed skills or plugins
- `git-workflow` — Branch-safety checks and commit hygiene for any repo
- `initialize-tdd` — Scaffold a staged, approval-gated TDD project
- `semantic-integration` — Integrating substantially diverged Git branches and merge or rebase is inadequate because it produces broad structural conflicts, or because mechanical application would place source changes into structures the target architecture has replaced and thereby lose their intent
<!-- END AUTO-GENERATED DOCS: software-development -->

## Where To Go Next

- [Maintainer Scaffolding](../officina/scaffolding/README.md) — the repository
  machinery around authored and generated skill surfaces
- [Documentation System](documentation-system.md) — documentation generation,
  publication, and validation
- [Repository Testing](../testing.md) — test commands, suite policy, hooks, CI,
  and parallel execution
- [Blueprint References](../../references/blueprint-schema/README.md) — the
  concrete blueprint contract index
