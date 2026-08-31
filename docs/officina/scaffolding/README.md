# Maintainer Scaffolding

This guide explains the repository machinery around an Officina skill: which
surfaces are authored, which views are generated, which public boundaries run
the machinery, and which checks establish confidence. For the architectural
meaning of blueprint fields, start with [Blueprints](../blueprints.md).

## 1. Authored surfaces

A discoverable skill module owns its instruction behavior and may contain one
non-discoverable runtime child for executable behavior:

```text
skills/<skill-id>/
  SKILL.md
  blueprint.yaml
  blueprints/
    gateway.yaml
    <instruction-source>.yaml
  tests/
  _rtx/                         # optional
    blueprint.yaml
    __init__.py
    blueprints/
      <runtime-source>.yaml
    <implementation files>
    tests/
```

The parent `blueprint.yaml` defines the skill module's identity, discovery,
authority, sources, children, exports, and access policy. Its
`blueprints/*.yaml` files describe behavioral sources owned directly by that
module.

When the skill owns executable behavior, `_rtx/` is a registered,
non-discoverable child module. Its blueprint owns the executable namespace and
its `blueprints/*.yaml` files own runtime sources and machine interfaces. An
instruction-only skill need not have this child.

Root `tests/` exercise the discoverable gateway and its routing contract.
`_rtx/tests/` exercise private runtime behavior. The two test surfaces follow
the behavior they verify; see [Repository Testing](../../testing.md#adding-tests).

Blueprints state architectural facts; gateways realize behavior. Both are
authored surfaces. Generated blocks and indexes are derived views, while
certificates are assurance state. None of these derived artifacts create new
nodes or relationships.

## 2. Synchronization

`skill-maker._rtx.interface.sync-blueprints` is the public synchronization
boundary. It checks or refreshes generated blueprint contract/interface blocks
in `SKILL.md` and repository-level generated artifacts such as the runtime
dependency index.

Check without changing generated files:

```bash
dispatcher --caller-skill node-certify \
  skill-maker._rtx.interface.sync-blueprints --check
```

Run the same interface without `--check` only when intentionally refreshing
derived artifacts. Do not invoke the private syncer implementation directly.

## 3. Validation and certification

The repository separates three forms of assurance:

1. The version-6 schemas under
   [`references/blueprint-schema/`](../../../references/blueprint-schema/)
   validate closed document shapes.
2. Repository validators under [`validators/skill/`](../../../validators/skill/)
   check graph-wide facts such as identity, ownership, dependencies, exports,
   access, bindings, and generated-view consistency.
3. `node-certify` performs semantic review and records certificates for the
   exact committed node state.

Run validators and tests through the repository entry point:

```bash
python3 repo_checks.py --suite validators
python3 repo_checks.py --suite precommit
```

`node-drift` reads certificate currentness. It does not write a parallel health
or conformance state.

## 4. Runtime boundary

Cross-module invocation goes through one exported interface:

```bash
dispatcher --caller-skill <caller-module> \
  <provider-module>.interface.<export> [arguments...]
```

Dispatcher resolves only the relevant caller and target blueprint chain,
checks each crossed access policy, compiles the source-owned process binding,
and invokes the gateway. It does not repair blueprints or validate unrelated
modules. Certification status is advisory during dispatch; authority still
comes from the target-side blueprint policies.

Callers do not invoke another module's private runtime path or private source
interface. The [Dispatcher](../dispatcher.md) guide owns the complete runtime
contract.

## 5. Safe change routes

For a normal module change:

1. Edit the blueprint or gateway that owns the fact.
2. Declare dependencies, interfaces, authority, and effects at their canonical
   owners.
3. Check synchronization, then refresh derived views intentionally if needed.
4. Run the affected validators and tests.
5. Review blueprint claims against actual behavior.
6. Certify the exact committed state when fresh certification is required.

For a structural change, use [Refactoring Officina Nodes](../refactor.md) to
choose between in-place refactoring and relocation. For a standards change,
use `update-standards` so the selected authority and its pinned dependent
closure remain aligned.

## Canonical references

- [Architectural Principles](../architectural-principles.md)
- [Blueprints](../blueprints.md)
- [Dispatcher](../dispatcher.md)
- [Certification and Drift](../certification_and_drift.md)
- [Standards](../standards.md)
- [Blueprint Search](../blueprint_search.md)
- [Blueprint schemas](../../../references/blueprint-schema/README.md)
- [Layered node standards](../../../references/node-standards/node.standard.yaml)
