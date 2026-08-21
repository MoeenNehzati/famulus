# Interface Facet Hashing Design

**Date:** 2026-08-17
**Status:** Implemented

## Context

Schema v6 makes a behavioral source the canonical owner of its gateway,
content, dependencies, and used interfaces. Before this change, source
interfaces owned named, versioned contracts and bindings but did not identify
which owned content or used interfaces realized each contract. Certification
could therefore identify only node-level drift.

The implemented repository does not require a `.interface.default`: 124 of
162 behavioral sources omit one and 31 declare no interfaces. The design
preserves that standard instead of creating artificial callable interfaces.

## Goals

- Identify which explicit source interfaces changed.
- Identify changed source-owned files independently of interface assignment.
- Preserve the source node as the sole ownership and dependency envelope.
- Keep dependency identity separate from local content identity.
- Make a node hash a canonical aggregation of its local facets.
- Provide the state needed for later selective audit reuse without changing
  audit scheduling in this change.

## Non-goals

- Do not require, infer, or synthesize `.interface.default` declarations.
- Do not move filesystem ownership or invocation authority to interfaces.
- Do not issue one certificate per interface yet.
- Do not reuse prior semantic audit evidence yet.
- Do not add interface-scoped source dependencies in this change; the existing
  `dependencies` field remains source-scoped.

## Considered Designs

### Required public-looking default interfaces

Rejected. Most sources do not have a default interface, and adding one would
confuse certification partitioning with callable behavior.

### Infer content from the gateway or process binding

Rejected. A source may expose several interfaces through one whole-file
gateway, while process bindings select entries rather than all supporting
files or interface dependencies. Inference would be incomplete and unstable.

### Explicit interface subsets plus a node remainder

Selected. Each explicit interface declares the subset of source content and
source interface uses relevant to it. Anything in the source envelope that no
interface claims remains in a non-routable node remainder facet.

## Blueprint Schema

Each behavioral-source interface declaration gains two required fields:

```yaml
interfaces:
  example.source.worker.interface.run:
    version: 1
    content:
    - _worker\.py
    uses_interfaces:
    - interface: storage.interface.read
      version: 2
    contract: ...
    process_binding: ...
```

`content` uses the existing `contentPatterns` definition and is resolved
relative to the behavioral source's module root. `uses_interfaces` uses the
existing `interfaceUseList` definition.

The source-level `content` and `uses_interfaces` fields remain required and
authoritative envelopes. Validation requires:

- every interface content match is directly owned by its source;
- every interface content match is included by the source content envelope;
- every interface use is present in the source `uses_interfaces` envelope;
- the source gateway is included in every declared interface's resolved
  content set;
- interface content and uses may overlap when multiple contracts genuinely
  share implementation or dependencies.

Files and interface uses claimed by no explicit interface form the node
remainder. A source with no interfaces consists entirely of its remainder.

Existing blueprints are migrated mechanically by copying each source's
`content` and `uses_interfaces` into every existing interface. This is
conservative: it preserves current assurance scope and can later be narrowed
by semantic review. Sources without interfaces require no edit.

## Canonical Hash State

Add immutable hash-state records for explicit interfaces and the node
remainder. Each manifest entry retains path, digest, and Git provenance.

An interface local hash covers:

- interface ID and version;
- source node and source-interface binding;
- the source gateway declaration;
- the canonical interface declaration excluding `uses_interfaces`;
- the policy-selected manifest for its declared content; and
- recursively resolved contract-reference files originating in that interface
  declaration.

Its dependency records separately contain the canonical hashes of the exact
interfaces in its `uses_interfaces` list and the node hashes of cross-owner
contract references originating in that interface. The interface local hash
does not recursively include dependency hashes.

The node remainder hash covers:

- the canonical source declaration excluding `interfaces`, `dependencies`,
  and `uses_interfaces`;
- the source blueprint's canonical structural projection rather than its raw
  whole-file digest;
- policy-selected source-owned files not claimed by any explicit interface.

Source dependencies and unclaimed source interface uses remain separate
remainder dependency records.

The behavioral-source node hash is:

```text
H({
  node_id,
  node_type,
  version,
  remainder_hash,
  interfaces: sorted([{id, version, interface_hash}])
})
```

Dependency hashes are not included in this local aggregation. Module hashing
and module export authority remain unchanged in this phase.

## Certificate and Drift Projection

Certificate payload version 3 adds canonical facet claims for the remainder
and each explicit interface: local hash, input manifest, and dependency
records. The aggregate `node_hash` remains the node-level read boundary; its
manifest and dependencies are canonical unions of the facet claims. Earlier
payload versions remain append-only history but are stale under facet-aware
currentness because they lack the required claims.

Currentness comparison reports stable facet-specific concerns:

- `interface-hash-mismatch:<interface-id>`;
- `interface-input-manifest-mismatch:<interface-id>`;
- `interface-dependency-mismatch:<interface-id>`;
- `remainder-hash-mismatch`;
- `remainder-input-manifest-mismatch`;
- `remainder-dependency-mismatch`.

It also reports `facet-set-mismatch` for missing, extra, or duplicate claims
and `facet-order-mismatch` when otherwise-valid claims are not in canonical
order.

Node currentness remains the conjunction of every facet and dependency. This
change pinpoints drift but intentionally retains the existing whole-node
certification writer and checks. A later change may reuse unchanged facet
evidence and audit only stale facets.

## Error Handling

Graph loading rejects out-of-envelope interface content or uses, missing
gateway coverage, unresolved interface dependencies, and interface dependency
cycles. Hashing rejects incomplete or noncanonical graph state. Drift treats
missing legacy facet claims as stale rather than rewriting certificate
history.

## Testing

- Schema tests require both new interface fields.
- Graph tests cover subset validation, overlap, zero-interface sources, and
  unresolved or cyclic interface uses.
- Hash tests prove one file changes only its claiming interfaces plus the
  aggregate node hash, while unrelated interfaces remain stable.
- Hash tests prove one used interface changes only dependent interface state.
- Certificate/currentness tests prove exact facet mismatch diagnostics.
- A mechanical migration test validates every migrated live blueprint.
- Existing node, projection, dispatcher, drift, and certifier suites remain
  green.

## Implemented Refinements

The implementation includes four fail-closed refinements that preserve the
selected architecture:

- an exported interface hash includes the exact export declaration as well as
  its source-interface declaration and selected manifest, so access or source
  binding changes cannot escape the dependency identity;
- route-smoke validation reuses the provider facet manifest when recomputing a
  used-interface hash;
- certificate currentness requires both the exact facet set and canonical
  facet order; and
- mechanical migration stops on inventory errors and preserves authored YAML
  text outside the inserted fields.

## Rollout

1. Add schema and graph support with failing tests first.
2. Add canonical facet states and aggregate hashing.
3. Add certificate projection and facet drift diagnostics.
4. Run the mechanical blueprint migration.
5. Validate the complete live repository and certification routes.
6. Do not reissue certificates that were stale before the cutover.
