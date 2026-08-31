# Skill Blueprints

This guide explains the live blueprint model. The schemas under
[`references/blueprint-schema/`](../../references/blueprint-schema/) remain authoritative for
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

## Readiness, installation, and preferences

Every module and behavioral source declares `maturity: stable` or
`maturity: experimental`. `stable` identifies a ready node; `experimental`
identifies a node still being evaluated. This is readiness metadata, not an
installation decision: either maturity may be `core` or `optional`.

Every discoverable skill module also declares `installation_tier: core` or
`installation_tier: optional`. These are historical discovery metadata, not
package-selection authority. Task 1's exact `mcp-core.json` declaration is the
sole core-package authority. Core setup repairs only that declaration in the
exact selected Python environment and does not inspect blueprint tiers or a
repository-wide dependency inventory.

An optional feature owns its own exact selected-package declaration and checks
or repairs it only when that feature's setup route is selected. Selecting one
feature neither inspects nor installs packages owned by another, and Famulus
has no install-all or dependency-reconciliation route. A module may also
declare `personal_preference.applies`. When true, its nonempty
`personal_preference.description` records the user-specific workflow choice;
when false, no description is needed.

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
code module:        <skill-id>._rtx
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
child at `_rtx/`, with global ID `<skill-id>._rtx`. The skill parent owns
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
schema_version: 6
node_type: module
id: example-skill
version: 2
maturity: stable
description: Example module.
gateway: {path: SKILL.md, language: Markdown}
content: [SKILL\.md]
authority: {owns_filesystem: []}
discovery: {mechanism: skill}
installation_tier: core
personal_preference: {applies: false}
children:
  _rtx: {}
sources:
  example-skill.source.gateway:
    blueprint: {base: module-root, path: blueprints/gateway.yaml}
namespace_exports:
  _rtx:
    version: 1
    access: {allow_all_modules: false, allowed_callers: [approved-caller]}
    surface:
      only: {example-skill._rtx.interface.run: 1}
exports: {}
```

The `schema_version` field is explicit because mixed-version repositories are
invalid. Only exports cross module boundaries.
Restricted exports are still exports; private source interfaces are omitted
from `exports`. Version 6 does not rename child exports through facades.

`children` registers a namespace; it does not expose that namespace outside
the parent. `namespace_exports` optionally routes a registered child's own
interface IDs across the parent boundary. Routes preserve descendant IDs and
must name an explicit, nonempty `surface.only` mapping. Version 6 has no
`surface.all` form: adding a child export never expands a reviewed parent route
implicitly. Routes do not copy contracts or flatten names.

## Behavioral-source blueprint

A behavioral-source blueprint owns:

- its gateway and directly owned content;
- intrinsic interfaces and their contracts;
- source dependencies and used interfaces;
- process bindings, direct I/O, effects, platform support, and runtime
  dependencies.

```yaml
schema_version: 6
node_type: behavioral_source
id: example-skill._rtx.source.runner
version: 1
maturity: stable
description: Implements the run operation.
gateway: {path: runner.py, language: Python>=3.11}
content: [runner\.py]
dependencies: []
uses_interfaces: []
interfaces:
  example-skill._rtx.source.runner.interface.run:
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
`example-skill._rtx.interface.run`. For a typical skill, `SKILL.md` is both the
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
references resolve to one exact registered module. Naming a module admits that
module and its registered descendants, but never its parent or siblings.

Namespace-route filters and the child export filter intersect. A parent cannot
widen the child's access or export a private child interface. A parent or
sibling inside the same registered subtree bypasses only namespace boundaries
it does not cross and must still satisfy the child's export policy.

Blueprints reference facts owned elsewhere instead of copying them. In
particular, modules do not duplicate source contracts, and consumers do not
duplicate provider contracts.

## Authoring workflow

1. Define the module boundary, registered children, sources, gateways, and
   direct ownership.
2. Define each source interface and its complete contract.
3. Export only the interfaces intended to cross each module boundary; add a
   namespace route only when descendant IDs must be outwardly discoverable.
4. Add an explicit namespace route when callers outside the parent subtree need
   the child's fully qualified interface ID.
5. Declare source and interface dependencies at the source that uses them.
6. Run the `skill-maker._rtx.interface.sync-blueprints` check and repository
   validators.
7. Review blueprints against actual gateways and content, then certify the
   exact committed state through `node-certify._rtx.interface.certify`.

Generated `SKILL.md` blocks and runtime-dependency indexes are derived views.
Certificate logs are certification state. None of them add nodes or graph
relationships.

## Related documentation

- [Architecture](architecture.md)
- [Dispatcher](dispatcher.md)
- [Certification and drift](certification_and_drift.md)
- [Blueprint search](blueprint_search.md)
- [Scaffolding](scaffolding/README.md)
- [Blueprint schema reference](../../references/blueprint-schema/README.md)
