# Certification and Drift

For an introduction to retained assurance, see [Getting
Started](getting-started.md). The
[Architectural Principles](architectural-principles.md) govern that role;
[Blueprints](blueprints.md) explains the declarations being assessed, and
[Schemas](schema.md) explains what structural validation can and cannot prove.

## Assurance model

Five ideas must remain distinct:

1. **Structural validity** means an artifact has the permitted machine-readable
   shape and satisfies mechanically expressible structural rules.
2. **Semantic accuracy** means the artifact truthfully and completely describes
   the behavior or policy it represents.
3. **Certification** retains mechanical results and semantic review evidence
   for one exact repository state.
4. **Currentness** means that retained evidence still matches every state and
   dependency on which it relies.
5. **Drift** is a relevant mismatch that makes the retained assurance suspect;
   it is not by itself proof that the changed state is wrong.

The lifecycle is:

```text
validate structure -> review meaning -> certify exact state
                   -> compare current state -> report drift or remain current
```

Schema validity does not establish semantic truth. Certification combines the
mechanical and semantic questions; currentness then determines whether the
recorded answer still applies.

Read the remaining contract in four parts:

1. **Graph and certifiability:** nodes, dependencies, structural validity, and
   semantic completeness.
2. **Identity of reviewed state:** input policy, manifests, Git provenance,
   node hashes, and facets.
3. **Retained evidence:** certificate payloads, signatures, checks, history,
   and the certification basis.
4. **Ongoing assurance:** currentness, drift selection, issuance order, and
   the authority and security boundary.

This document defines the live version-6 certification contract.

Certification is repository-bound. The public certifier requires an explicit
reviewed repository and commit and derives the complete certifiable graph from
that checkout. Installed-source adapters are read-only drift diagnostics;
installing a plugin does not add its modules to the package's certification
graph.

## Nodes and dependencies

Version 6 has two authored node types:

- A `module` owns discovery, filesystem authority, contained behavioral
  sources, and exported interfaces.
- A `behavioral_source` owns one whole-file gateway, content and interface-use
  envelopes, intrinsic interface contracts, and source dependencies. Each
  explicit interface claims content and interface-use subsets of those
  envelopes.

The blueprint graph derives certification dependencies from source use,
private-interface use, module-export use, namespace routing, topology
proofs, and cross-owner contract references. Containment assigns ownership but
adds no certification edge. An edge records the target node and its exact
version. A node's local hash does not recursively include dependency bytes;
certificates record direct dependency node hashes separately.

Semantic-audit ordering uses a separate neutral projection,
`officina.certification-dependency-dag/v1`. It contains every module,
behavioral source, and interface facet. In addition to facet-attributed direct
uses, it orders an interface before its owning source, a source before its
owning module, and a child module before its parent. It omits evidence-only
`certified-under` relations and contains no audit or worker state.

Every authored node has one blueprint. Every behavioral source has one
whole-file gateway. A module with discovery also has a whole-file gateway; a
dependency-only module may omit discovery. Blueprint declarations are graph
authority. Certificates and generated review artifacts report graph state but
never add graph edges.

## Structural validity and certifiability

A version-6 blueprint may be structurally valid before it is certifiable.
Structural validation requires canonical identity, a resolvable whole-file
gateway, containment and relationship shape, safe paths, and closed shapes for
every semantic value that is present. It does not manufacture semantic facts
or assert that an interface is complete.

Structural validation does not invent permissive argv rules, generic success
outcomes, or other guessed defaults merely to satisfy a schema. Missing
descriptions, contract sections, invocation details, direct-I/O facts, or
compatibility claims are certifier findings.

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
[`references/certification-policy/node-hash-policy.yaml`](../../references/certification-policy/node-hash-policy.yaml)
and validates it through the central `src/officina/configuration/schema.json`.
The [certification policy directory](../../references/certification-policy/)
contains the policy and its retained historical contract. The historical
`references/certification-policy/node-hash-policy.schema.json` remains in the
certification basis for existing records but is not the active runtime
validator. The canonical policy has this shape:

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

For a version-6 behavioral source, each explicit interface has a local hash
covering its canonical declaration and selected input manifest. Unclaimed
source state forms a remainder facet. `node_hash(x)` aggregates the remainder
and sorted interface local hashes. It excludes certificates, history, generated
status artifacts, and dependency content. Changing an interface input changes
that interface facet and the aggregate source hash; changing unclaimed input
changes the remainder and aggregate. Changing only a dependency makes the
owning facet suspect through its dependency claim without changing its local
hash or the aggregate source hash.

## Certificate format

`references/blueprint-schema/certificate.schema.json` defines a closed envelope:

```yaml
payload:
  certificate_schema_version: 3
  subject:
    id: example-skill._rtx.source.runtime
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
  facets:
    - id: example-skill._rtx.source.runtime
      type: remainder
      local_hash: sha256:...
      input_manifest: []
      dependencies: []
    - id: example-skill._rtx.source.runtime.interface.run
      type: interface
      local_hash: sha256:...
      input_manifest:
        - path: skills/example-skill/_rtx/runtime.py
          digest: sha256:...
          git_provenance: tracked
      dependencies: []
  certification_basis_hash: sha256:...
  certifier:
    interface: node-certify._rtx.interface.certify
    version: 2
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
`node_hash`. Version-6 dependency relations are `uses-source`,
`uses-private-interface`, `uses-export`, `references-cross-owner-contract`,
`contains-source`, `routes-child-namespace`, and `routes-terminal-module`. The payload contains
no separate dependency-certificate hash.

Version-6 certificates use payload version 3 and include canonical facet
claims. Payload versions 1 and 2 remain readable only as historical formats;
they are not current version-6 certification state.

Containment and route topology are certificate dependencies in version 6:
module certificates depend on contained sources, parent namespace routes
depend on the routed child module. A `uses-export` dependency targets the exact
behavioral source implementing the export. The exporting-module certificate
separately records boundary identity and access without making every consumer
depend on every source in that module. Live dispatcher authorization reads the
relevant blueprints directly and treats certificate currentness as advisory.

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
check. For the stale issuance worklist, each discovered implementation
file or invoked interface must map to a directly owned certification input, an
explicit certification dependency, or the certification basis. The certifier
runs the scoped trace twice before any certificate append and requires the
mapped signatures to agree. It records only the passed versioned check.
Manifest and node-hash derivation are pure: drift and currentness never execute
gateways or rerun route smoke. Nodes already current are neither route-smoked
nor appended.

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
- its subject identifies the current node, and its tracked inputs are clean and
  reproducible at HEAD; `source_commit` records issuance provenance and need
  not equal the current HEAD;
- its input manifest resolves safely and every digest matches current bytes;
- its recorded node hash equals the reconstructed node hash;
- its facet set, local hashes, input manifests, and facet dependency claims
  exactly match the reconstructed facets;
- its dependency set exactly matches the derived direct dependencies, every
  target version and hash matches, and every ordinary recursive dependency
  certificate is current;
- for a certifier-bearing schema-v6 graph, its `certified-under` interface
  dependencies exactly match the current mechanical certifier and the semantic
  audit interface for each module, source remainder, or interface facet;
  generic graphs without structured evidence retain the aggregate certifier
  identity check;
- its `certification_basis_hash` equals the current basis hash;
- its signed checks exactly match the current versioned check registry and all
  are recorded as passed without findings;
- its history link agrees with the retained preceding entry.

A missing or malformed field, signature, dependency, or input makes the node
suspect. A suspect certificate is retained. It becomes current again without
reissuance if all currentness conditions later return to the recorded values.
Reissuing an unchanged certificate does not change the node hash or semantic
interface projections.

Drift projects these comparisons into an exact stale worklist. For interface
and remainder facets it distinguishes local-hash, input-manifest, and direct-
dependency mismatches. Input-manifest causes name added, removed, and changed
files. Dependency causes cover all direct facet dependencies: interface uses
name the interface id, while other relations name their relation and target.
The evidence-only `certified-under` relation identifies the exact mechanical,
interface-audit, source-audit, or module-audit interface whose hash changed;
it participates in hash comparison and drift reporting but not recursive
currentness, cycle, postorder, or route-smoke traversal. Old or partial v6
certificates report missing structured dependencies and require recertification;
there is no compatibility fallback to the certifier module hash.
Certificate, basis, graph, or other non-facet concerns remain node-scoped. This
worklist is diagnostic evidence for selective bottom-up semantic review, not
authority to sign.

In dependency-first pseudocode:

```text
is_current(x):
    certificate = read_current_certificate(x)
    return (
        valid_signature(certificate)
        and safe_manifest_matches(certificate.payload.input_manifest)
        and certificate.payload.node_hash == node_hash(x)
        and certificate.payload.facets == current_facet_claims(x)
        and certificate.payload.dependencies == current_dependency_claims(x)
        and all(is_current(d) for d in recursive_direct_dependencies(x))
        and project_certifier_identity(certificate.payload.certifier, x)
            == project_certifier_identity(current_certifier_identity(), x)
        and certificate.payload.certification_basis_hash
            == current_certification_basis_hash()
        and certificate.payload.checks == expected_passed_checks()
        and valid_history_link(certificate)
    )
```

`recursive_direct_dependencies` excludes `certified-under` evidence. For a
certifier-bearing schema-v6 graph, `project_certifier_identity` excludes the
aggregate certifier node hash because the structured interface records carry
that identity.

When drift exists, certification uses the stale worklist for selective
bottom-up semantic review. Exact interface drift selects that interface, its
source, and module ancestors; remainder drift selects its source and module
ancestors; an unattributed stale source conservatively selects all its
interfaces. A sole mechanical `certified-under` change selects no semantic
task. An unchanged facet reuses evidence only when its claim is authenticated
by the latest valid signed certificate and still matches canonical state. The
signature covers the facet manifest and dependencies plus the certificate's
whole-node semantic-review pass. Selective reuse interprets that pass as
covering each included unchanged facet; it is not an independent per-facet
semantic attestation.

Code, not the orchestrating LLM, traverses this graph. The
`node-certify._rtx.interface.semantic-audit-scheduler` keeps one locked run
state and returns only dependency-ready task IDs, kinds, bounded input-file
handles, and counts. Each input identifies the exact repository and assigned
vertex without exposing the full DAG. The orchestrator fills a bounded pool
with one fresh subagent per task, submits each exact
`node-certify.semantic-audit-result/v1` report, and refills available slots.
Workers audit only their assigned vertex; they must not recursively audit,
schedule, or delegate dependencies. Missing or inconsistent prerequisite
evidence produces `abort`, and any malformed, rejected, aborted, lost, or
failed task terminates the run before signing.

Only a scheduler-complete run reaches the mechanical certifier. The mechanical
certifier then independently reloads current repository state, skips current
nodes, route-smokes the stale issuance worklist, and issues certificates
dependency-first. Semantic scheduling therefore reduces LLM context without
weakening final freshness checks.

Selectivity applies only while `certification_basis_hash` matches. Because the
aggregate hash does not identify which basis input changed, a basis mismatch
remains semantically invalidating until the separate node/interface-only basis
migration. Semantic audit instruction-body changes are the current localized
case; basis-listed mechanical, gateway, blueprint, hashing, and view inputs
remain global.
After each repair the certifier discards the prior review snapshot, reloads the
blueprint and graph, and reruns the checks. It then recomputes currentness,
skips nodes already current, route-smokes the remaining stale worklist, and
issues those nodes dependency-first. Only after discrepancies are resolved does
it reconstruct the manifest, node hash, dependencies, basis hash, and checks
internally, sign the canonical payload, append the complete signed record, and
verify the append before reporting success. Runtime performance and host
availability remain outside certification. The certifier never signs a
caller-supplied certificate payload.

## Authority and security boundary

`node-certify` is the sole supported writer for blueprint repair,
certificate signing, and the append-only certificate log. `node-drift` is
read-only and verifies through the public-key path.
No broker, service identity, second writer, or parallel signing route is
introduced. Atomic no-follow writes, user-only permissions, history, and
post-write verification remain defense in depth.

Secure atomic writes are the default. A caller may explicitly opt into the
existing non-atomic fallback when the host cannot provide the secure primitive;
the certifier never selects that fallback silently.

Dispatcher authorization is independent of certificate currentness. If a
preverified in-memory status view is available, dispatcher consults only the
caller ancestry, crossed target namespaces, terminal module, and implementing
source and emits bounded diagnostics. Missing, stale, expired, malformed, or
unavailable status is warning-only: it cannot grant authority, deny an
otherwise valid route, trigger certification work, or cause repository reads.

Certification workflows retain their own fail-closed rules. In particular,
self-certification may resume only from empty history or a canonical,
signature-valid, unbroken dependency-first prefix of the exact closure.
Corrupt history, a non-prefix gap, a wrong-subject entry, or missing
verification material blocks certification even though it does not block an
otherwise authorized dispatcher route.

This is a cooperative same-user contract, not filesystem isolation between
same-UID processes. A malicious process running as the same OS user may access
signing material or certificate outputs. Signatures and currentness detect
drift, corruption, and changes outside the supported writer contract; they do
not defend against that attacker.

## Related documentation

- [Overview](README.md)
- [Getting Started](getting-started.md)
- [Architectural Principles](architectural-principles.md)
- [Blueprints](blueprints.md)
- [Schemas](schema.md)
- [Certification policy](../../references/certification-policy/)
