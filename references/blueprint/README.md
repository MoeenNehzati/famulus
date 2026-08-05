# Blueprint References

The concrete schemas in this directory are the canonical source for blueprint
shape and field-level authoring rules. See
[`docs/skill-blueprints.md`](../../docs/skill-blueprints.md) for the current
contributor overview and [`docs/certification_and_drift.md`](../../docs/certification_and_drift.md)
for the version-6 input and certificate contract.

## Live version-6 contracts

Version 6 is the live blueprint family. Validate each document against its
concrete schema:

- `module.schema.json`: discovery, filesystem authority, contained behavioral
  sources, and exported interfaces
- `behavioral-source.schema.json`: a whole-file gateway, owned content,
  dependencies, used interfaces, and intrinsic interface contracts
- `caller-contract.schema.json`: the shared semantic contract and
  process-binding definitions
- `direct-io.schema.json`: direct resource interactions used by semantic
  contracts
- `interface-projection.schema.json`: bounded projections of callable exports
- `pooled-review.schema.json`: generated certificate-backed review projections
- `certificate.schema.json`: the signed current-certificate and history-entry
  envelope
- `common.schema.json`: shared identifiers, locators, gateways, requirements,
  ownership, and relationship shapes

`schema.json` is the live dispatcher and accepts only version-6 modules,
behavioral sources, direct child registration, and namespace exports.
`schema.annotated-draft.json` is the matching authoring
entry point; it delegates field-level guidance to those same two concrete
schemas. Earlier schema families have been retired; their conversion behavior
is preserved only in the migration engine and its regression evidence. V4
parsing remains available only for the frozen migration bundle, explicit
migration/test fixtures, and compatibility checks that request that schema
family directly.

`template.yaml` is the schema-family artifact manifest. Its `examples` name
the live module root and ordinary behavioral-source blueprints under
`blueprints/`; `generated_outputs` names the derived `SKILL.md` blocks.

## Version-6 authoring contract

Every module or behavioral source uses `schema_version: 6` and its exact
`node_type`. A gateway is one whole existing file described by `path`,
`language`, and optional alternative `machines`. Language and machine
requirements use a name, an exact version, or a comma-separated intersection,
such as `Python`, `Python==3.11`, or `Python>=3.11,<4`. Gateway fragments,
symbols, and legacy gateway kinds are not authored. Process-specific entry and
transport mechanics belong in an interface's optional `process_binding`; its
non-empty `entry` is a provider-specific selector, not a Python identifier.

A module owns:

- optional discovery, currently `{mechanism: skill}`;
- filesystem authority and suggested permissions;
- a map of contained behavioral sources to blueprint locators;
- explicitly registered child modules keyed by their local path segment;
- namespace exports that expose all or a selected subset of a direct child; and
- exported interface IDs, each resolving to one intrinsic source interface.

An access policy admits the owning module, every module when
`allow_all_modules` is true, or a caller whose registered ancestry contains an
entry in `allowed_callers`. An empty false allowlist is therefore private to
the owner. At a namespace hop, the accepting owner becomes the
immediate caller of the next hop; upstream caller and source identities are not
propagated as permission. `uses_interfaces` is static relationship and
certification metadata, not a runtime grant.

Export versions are derived from their source interfaces. Contracts and
process bindings remain intrinsic to behavioral sources; modules do not copy
them into exports. Omitting discovery makes a module dependency-only.

A behavioral source owns:

- one gateway and a non-empty `content` list;
- optional paired `platform_support` and `runtime_dependencies` declarations
  covering the gateway implementation and every intrinsic interface;
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

Blueprint locators use `module-root` for live child registration and owned
blueprint references. `repository-root` remains for explicit cross-repository
contract references where the schema permits it. Content entries
remain case-sensitive Python regular expressions matched with `re.fullmatch`
against normalized POSIX paths under the ownership root. They declare
ownership only, not that every match is tracked or hashed. The project
node-input policy resolves the actual certificate inputs from direct ownership.

## Hash and derived artifacts

The ordered project input policy is
`../certification/node-hash-policy.yaml`, validated by its adjacent schema.
It starts from Git-tracked directly owned regular files and applies sequential
Git-ignore include/exclude rules with last-match-wins. Mandatory blueprint,
gateway, and same-owner authored-contract closure and reserved-output rejection
remain non-configurable certifier invariants.

Certification reviews gateway-language, gateway-machine, runtime-dependency,
and platform declarations for blueprint correctness. The resulting versioned
review records belong in `checks`; certificates do not record host-runtime
evidence or performance observations.

`schema-meta.json` defines the annotation protocol and relationship matrix.
Pre-v4 declarations and health records are retained only as migration-engine
evidence; live certification records use `certificate.schema.json`.
