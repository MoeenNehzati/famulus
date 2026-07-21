# Blueprint References

The concrete schemas in this directory are the canonical source for blueprint
shape and field-level authoring rules. See
[`docs/skill-blueprints.md`](../../docs/skill-blueprints.md) for the current
contributor overview and [`docs/certification_and_drift.md`](../../docs/certification_and_drift.md)
for the version-4 input and certificate contract.

## Staged version-4 contracts

Version 4 is staged for an atomic graph cutover. Validate candidates directly
against their concrete schema:

- `module.schema.json`: discovery, filesystem authority, contained behavioral
  sources, and exported interfaces
- `behavioral-source.schema.json`: a whole-file gateway, owned content,
  dependencies, used interfaces, and intrinsic interface contracts
- `caller-contract.schema.json`: source-owned semantic interface behavior
- `direct-io.schema.json`: direct resource interactions used by semantic
  contracts
- `certificate.schema.json`: the signed current-certificate and history-entry
  envelope
- `common.schema.json`: shared identifiers, locators, gateways, requirements,
  ownership, and relationship shapes

`schema.json` deliberately remains the live pre-v4 dispatcher until Task 5.
It must reject direct version-4 module and behavioral-source documents during
staging. The exact predecessor files `machine-module.schema.json` and
`behavior-source.schema.json` stay on that live route through Task 4;
`module.schema.json` and `behavioral-source.schema.json` are direct-tested
candidates only. Task 5 replaces the predecessors and routing atomically.
Other pre-v4 schema files and `v2/` remain migration inputs; do not extend them
with version-4 behavior.

`template.yaml` is the schema-family artifact manifest. Its existing
`examples` and `generated_outputs` describe the live family. `v4_examples`
names the staged module root and behavioral-source sidecars without switching
the live generator.

## Version-4 authoring contract

Every module or behavioral source uses `schema_version: 4` and its exact
`node_type`. A gateway is one whole existing file described by `path`,
`language`, and optional alternative `machines`. Language and machine
requirements use a name, an exact version, or a comma-separated intersection,
such as `Python`, `Python==3.11`, or `Python>=3.11,<4`. Gateway fragments,
symbols, and legacy gateway kinds are not authored. Process-specific entry and
transport mechanics belong in an interface's optional `process_binding`.

A module owns:

- optional discovery, currently `{mechanism: skill}`;
- filesystem authority and suggested permissions;
- a map of contained behavioral sources to blueprint locators; and
- exported interface IDs, each resolving to one intrinsic source interface
  with caller access declared as allow-all or a non-empty module allowlist.

Export versions are derived from their source interfaces. Contracts and
process bindings remain intrinsic to behavioral sources; modules do not copy
them into exports. Omitting discovery makes a module dependency-only.

A behavioral source owns:

- one gateway and a non-empty `content` list;
- direct behavioral-source dependencies with exact versions and blueprint
  locators;
- exact-version uses of sibling private interfaces or module exports; and
- intrinsic interfaces keyed by full source-interface ID.

Each intrinsic interface defines its own version, description, semantic
`contract`, and optional `process_binding`. Contracts define arguments,
preconditions, interaction, warnings, outputs, outcomes, execution, helpers,
and direct I/O. They do not contain argv/stdin bindings, output channels,
signals, cancellation transport, or stop mechanics; those belong to
`process_binding`.

Blueprint locators use only `module-root` or `repository-root`. Content entries
remain case-sensitive Python regular expressions matched with `re.fullmatch`
against normalized POSIX paths under the ownership root. They declare
ownership, not hash inclusion order.

## Hash and derived artifacts

The ordered project input policy is
`../certification/node-hash-policy.yaml`, validated by its adjacent schema.
It starts from Git-tracked directly owned regular files and applies sequential
Git-ignore include/exclude rules with last-match-wins. Mandatory blueprint,
gateway, and same-owner authored-contract closure and reserved-output rejection
remain non-configurable certifier invariants.

`schema-meta.json` defines the annotation protocol and staged relationship
matrix. `interface-projection.schema.json`, `pooled-review.schema.json`, and
their producers remain pre-v4 until they can move atomically with the version-4
graph in Task 2. `health.schema.json` likewise remains a pre-v4 migration input;
new version-4 certification records use `certificate.schema.json`.

`legacy-skill.schema.json` is an exact migration snapshot of the former
monolithic schema. Do not add new features to it.
