# Certification and Drift

This document defines the version-4 certification contract. The schemas are
staged before the version-4 graph and runtime cutover; the pre-v4 live schema
route remains authoritative until that cutover.

## Nodes and dependencies

Version 4 has two authored node types:

- A `module` owns discovery, filesystem authority, contained behavioral
  sources, and exported interfaces.
- A `behavioral_source` owns one whole-file gateway, its content selection,
  intrinsic interface contracts, source dependencies, and interface uses.

The blueprint graph derives certification dependencies from containment,
source use, private-interface use, module-export use, and cross-owner contract
references. An edge records the target node and its exact version. A node's
local hash does not recursively include dependency bytes; certificates record
the direct dependency node hashes separately.

Every authored node has one blueprint. Every behavioral source has one
whole-file gateway. A module with discovery also has a whole-file gateway; a
dependency-only module may omit discovery. Blueprint declarations are graph
authority. Certificates and generated review artifacts report graph state but
never add graph edges.

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
- audit and health records;
- pooled-review output;
- any other reserved certification output.

The canonical policy explicitly excludes logs, `log/` and `logs/`, Python
bytecode and `__pycache__/`, `.pytest_cache/`, `.cache/`, `_build/`, `build/`,
`dist/`, `.certificates/`, `.last_audit.json`, hidden health records, and
`.pooled-blueprint-review.yaml`. A later include may deliberately restore an
eligible runtime or generated regular file, but it cannot override mandatory
closure or reserved-output safety.

## Manifest, Git provenance, and node hash

The resolved `input_manifest` records each selected repository-relative
`path`, its `sha256` `digest`, and `git_provenance`: `tracked`, `ignored`, or
`untracked`. It does not record policy-internal details such as a final rule.

All tracked manifest entries and tracked blueprint data must match the
certificate's `source_commit`. Included ignored and untracked files are signed
local-state claims: their exact bytes must remain stable while certification
runs, but they need not be committed. Consequently, `source_commit`
reproduces the tracked subset only when a certificate includes local inputs.

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
  certificate_schema_version: 1
  subject:
    id: example-skill.source.gateway
    node_type: behavioral_source
    version: 1
    blueprint_path: skills/example-skill/blueprints/gateway.yaml
    gateway_path: skills/example-skill/SKILL.md
  node_hash: sha256:...
  source_commit: ...
  input_manifest:
    - path: skills/example-skill/SKILL.md
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
`node_hash`. Relations are `contains-source`, `uses-source`,
`uses-private-interface`, `uses-export`, or
`references-cross-owner-contract`. The payload contains no separate
dependency-certificate hash.

The certifier identity contains its exported interface and version, its node
hash, and its source commit. `certification_basis_hash` is the single digest
for all other certification machinery: the node-input policy, certifier,
schemas, hashing and safety implementation, checks, binding compilers, and
machine evaluators. There is no separate policy, schema, or checker hash in the
certificate. A change to any basis component changes this one digest.

The current certificate is authoritative for status. The complete signed
entry is also appended to history. The first entry has
`previous_entry_hash: null`; each later entry hashes the canonical complete
preceding entry, including its signature. History detects modification,
middle-entry removal, and reordering within retained history. Without an
external anchor it cannot prove that the newest entries were not removed.

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
        and valid_history_link(certificate)
    )
```

The certifier reconstructs the manifest, node hash, dependencies, basis hash,
and checks internally before signing. It never signs a payload supplied as
already validated by an LLM. It also verifies append and current-certificate
writes before reporting success.

## Authority and security boundary

The existing audit writer, migrated to `skill-certifier`, is the sole
supported writer for blueprint repair, certificate signing, and certificate
history. `skill-drift` is read-only and verifies through the public-key path.
No broker, service identity, second writer, or parallel signing route is
introduced. Atomic no-follow writes, user-only permissions, history, and
post-write verification remain defense in depth.

This is a cooperative same-user contract, not filesystem isolation between
same-UID processes. A malicious process running as the same OS user may access
signing material or certificate outputs. Signatures and currentness detect
drift, corruption, and changes outside the supported writer contract; they do
not defend against that attacker.
