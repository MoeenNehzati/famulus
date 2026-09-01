# Skill-node Maintainer Scaffolding

This is the current maintainer guide for the repository's Officina skill-node
profile. It explains what to edit, what to regenerate, which public boundaries
run the machinery, and which checks establish confidence. If the node model is
unfamiliar, the [Officina Overview](../README.md) and [Getting
Started](../getting-started.md) provide background; use
[Blueprints](../blueprints.md) for the meaning of blueprint fields.

The central distinction is authority. Blueprints and gateways are authored
surfaces: they own architectural claims and behavior. Generated contract
blocks, indexes, and documentation are derived views of those claims.
Certificates are retained assurance for an exact state. Edit the authority,
then deliberately synchronize its views; never treat a generated view or a
certificate as a second source of truth.

The maintenance lifecycle follows that boundary:

1. Identify the blueprint, gateway, or standard that owns the fact.
2. Change that authority without crossing undeclared ownership or interface
   boundaries.
3. Synchronize generated views.
4. Validate the declared shape, repository graph, and affected behavior.
5. Exercise cross-module behavior through Dispatcher rather than a private
   implementation path.
6. Certify the exact committed state when fresh semantic assurance is needed.

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

Blueprints state architectural facts; gateways realize behavior. None of the
derived artifacts above create new nodes or relationships.

## 2. Synchronization

`skill-maker._rtx.interface.sync-blueprints` is the public synchronization
boundary. It checks or refreshes generated blueprint contract/interface blocks
in `SKILL.md` and repository-level generated artifacts such as the runtime
dependency index.

Check without changing generated files:

```json
{"caller":"node-certify","interface":"skill-maker._rtx.interface.sync-blueprints","version":1,"arguments":{"positionals":[],"options":{"--check":true},"stdin":null},"dry_run":false}
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

The schemas under
[`references/standards-schema/`](../../../references/standards-schema/)
validate structured standard documents, while the canonical policy lives in
[`references/node-standards/`](../../../references/node-standards/). The
[Standards](../standards.md) guide explains why structural validity and policy
authority are separate.

Run validators and tests through the repository entry point:

```bash
python3 repo_checks.py --suite validators
python3 repo_checks.py --suite precommit
```

`node-drift` reads certificate currentness. It does not write a parallel health
or conformance state.

## 4. Runtime boundary

Cross-module invocation goes through one exported interface:

```json
{"caller":"<caller-module>","interface":"<provider-module>.interface.<export>","version":1,"arguments":{"positionals":[],"options":{},"stdin":null},"dry_run":false}
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

Choose the route from the kind of change, not from the file that happens to be
open:

| Task | Route |
| --- | --- |
| Create a skill or change its intended behavior or public interface | `skill-maker` |
| Improve a registered node while preserving behavior | `refactor-node` |
| Move a registered node or owned file | `relocate-nodes` |
| Change canonical repository policy | `update-standards` |
| Issue fresh semantic assurance | `node-certify` |
| Check whether retained assurance is current | `node-drift` |

These routes own their operational instructions. This guide only shows how
their responsibilities fit together.

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

The general node rules live under
[`references/node-standards/`](../../../references/node-standards/). The
skill-specific interface-design guide under
[`references/skill-standards/`](../../../references/skill-standards/) explains
when one instruction gateway should route to additional instruction sources.
It complements rather than replaces the selected node-standard closure.

## Canonical references

- [Officina Overview](../README.md)
- [Getting Started](../getting-started.md)
- [Architectural Principles](../architectural-principles.md)
- [Blueprints](../blueprints.md)
- [Dispatcher](../dispatcher.md)
- [Certification and Drift](../certification_and_drift.md)
- [Standards](../standards.md)
- [Blueprint Search](../blueprint_search.md)
- [Blueprint schemas](../../../references/blueprint-schema/README.md)
- [Layered node standards](../../../references/node-standards/node.standard.yaml)
- [Standards schema](../../../references/standards-schema/standard-v6.schema.json)
- [Skill interface design guidance](../../../references/skill-standards/interface-design.md)
