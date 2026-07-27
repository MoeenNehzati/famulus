# Skill Blueprints

This guide explains the live blueprint model. The schemas under
[`references/blueprint/`](../references/blueprint/) remain authoritative for
field shapes and required values.

## Model

Every blueprint describes one node. The repository has exactly two node kinds:

- a `module` is a directory-rooted identity, namespace, discovery, access, and
  authority boundary;
- a `behavioral_source` is a cohesive implementation or instruction unit
  contained by exactly one module.

Every node has content, one whole-file gateway, and one blueprint. The gateway
is its operational face; the blueprint is its descriptive face. Certification
checks that they agree.

An interface is owned by a behavioral source. It is private to the containing
module unless the module exports it. An export adds a public ID and access
policy but does not copy the interface contract or version.

## Layout and identity

```text
skills/<skill-id>/
  blueprint.yaml
  blueprints/
    gateway.yaml
  SKILL.md
  _rtx/
    blueprint.yaml
    __init__.py
    blueprints/
      runner.yaml
    runner.py
```

The canonical identities are:

```text
skill module:       <skill-id>
code module:        <skill-id>-rtx
behavioral source:  <module-id>.source.<local-source-id>
source interface:   <source-id>.interface.<local-interface-name>
module export:      <module-id>.interface.<export-name>
```

Module blueprints are `<module-root>/blueprint.yaml`. A parent registers each
direct child in `children`; physical nesting without that registration is
invalid. Directly contained source blueprints are
`<module-root>/blueprints/*.yaml`. Inventory follows declarations, not hidden
sidecars or directory conventions.

Every repository-managed skill registers exactly one non-discoverable code
child at `_rtx/`, with global ID `<skill-id>-rtx`. The skill parent owns
instruction behavior, discovery, and parent-facing interfaces. The child owns
executable behavior, machine interfaces, runtime assets, and its tests.

## Module blueprint

A module blueprint owns:

- identity, description, gateway, and module content scope;
- contained-source blueprint locators;
- exported interface IDs and caller access;
- filesystem authority and optional host discovery.

For example:

```yaml
schema_version: 5
node_type: module
id: example-skill
version: 2
description: Example module.
gateway: {path: SKILL.md, language: Markdown}
content: [SKILL\.md]
authority: {owns_filesystem: []}
discovery: {mechanism: skill}
children:
  example-skill-rtx:
    base: module-root
    path: _rtx/blueprint.yaml
sources:
  example-skill.source.gateway:
    blueprint: {base: module-root, path: blueprints/gateway.yaml}
namespace_exports: {}
exports:
  example-skill.interface.run:
    facade_interface:
      interface: example-skill-rtx.interface.run
      version: 1
    access:
      allow_all_modules: false
      allowed_callers: [approved-caller]
```

The `schema_version` field is explicit because mixed-version repositories are
invalid. Only exports cross module boundaries.
Restricted exports are still exports; private source interfaces are omitted
from `exports`. A facade may target only an exported child interface and can
only restrict its caller policy further.

`children` registers a namespace; it does not expose that namespace outside
the parent. `namespace_exports` optionally routes a registered child's own
interface IDs across the parent boundary. Routes preserve descendant IDs and
may expose `all` reviewed exports or an exact `only` surface. They do not copy
contracts or flatten names.

## Behavioral-source blueprint

A behavioral-source blueprint owns:

- its gateway and directly owned content;
- intrinsic interfaces and their contracts;
- source dependencies and used interfaces;
- process bindings, direct I/O, effects, platform support, and runtime
  dependencies.

```yaml
schema_version: 5
node_type: behavioral_source
id: example-skill-rtx.source.runner
version: 1
description: Implements the run operation.
gateway: {path: runner.py, language: Python>=3.11}
content: [runner\.py]
dependencies: []
uses_interfaces: []
interfaces:
  example-skill-rtx.source.runner.interface.run:
    version: 1
    description: Run one operation.
    contract: {}
    process_binding:
      kind: process
      entry: Interface
      patterns: []
```

The abbreviated empty contract and patterns above illustrate ownership only;
real callable interfaces must satisfy the complete schemas.

The code module separately exports the source interface as
`example-skill-rtx.interface.run`. For a typical skill, `SKILL.md` is both the
parent module gateway and the gateway of `<skill-id>.source.gateway`; the
source directly owns the file.

## Ownership and dependencies

Parent containment excludes registered child module roots. Each regular file
has exactly one direct owner: its most specific source, or its immediate module
if no source owns it. Sibling source ownership cannot overlap, and a parent
cannot claim a child's content or filesystem authority.

A source declares another source in `dependencies` when its behavior depends
on that source as a unit. It declares callable contracts in
`uses_interfaces`. Same-module uses may target private source interfaces.
Cross-module uses must target an authorized module export.

Caller allowlists accept a globally unique module ID or a Python-style
leading-dot reference relative to the declaring module. `._rtx` names a skill
parent's code child; `..parser` names the owner's sibling `parser`. These
references resolve to one exact ID through the registered tree and never grant
all descendants implicitly.

Parent facade filters and child export filters intersect; a parent cannot widen
the child's access or export a private child interface. A direct request to a
child export does not use the parent's facade. A parent or sibling inside the
same registered subtree therefore bypasses the common parent's outward
namespace route, but still must satisfy the child's own export policy.

Blueprints reference facts owned elsewhere instead of copying them. In
particular, modules do not duplicate source contracts, and consumers do not
duplicate provider contracts.

## Authoring workflow

1. Define the module boundary, registered children, sources, gateways, and
   direct ownership.
2. Define each source interface and its complete contract.
3. Export only the interfaces intended to cross each module boundary; add a
   namespace route only when descendant IDs must be outwardly discoverable.
4. Add parent facades only for intentional parent-owned APIs, and keep their
   access no broader than the child export.
5. Declare source and interface dependencies at the source that uses them.
6. Run the `skill-maker.interface.sync-blueprints` check and repository
   validators.
7. Review blueprints against actual gateways and content, then certify the
   exact committed state through `skill-certifier.interface.certify`.

Generated `SKILL.md` blocks and runtime-dependency indexes are derived views.
Certificate logs are certification state. None of them add nodes or graph
relationships.

## Related documentation

- [Architecture](architecture.md)
- [Certification and drift](certification_and_drift.md)
- [Blueprint search](blueprint_search.md)
- [Scaffolding](scaffolding/README.md)
- [Blueprint schema reference](../references/blueprint/README.md)
