# Blueprint Catalog Format Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the old module `category`, `role`, and `kind` discovery tags with a configuration-constrained catalog and activation format without migrating existing blueprints.

**Architecture:** A value-only blueprint configuration owns the allowed catalog and activation labels. The central configuration schema validates that file, while the module JSON Schema uses `x-officina-config` only where those values constrain enums. Blueprint schema loaders supply the configuration; unrelated JSON Schemas remain ordinary `jsonschema` consumers.

**Tech Stack:** YAML, JSON Schema Draft 7, Python, `officina.common.configured_schema`

## Global Constraints

- Do not modify any existing skill `blueprint.yaml`.
- `catalog.domain`, `catalog.visibility`, and `persistent_modifier` are scalar values.
- `catalog.topics` and `activated_by` are nonempty unique lists.
- Configuration files contain values only, never JSON Schema fragments.
- Only schemas actually using configuration route through `configured_schema`.

---

### Task 1: Define the blueprint configuration family

**Files:**
- Create: `references/blueprint/config.yaml`
- Modify: `src/officina/common/configuration.schema.json`

**Interfaces:**
- Produces: `/blueprint_catalog/domains`, `/blueprint_catalog/topics`, `/blueprint_catalog/visibility`, and `/blueprint_catalog/activated_by` configuration sources.

- [x] Add the approved value lists to `references/blueprint/config.yaml`.
- [x] Add a strict `blueprintCatalogConfig` branch to the central configuration schema.

### Task 2: Define the configured module discovery format

**Files:**
- Modify: `references/blueprint/module.schema.json`

**Interfaces:**
- Consumes: the four configuration sources from Task 1.
- Produces: required discoverable-module fields `catalog`, `activated_by`, and `persistent_modifier` under `discovery`.

- [x] Remove the old `category`, `role`, and `kind` properties and definitions.
- [x] Define the strict catalog, activation, and modifier structures.
- [x] Attach `x-officina-config` enum intersections to configured values.

### Task 3: Supply configuration at blueprint schema-loading boundaries

**Files:**
- Modify: `src/officina/common/blueprint_graph.py`
- Modify: `src/officina/common/blueprint_template.py`
- Modify: `src/officina/common/nested_module_migration.py`

**Interfaces:**
- Consumes: `references/blueprint/config.yaml` and configured module schema annotations.
- Produces: validators and template schemas containing the configured enum constraints.

- [x] Route the blueprint schema bundle through `configured_schema` with the sibling config.
- [x] Preserve ordinary `jsonschema` handling in unrelated schema consumers.
- [x] Do not add backward compatibility for old discovery tags.

### Task 4: Document the boundary

**Files:**
- Modify: `docs/contributors/configured-schema.md`

**Interfaces:**
- Produces: contributor guidance distinguishing configured blueprint schemas from ordinary schemas.

- [x] Update the example to use the real blueprint config path and approved catalog fields.
- [x] Record that live blueprint migration is intentionally separate.
