# Maintainer Scaffolding

This document describes the repository machinery that keeps modules explicit,
composable, and checkable. Start with
[`skills/skill-maker/`](../../skills/skill-maker/): it owns blueprint
synchronization and the skill-system validators.

## Authored surfaces

A skill module normally contains:

```text
skills/<name>/
  SKILL.md
  blueprint.yaml
  blueprints/
    gateway.yaml
    <implementation>.yaml
  _rtx/
  tests/
```

- `SKILL.md` is the discoverable instruction gateway.
- `blueprint.yaml` defines the module boundary, contained sources, exports,
  access, authority, and discovery.
- `blueprints/*.yaml` define behavioral sources, their intrinsic interfaces,
  dependencies, process bindings, and direct I/O.
- private implementation and tests provide the behavior described by those
  blueprints.

The module blueprint and behavioral-source blueprints are authored authority.
Generated documentation blocks and indexes are derived views. Certificate logs
are certification state.

## Generated views

[`skills/skill-maker/_rtx/_blueprint_syncer.py`](../../skills/skill-maker/_rtx/_blueprint_syncer.py)
derives:

- blueprint contract and interface blocks in `SKILL.md`;
- `references/blueprint/runtime_dependencies.json`;
- other registered generated documentation.

Do not edit generated blocks by hand. Run the exported check:

```bash
dispatcher --caller-skill skill-certifier \
  skill-maker.interface.sync-blueprints --check
```

Run without `--check` only when intentionally refreshing generated artifacts.

## Runtime boundary

Cross-module execution uses one public form:

```bash
dispatcher --caller-skill <caller-module> \
  <provider-module>.interface.<export> [arguments...]
```

The dispatcher:

1. loads the dispatch-relevant module/source closure, treating unrelated
   blueprint defects as warnings;
2. resolves the module export to its contained source interface;
3. checks the immediately calling module against each target-side access
   policy; source identity and `uses_interfaces` do not grant permission;
4. reports unavailable or stale certificates as warnings;
5. compiles the source-owned process binding;
6. invokes the gateway through its runtime provider.

Callers do not invoke another module's private runtime path or private source
interface. Runtime declarations must name the module that owns their Python
file; repository validation checks this against the deepest registered module.

## Validation and certification

Validation has three layers:

- the v4 schemas validate closed document shapes;
- repository validators check identities, ownership, exports, dependencies,
  access, process bindings, and generated views;
- `skill-certifier` performs semantic review and issues append-only signed
  certificates for the exact committed graph state.

`skill-drift` is a read-only certificate-currentness consumer. It does not
write a parallel health or conformance state.

## Safe change routes

When changing a module:

1. Edit the module or source blueprint that owns the fact.
2. Edit its gateway or content as needed.
3. Run blueprint sync in check mode, then refresh intentionally if required.
4. Run the affected validators and tests.
5. Review the final blueprints against actual behavior.
6. Certify the exact committed state.

When changing the architecture or schema:

1. Update the existing schema, graph, compiler, or validator owner; do not add
   a parallel authority.
2. Use `update-standards` to change the smallest applicable document under
   `references/node-standards/`, then update its pinned dependent closure.
3. Update the relevant conceptual documentation.
4. Run the complete validation and certification suites.

## Canonical references

- [Architecture](../architecture.md)
- [Skill blueprints](../skill-blueprints.md)
- [Certification and drift](../certification_and_drift.md)
- [Blueprint search](../blueprint_search.md)
- [Blueprint schemas](../../references/blueprint/README.md)
- [Layered node standards](../../references/node-standards/node.standard.yaml)
