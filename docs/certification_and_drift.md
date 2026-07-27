# Certification and Drift

This document defines the live version-5 certification contract. Version-4
schemas remain only as immutable converter input under
`references/blueprint/migrations/v4/`.

Certification is repository-bound. The public certifier requires an explicit
reviewed repository and commit and derives the complete certifiable graph from
that checkout. Installed-source adapters are read-only drift diagnostics;
installing a plugin does not add its modules to the package's certification
graph.

## Nodes and dependencies

Version 5 has two authored node types:

- A `module` owns discovery, filesystem authority, contained behavioral
  sources, and exported interfaces.
- A `behavioral_source` owns one whole-file gateway, its content selection,
  intrinsic interface contracts, source dependencies, and interface uses.

The blueprint graph derives certification dependencies from source use,
private-interface use, module-export use, namespace routing, facades, topology
proofs, and cross-owner contract references. Containment assigns ownership but
adds no certification edge. An edge records the target node and its exact
version. A node's local hash does not recursively include dependency bytes;
certificates record direct dependency node hashes separately.

Every authored node has one blueprint. Every behavioral source has one
whole-file gateway. A module with discovery also has a whole-file gateway; a
dependency-only module may omit discovery. Blueprint declarations are graph
authority. Certificates and generated review artifacts report graph state but
never add graph edges.

## Structural validity and certifiability

A version-5 blueprint may be structurally valid before it is certifiable.
Structural validation requires canonical identity, a resolvable whole-file
gateway, containment and relationship shape, safe paths, and closed shapes for
every semantic value that is present. It does not manufacture semantic facts
or assert that an interface is complete.

The one-time converter preserves every authored fact while registering nested
modules, moving code ownership, and rewriting exact identities. Facts absent
from the source remain absent; migration must not invent permissive argv rules,
generic success outcomes, or other guessed defaults merely to satisfy a
schema. Missing descriptions, contract sections, invocation details,
direct-I/O facts, or compatibility claims are certifier findings.

The certifier-owned workflow reviews such a draft against the gateway and node
content. It may repair the candidate blueprint, but each repair invalidates the
previous review snapshot. The workflow reloads the schema and graph and reruns
all checks until either the blueprint is complete and exact or it reports
failure. The signing core accepts no caller-supplied payload and signs only the
final reconstructed state. No `certified`, `conformant`, or draft-status field
is authored in a blueprint; availability is determined solely from a current
certificate.

## Resolving node inputs

The certifier loads one project policy from
`references/certification/node-hash-policy.yaml` and validates it against
`references/certification/node-hash-policy.schema.json`. The canonical policy
has this shape:

```yaml
policy_version: 1
path_syntax: gitignore
starting_set: git-tracked-directly-owned-regular-files
rules:
  - action: exclude
    pattern: "**/*.log"
  - action: exclude
    pattern: "**/_build/**"
  - action: include
    pattern: "skills/example-skill/generated/required.json"
    require_match: true
```

The repository policy contains only the canonical exclusions; the final
include above illustrates the syntax and is not part of that file.

Input resolution follows these rules:

1. Normalize paths relative to the repository root and reject absolute paths,
   traversal, boundary crossings, symlinks, and special files.
2. Start from Git-tracked regular files owned directly by the node.
3. Apply policy rules in order using Git-ignore pattern syntax. The last
   matching rule wins.
4. An `include` may add an ignored or untracked regular file only when that
   file is directly owned by the node. Its optional `require_match` defaults
   to false; when true, loading the project policy fails if the rule matches no
   eligible file anywhere in the repository. An `exclude` cannot declare
   `require_match`; an unmatched exclusion is a no-op.
5. Add the mandatory closure: the blueprint, gateway, and the transitive
   same-owner authored contracts referenced by them. A policy exclusion that
   removes a mandatory input is an error. A referenced contract owned by
   another node becomes a dependency instead of a local input.

Content regular expressions continue to express authored ownership. They are
not ordered hash rules and do not replace the project policy.

The following inputs are forbidden regardless of project policy:

- current certificates and certificate histories;
- signing material;
- any other reserved certifier output.

The canonical policy explicitly excludes logs, `log/` and `logs/`, Python
bytecode and `__pycache__/`, `.pytest_cache/`, `.cache/`, `_build/`, `build/`,
`dist/`, and `.certificates/`. A later include may deliberately restore an
eligible runtime or generated regular file, but it cannot override mandatory
closure or reserved-output safety.

## Manifest, Git provenance, and node hash

The resolved `input_manifest` records each selected repository-relative
`path`, its `sha256` `digest`, and `git_provenance`: `tracked`, `ignored`, or
`untracked`. It does not record policy-internal details such as a final rule.

At issuance, all tracked manifest entries and tracked blueprint data must match
the certificate's `source_commit`. Included ignored and untracked files are
signed local-state claims: their exact bytes must remain stable while
certification runs, but they need not be committed. Consequently,
`source_commit` records the snapshot needed to reproduce the tracked subset
when a certificate includes local inputs. Later currentness requires relevant
tracked inputs to be clean at current `HEAD`, but does not require current
`HEAD` to equal the certificate's issuance commit.

`node_hash(x)` covers the canonical node identity and blueprint data together
with the selected paths and their exact bytes. It excludes certificates,
history, generated status artifacts, and dependency content. Changing an
input path, byte, blueprint field, or node identity changes the node hash.
Changing only a dependency makes the certificate suspect through its recorded
dependency hash without changing the dependent's local node hash.

## Certificate format

`references/blueprint/certificate.schema.json` defines a closed envelope:

```yaml
payload:
  certificate_schema_version: 2
  subject:
    id: example-skill-rtx.source.runtime
    node_type: behavioral_source
    version: 1
    blueprint_path: skills/example-skill/_rtx/blueprints/runtime.yaml
    gateway_path: skills/example-skill/_rtx/runtime.py
  node_hash: sha256:...
  source_commit: ...
  input_manifest:
    - path: skills/example-skill/_rtx/runtime.py
      digest: sha256:...
      git_provenance: tracked
  dependencies: []
  certification_basis_hash: sha256:...
  certifier:
    interface: skill-certifier.interface.certify
    version: 1
    node_hash: sha256:...
    source_commit: ...
  checks: []
  key_id: sha256:...
  previous_entry_hash: null
  certified_at: 2026-07-20T12:00:00Z
signature:
  scheme: ed25519
  value: base64:...
```

The signature covers the canonical encoding of `payload`. The schema does not
freeze the signature algorithm, but the scheme is explicit and the signature
value is valid base64. `certified_at` is informational and never establishes
currentness.

Each dependency entry contains `relation`, `target`, `version`, and
`node_hash`. V5 dependency relations are `uses-source`,
`uses-private-interface`, `uses-export`, `references-cross-owner-contract`,
`contains-source`, `routes-child-namespace`, `routes-terminal-module`,
`facades-child-export`, and `facades-implementing-source`. The payload contains
no separate dependency-certificate hash.

Containment and route/facade topology are certificate dependencies in v5:
module certificates depend on contained sources, parent namespace routes
depend on the routed child module, and facades depend on both the child export
and the implementing terminal source. A `uses-export` dependency targets the
exact behavioral source implementing the export. Runtime admission separately
requires the current exporting-module certificate, so boundary identity and
access remain protected without making every consumer depend on every source in
that module.

The certifier identity contains its exported interface and version, its node
hash, and its source commit. `certification_basis_hash` is the single digest
for all other certification machinery: the node-input policy, certifier,
schemas, hashing and safety implementation, checks, binding compilers, and
machine evaluators. There is no separate policy, schema, or checker hash in the
certificate. A change to any basis component changes this one digest.

Gateway-language, gateway-machine, runtime-dependency, and platform claims are
audited for blueprint correctness through versioned entries in `checks`. The
certifier does not test or record the performance, installed versions, or host
availability of those machines and dependencies; ordinary tests own those
questions.

Python route-smoke dependency tracing is another certifier-owned mechanical
check. For the selected dependency closure, each discovered implementation
file or invoked interface must map to a directly owned certification input, an
explicit certification dependency, or the certification basis. The certifier
runs the scoped trace twice before any certificate append and requires the
mapped signatures to agree. It records only the passed versioned check.
Manifest and node-hash derivation are pure: drift and currentness never execute
gateways or rerun route smoke.

Each node has one append-only certificate log. Every appended complete entry
contains `payload` and `signature`, and the signature covers the canonical
encoding of `payload` only. The first entry has `previous_entry_hash: null`;
each later entry hashes the canonical complete preceding entry, including its
signature. The last complete valid entry is the current certificate and earlier
entries are history. The chain detects modification, middle-entry removal, and
reordering within retained history. Without an external anchor it cannot prove
that the newest entries were removed.

## Currentness and drift

A certificate is current only when all of the following hold:

- its envelope and payload validate and its signature verifies under `key_id`;
- its subject and source commit identify the current node state;
- its input manifest resolves safely and every digest matches current bytes;
- its recorded node hash equals the reconstructed node hash;
- its dependency set exactly matches the derived direct dependencies, every
  target version and node hash matches, and every dependency certificate is
  current;
- its `certifier` fields match the current certifier;
- its `certification_basis_hash` equals the current basis hash;
- its signed checks exactly match the current versioned check registry and all
  are recorded as passed without findings;
- its history link agrees with the retained preceding entry.

A missing or malformed field, signature, dependency, or input makes the node
suspect. A suspect certificate is retained. It becomes current again without
reissuance if all currentness conditions later return to the recorded values.
Reissuing an unchanged certificate does not change the node hash or semantic
interface projections.

In dependency-first pseudocode:

```text
is_current(x):
    certificate = read_current_certificate(x)
    return (
        valid_signature(certificate)
        and safe_manifest_matches(certificate.payload.input_manifest)
        and certificate.payload.node_hash == node_hash(x)
        and certificate.payload.dependencies == current_dependency_claims(x)
        and all(is_current(d) for d in direct_dependencies(x))
        and certificate.payload.certifier == current_certifier_identity()
        and certificate.payload.certification_basis_hash
            == current_certification_basis_hash()
        and certificate.payload.checks == expected_passed_checks()
        and valid_history_link(certificate)
    )
```

When drift exists, the certifier runs its owned check scripts and semantic review.
After each repair it discards the prior review snapshot, reloads the blueprint
and graph, and reruns the checks. Only after discrepancies are resolved does it
reconstruct the manifest, node hash, dependencies, basis hash, and checks
internally, sign the canonical payload, append the complete signed record, and
verify the append before reporting success. Runtime performance and host
availability remain outside certification. The certifier never signs a
caller-supplied certificate payload.

## Authority and security boundary

`skill-certifier` is the sole supported writer for blueprint repair,
certificate signing, and the append-only certificate log. `skill-drift` is
read-only and verifies through the public-key path.
No broker, service identity, second writer, or parallel signing route is
introduced. Atomic no-follow writes, user-only permissions, history, and
post-write verification remain defense in depth.

Secure atomic writes are the default. A caller may explicitly opt into the
existing non-atomic fallback when the host cannot provide the secure primitive;
the certifier never selects that fallback silently.

The dispatcher derives one repository-backed certification view. In ordinary
operation it admits only exports whose module and implementing source have
current certificates. The same view admits exact self-certification of
`skill-certifier` when its certification closure has no history or has
appendable history: every existing log in the closure must be canonical,
schema-valid, signature-valid, and unbroken, although its final signing key may
be inactive, and existing logs must form a dependency-first prefix of the exact
closure. An empty closure history or a valid partial prefix may resume through
this path. Corrupt history, a non-prefix gap, a wrong-subject entry, or missing
verification material fails closed. The only uncertified mechanical subcall
admitted is the existing read-only blueprint synchronization check.

This is a cooperative same-user contract, not filesystem isolation between
same-UID processes. A malicious process running as the same OS user may access
signing material or certificate outputs. Signatures and currentness detect
drift, corruption, and changes outside the supported writer contract; they do
not defend against that attacker.
