# Skill Blueprints

This document explains the architecture of Famulus skill blueprints for
contributors. It is a conceptual guide, not a second schema specification.
The concrete schemas under [`references/blueprint/`](../references/blueprint/)
are authoritative for fields, types, required values, examples, contract-hash
participation, and validation-rule traceability.

## Purpose

A blueprint describes a skill as an explicit artifact graph. The graph makes
the skill's public interfaces, instruction sources, implementation bindings,
dependencies, IO, and ownership boundaries inspectable without inferring them
from directory layout or prose.

Blueprints serve three related workflows:

- authors declare the intended skill contract;
- validators check the declared graph against repository and filesystem rules;
- audit and drift tooling certify the graph and later detect relevant changes.

The blueprint is not the skill's implementation and does not replace its LLM
instructions. It records the contract that connects those artifacts.

## Authority Model

Each blueprint-backed skill has one canonical graph root:

```text
skills/<skill>/blueprint.yaml
```

That root owns skill-level identity, the default LLM interface contract, and
locators for additional interface nodes. Each additional interface or
behavior-source node owns its own contract in a hidden sidecar beside the file
it binds. Nodes point to dependencies; they do not copy facts owned by those
dependencies.

The authority order is:

1. Authored root and subordinate blueprint files define the graph.
2. Files bound by those nodes provide instructions, behavior, or executable
   implementation.
3. Generated `SKILL.md` blocks, pooled reviews, manifests, and health records
   are derived views or certification state. They do not add graph authority.

[`schema.json`](../references/blueprint/schema.json) is the compatibility entry
point for the schema family. New typed authoring uses the concrete schemas
listed in the [blueprint reference index](../references/blueprint/README.md).

## Graph Nodes

The typed graph has four authored node kinds.

### Skill root

The skill root owns facts about the skill as a whole, the inline
`default_interface`, and version-pinned locators for additional interfaces.
The inline interface has the canonical ID `<skill>.llm.default` and is
implicitly bound to `SKILL.md`.

### LLM interface

An LLM-interface node binds one instruction file, such as `SKILL.md` or a named
file under `llm_interfaces/`. It declares the behavior sources and callable
interfaces used by that prompt surface.

LLM interfaces are instruction surfaces. They are selected by skill-routing
logic and are not executed by the dispatcher.

### Machine interface

A machine-interface node binds one private runtime entrypoint or command file.
It declares the callable contract used by the dispatcher, including access,
platform, dependency, IO, and filesystem-ownership metadata.

Hand-authored LLM instructions should name the canonical machine interface,
not its private runtime path. Field shapes and binding restrictions belong to
the machine-interface schema rather than this document.

### Behavior source

A behavior-source node binds one file that influences an interface or another
behavior source. Examples include a policy document, configuration file, or
shared repository standard. Directories are not bound as behavior-source
nodes; a collection that needs graph identity must expose a concrete file.

Repository-owned behavior sources live under `references/`. Skill-local
sources remain owned by their skill. Cross-skill behavior should be consumed
through declared interfaces rather than by reaching into another skill's
private files.

## File Layout

A subordinate node sidecar is hidden beside its bound file:

```text
SKILL.md
blueprint.yaml  # includes default_interface

_rtx/_worker.py
_rtx/._worker.py.blueprint.yaml

references/policy.md
references/.policy.md.blueprint.yaml
```

If multiple nodes bind the same file, their sidecar names are qualified by
local node name. The skill root alone keeps the unsuffixed `blueprint.yaml`
name.

Existing typed skills may retain `.SKILL.md.blueprint.yaml` as a compatibility
representation, but a root must not define both forms. The exact naming rules and examples are maintained in the concrete schemas and
the committed artifact-layout manifest, not here.

## Authored And Generated Artifacts

Authored contract inputs include:

- the root `blueprint.yaml` and default-bound `SKILL.md`;
- reachable interface and behavior-source sidecars;
- the instruction, runtime, and source files bound by those nodes.

Generated or local-state outputs include:

- blueprint contract and interface blocks injected into `SKILL.md`;
- repository-level generated manifests;
- node health records and legacy audit records;
- pooled blueprint reviews and their health records.

Generated artifacts must agree with the authored graph, but they never become
inputs that silently redefine it.

The inline default has no independent health record. Root skill health covers
the root contract, `SKILL.md`, and the default interface's downstream edges.

## Authoring Workflow

When creating or changing a skill:

1. Design the user-facing LLM and machine interfaces before choosing file
   layout.
2. Write the skill instructions according to the
   [skill module standards](../references/skill-standards/skill-guidelines.md).
3. Create the root and subordinate blueprints from their concrete schemas.
4. Declare every behavior-relevant bound file and cross-interface dependency
   on the node that uses it.
5. Refresh generated blueprint artifacts through the `skill-maker` sync
   interface.
6. Run repository validation and relevant tests before certification.

For the repository's concrete scaffolding workflow, see
[`docs/scaffolding/README.md`](scaffolding/README.md).

## Validation, Audit, And Drift

Schema validation checks document shape. Repository validators additionally
check relationships that require filesystem or graph context, such as locator
resolution, binding containment, node identity, access control, and dependency
compatibility.

Audit and drift have separate roles:

- `skill-audit` judges whether the declared graph exactly represents the
  selected skill closure and writes certification state when commit-backed
  requirements are satisfied;
- `skill-drift` mechanically determines whether the installed artifacts still
  match that certification state.

Health records and pooled reviews are auxiliary outputs. They cannot add nodes
or edges to the authored graph. See
[`docs/audit_and_drift.md`](audit_and_drift.md) for the certification and drift
model.

## Migration

Legacy monolithic blueprints remain a compatibility boundary while skills move
to typed, file-backed nodes. A migration should preserve canonical interface
identity and observable behavior unless a contract change is explicitly
approved. Move one interface at a time and validate the reachable graph after
each step.

Generated reviews and health records are never migration inputs.

## Reference Map

- [Blueprint reference index](../references/blueprint/README.md)
- [Compatibility schema entry point](../references/blueprint/schema.json)
- [Schema metadata protocol](../references/blueprint/schema-meta.json)
- [Skill module standards](../references/skill-standards/skill-guidelines.md)
- [LLM interface design](../references/skill-standards/llm-interface-design.md)
- [Skill scaffolding](scaffolding/README.md)
- [Audit and drift](audit_and_drift.md)
