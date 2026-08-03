# Central Configuration Schema Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route repository configuration and production JSON Schema loading through one strict common boundary without complicating configuration syntax.

**Architecture:** The central configuration schema selects strict domain branches through their existing root fields. `configured_schema.py` owns parsing, JSON compatibility, optional configured composition, local references, and validator construction; domain modules only translate errors and materialize domain objects.

**Tech Stack:** Python 3.11+, PyYAML, jsonschema Draft 7, pytest.

## Global Constraints

- Configuration files remain plain value-oriented YAML or JSON.
- No `kind` discriminator is added.
- No JSON Schema vocabulary is accepted as configuration syntax.
- `config_path=None` remains valid for unannotated schemas.
- Credentials, generated state, blueprints, standards, and external metadata are excluded.
- No production module other than `configured_schema.py` constructs a JSON Schema validator or resolver.

---

### Task 1: Central configuration contract

**Files:**
- Modify: `src/officina/common/configuration.schema.json`
- Modify: `src/officina/common/configured_schema.py`
- Modify: `tests/test_configured_schema.py`

**Interfaces:**
- Produces: `load_configuration(config_path, config_schema_path=None, ...) -> dict[str, Any]`
- Produces: `validate_configuration(document, config_schema_path=None, ...) -> None`
- Preserves: `configured_validator(schema_path, config_path=None, ...)`

- [ ] Add failing tests for all four natural-key configuration families, mixed-family rejection, schema-vocabulary rejection, default companion selection, and non-finite YAML rejection.
- [ ] Run `PYTHONPATH=src pytest -q tests/test_configured_schema.py` and confirm the new tests fail for missing central behavior.
- [ ] Move the annotation protocol under `definitions.configAnnotation`, add strict domain branches, and update annotation lookup.
- [ ] Add default central-schema resolution and in-memory validation while retaining explicit external companions.
- [ ] Run the focused suite and require a clean pass.

### Task 2: Repository configuration consumers

**Files:**
- Modify: `src/officina/common/docstring/docstring_policy.py`
- Modify: `src/officina/common/certification_hashing.py`
- Create: `skills/recurring-tasks/_rtx/_jobs_config.py`
- Modify: `skills/recurring-tasks/_rtx/_unit_writer.py`
- Modify: `skills/recurring-tasks/_rtx/_healthcheck_probe.py`
- Modify: `skills/recurring-tasks/_rtx/_live_probe.py`
- Modify: `skills/recurring-tasks/_rtx/_job_executor.py`
- Modify: `skills/recurring-tasks/_rtx/_job_control.py`
- Modify: `skills/cloud-files/_rtx/_drive_gateway.py`
- Modify: `skills/cloud-files/_rtx/_ensure_oauth.py`
- Test: `tests/test_configuration_consumers.py`

**Interfaces:**
- Consumes: `load_configuration` and `validate_configuration`
- Produces: `load_jobs_document(path) -> dict[str, Any]`
- Produces: `load_jobs(path) -> list[dict[str, Any]]`

- [ ] Add failing integration tests proving config consumers reject malformed fields and accept existing documents unchanged.
- [ ] Run the new focused tests and confirm failure occurs at direct/manual loading boundaries.
- [ ] Migrate docstring and node-hash policy loading with domain error translation.
- [ ] Introduce the recurring-jobs shared loader and route all five readers and writers through it.
- [ ] Route cloud-files settings reads and writes through central validation while excluding credentials.
- [ ] Run focused consumer and existing domain tests.

### Task 3: Production schema consumers

**Files:**
- Modify: `src/officina/common/blueprint_graph.py`
- Modify: `src/officina/common/blueprint_template.py`
- Modify: `src/officina/common/nested_module_migration.py`
- Modify: `src/officina/common/visualization/payload.py`
- Modify: `skills/list-manager/_rtx/_get_schema.py`
- Test: `tests/test_configured_schema_adoption.py`

**Interfaces:**
- Consumes: `configured_validator`, `load_configured_schema`, and `load_configured_schema_bundle`
- Preserves domain validator return behavior and domain-specific errors.

- [ ] Add a failing architecture test that identifies production validator/resolver construction outside the common boundary.
- [ ] Add focused behavior tests for blueprint, visualization, frozen migration, and list-manager bundles.
- [ ] Replace direct schema parsing and resolver construction with common API calls and confined roots/catalogs.
- [ ] Preserve domain exception translation and schema introspection needed by blueprint rendering and list-manager descriptions.
- [ ] Run focused schema and consumer tests.

### Task 4: Documentation and final validation

**Files:**
- Modify: `docs/contributors/configured-schema.md`
- Modify: `src/officina/common/README.md`

**Interfaces:**
- Documents the central config families, optional configuration semantics, exclusions, and migration rule.

- [ ] Update contributor documentation with compact examples for each configuration family.
- [ ] Document that schema complexity is implementation-only and cannot appear in configs.
- [ ] Run configured-schema, docstring, certification, blueprint, visualization, recurring-task, cloud-files, and list-manager focused suites.
- [ ] Run the architecture test and require no direct production validator construction outside the common boundary.
