# Implementation Phase Index

Start with [../IMPLEMENT.md](../IMPLEMENT.md). This file only indexes phases; it
does not define a second execution workflow or normative requirements.

**Goal:** Implement the normative machine-module, caller-contract,
consumer-local injection, and interface-admissibility design without preserving
existing v2 or singular-prototype declarations as a second authority.

**Architecture:** JSON Schemas define closed local shapes; a strict inventory
feeds graph normalization; graph/static validators enforce relationships and
bindings; dispatcher resolves nested exports; injection selects certified
consumer-local canonical YAML; admissibility and certification bind machine,
conformance, and semantic evidence.

**Tech Stack:** Python 3, PyYAML, JSON Schema draft-07, pytest, dispatcher,
existing Officina graph/health/template packages, generated skill standards.

## Global constraints

- Follow the precedence, workflow, stop conditions, and completion-report
  format in `../IMPLEMENT.md`.
- Read the decision ledger once, then only the normative sections named by the
  active phase.
- Treat existing blueprint declarations as non-authoritative migration hints.
  Phases 1 through 4 use live content, gateways, tests, and observed behavior as
  evidence but do not rewrite live blueprints.
- Do not retain `calls`, selectors, constraints, profiles, dispatcher
  consequences, or removed execution alternatives in v3.
- Preserve unrelated dirty worktree changes; stage exact files only.
- Every plan ends with passing focused tests and `git diff --check`.
- Do not proceed past a plan with unexplained failures.

## Order

1. [01-schema-standards-and-fixtures.md](01-schema-standards-and-fixtures.md)
2. [02-inventory-graph-and-dispatcher.md](02-inventory-graph-and-dispatcher.md)
3. [03-consumer-local-injection.md](03-consumer-local-injection.md)
4. [04-admissibility-and-certification.md](04-admissibility-and-certification.md)
5. [05-migration-docs-and-release.md](05-migration-docs-and-release.md)

Plans 2 and 3 consume the schema API from Plan 1. Plan 4 consumes the normalized
graph and projection APIs from Plans 2-3. Plan 5 performs repository migration
only after all target infrastructure exists and only when the user explicitly
authorizes Phase 5.
