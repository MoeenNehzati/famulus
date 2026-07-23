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
<module-root>/
  blueprint.yaml
  blueprints/
    gateway.yaml
    runner.yaml
  SKILL.md
  _rtx/
    _runner.py
```

The canonical identities are:

```text
module:             <module-id>
behavioral source:  <module-id>.source.<local-source-id>
source interface:   <source-id>.interface.<local-interface-name>
module export:      <module-id>.interface.<export-name>
```

Module blueprints are `<module-root>/blueprint.yaml`. Directly contained source
blueprints are `<module-root>/blueprints/*.yaml`. Inventory follows those
declarations; hidden sidecars and directory conventions do not create nodes.

## Module blueprint

A module blueprint owns:

- identity, description, gateway, and module content scope;
- contained-source blueprint locators;
- exported interface IDs and caller access;
- filesystem authority and optional host discovery.

For example:

```yaml
schema_version: 4
node_type: module
id: example-skill
version: 1
description: Example module.
gateway: {path: SKILL.md, language: Markdown}
content: [SKILL\.md, _rtx/_runner\.py]
authority: {owns_filesystem: []}
discovery: {mechanism: skill}
sources:
  example-skill.source.gateway:
    blueprint: {base: module-root, path: blueprints/gateway.yaml}
  example-skill.source.runner:
    blueprint: {base: module-root, path: blueprints/runner.yaml}
exports:
  example-skill.interface.run:
    source_interface: example-skill.source.runner.interface.run
    access:
      allow_all_modules: false
      allowed_callers: [approved-caller]
```

Only exports cross module boundaries. Restricted exports are still exports;
private source interfaces are omitted from `exports`.

## Behavioral-source blueprint

A behavioral-source blueprint owns:

- its gateway and directly owned content;
- intrinsic interfaces and their contracts;
- source dependencies and used interfaces;
- process bindings, direct I/O, effects, platform support, and runtime
  dependencies.

```yaml
schema_version: 4
node_type: behavioral_source
id: example-skill.source.runner
version: 1
description: Implements the run operation.
gateway: {path: _rtx/_runner.py, language: Python>=3.11}
content: [_rtx/_runner\.py]
dependencies: []
uses_interfaces: []
interfaces:
  example-skill.source.runner.interface.run:
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

For a typical skill, `SKILL.md` is both the module gateway and the gateway of
`<module-id>.source.gateway`. The source directly owns the file. This does not
create a second contract or blueprint for the same node.

## Ownership and dependencies

Module content contains source content, but each regular file has exactly one
direct owner: its most specific source, or the module if no source owns it.
Sibling source ownership cannot overlap.

A source declares another source in `dependencies` when its behavior depends
on that source as a unit. It declares callable contracts in
`uses_interfaces`. Same-module uses may target private source interfaces.
Cross-module uses must target an authorized module export.

Blueprints reference facts owned elsewhere instead of copying them. In
particular, modules do not duplicate source contracts, and consumers do not
duplicate provider contracts.

## Authoring workflow

1. Define the module boundary, sources, gateways, and direct ownership.
2. Define each source interface and its complete contract.
3. Export only the interfaces intended to cross the module boundary.
4. Declare source and interface dependencies at the source that uses them.
5. Run the `skill-maker.interface.sync-blueprints` check and repository
   validators.
6. Review blueprints against actual gateways and content, then certify the
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
