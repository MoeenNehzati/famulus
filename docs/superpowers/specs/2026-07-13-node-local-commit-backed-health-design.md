# Node-Local Commit-Backed Artifact Health Design

Status: approved (design phase). Date: 2026-07-13.

## Purpose

Revise the typed blueprint and artifact-health prototype so that
`skill-audit` produces reusable, node-local, commit-backed certifications.
Certification must remain recursive without turning every audit into a new
whole-graph snapshot. The schema family remains the complete machine-readable
source for blueprint creation and validation.

This design incorporates the independent audits of schema sufficiency,
shared-source behavior, target isolation, write atomicity, incremental
recertification, pooled review, and dispatcher consistency.

## Goals

- Keep one canonical `blueprint.yaml` as each skill graph root.
- Keep subordinate blueprints file-backed and file-only.
- Make `skill-audit` the only writer of every health record.
- Permit `skill-audit` to certify any node reached from the selected root.
- Reuse healthy child certifications without rechecking their Git status.
- Stamp only node-local inputs that match a restorable Git commit.
- Complete semantic audits even when a node is not stamp-worthy.
- Preserve unchanged health records byte-for-byte.
- Make shared behavior-source certification independent of the consumer root.
- Make exact-target audit and drift operate on exactly one root plus its
  reachable closure.
- Keep pooled reviews derived and non-authoritative.
- Make all creation and validation requirements discoverable from the schema
  family, including references to their validator implementations.
- Use only Python standard-library primitives for hashing, authentication, Git
  subprocesses, and atomic filesystem replacement.

## Non-Goals

- Do not commit health records, pooled reviews, or the local HMAC key in phase
  one.
- Do not design a portable certification ledger yet.
- Do not introduce external key custody or public-key signatures yet.
- Do not execute code from a copied target installation while auditing it.
- Do not add behavior-source allow lists.
- Do not permit directory-backed blueprints.
- Do not make pooled artifacts inputs to canonical graph health.

## Terminology

- **Root:** the canonical skill-level `blueprint.yaml`.
- **Node:** a skill, LLM interface, machine interface, or behavior source.
- **Local inputs:** files whose bytes contribute directly to one node's local
  hash. They do not include child nodes' files.
- **Child:** a node reached through a declared interface or behavior-source
  edge.
- **Consumer root:** the skill root from which traversal began. This term does
  not confer write authority or ownership.
- **Stamp:** an authenticated health record written by `skill-audit`.
- **Stamp-worthy:** semantically acceptable and eligible for a commit-backed
  stamp.
- **Refresh-required:** a node whose existing record cannot be reused even if
  the node remains semantically acceptable.
- **Audit-run evidence:** diagnostic output from the current invocation,
  including raw command output and durations. It is not node health.

## Chosen Architecture

Health is a set of independently valid node certifications rather than a
whole-graph snapshot. `skill-audit` traverses bottom-up. A parent depends on
the authenticated certified-health hashes of its direct children. It does not
re-run Git checks for an already healthy child.

Alternative designs were rejected for phase one:

- Whole-graph snapshots would rewrite healthy descendants and reintroduce
  consumer-specific shared records.
- An external ledger would make certification portable but requires a separate
  trust, storage, and synchronization design.

## Certification Authority

Only `skill-audit` writes health files. Filesystem location determines node
identity and behavior-source visibility, but it does not restrict
`skill-audit`'s authority to certify a reached node.

Invoking `skill-audit` for skill A may therefore stamp an interface or
behavior source associated with skill B when that node is in A's valid
reachable closure. The evidence used for that stamp must be intrinsic to the
node. It must not depend on A being the traversal root.

## Graph Visibility And Identity

### Interfaces

LLM and machine interfaces may declare cross-skill interface dependencies by
canonical interface ID and pinned version. Hand-authored LLM instructions name
the interface to use; they do not invoke a skill by bare skill name or reach
into another skill's `_rtx` or `_cx` files.

The blueprint edge records the dependency. Dispatcher access control remains
an interface-level concern.

### Behavior sources

Behavior-source visibility follows repository location:

- A node owned by skill X may directly reference behavior files under
  `skills/X/`.
- Any skill may directly reference behavior files under repository-root
  `references/`.
- A node owned by skill X may not directly reference behavior files under
  `skills/Y/`.

Visibility is evaluated from the node declaring the edge, not from the
consumer root. Consequently, skill A may use an interface owned by skill B,
and B's interface may reference B-local behavior sources. A has not acquired
permission to declare a direct edge to those B-local files.

No behavior-source allow list is added.

Skill-local behavior-source IDs must use the owning skill namespace.
Repository-root shared behavior sources use the `references.source.*`
namespace. A sidecar whose declared namespace disagrees with its physical
owner location is invalid.

### Shared nodes and edges

A canonical node has one canonical sidecar, binding, identity, and health
path. Repository graph composition treats relationships as a set. The same
canonical edge contributed through multiple roots is represented once.
Duplicate relationships authored within one graph remain an error.

Targeted and repository-wide resolution must produce the same node and edge
hashes for the same reachable closure.

## Audit Selection And Isolation

An exact target selects exactly:

1. the requested skill root;
2. its reachable interface and behavior-source closure.

Unrelated skills are not loaded, validated, hashed, audited, or included in
post-write verification. A malformed unrelated skill cannot block an exact
target. A malformed reachable node must block the affected certification.

Repository-wide validation remains available only through an explicit
all-skills operation.

The trusted running certifier reads the target installation's:

- blueprint graph;
- schema family;
- audit-policy manifest;
- certifier and shared implementation files for hashing;
- node-local source files.

It does not execute target installation code. Target dependency discovery must
therefore use declared metadata or trusted static analysis rather than
importing target modules for route smoke.

No schema, policy, implementation, or dependency hash may silently come from
the running source repository when a different target installation was
selected.

## Audit Phases

### Phase 1: Prepare

For an exact target, `skill-audit`:

1. resolves the selected root and reachable closure;
2. validates every reachable blueprint against the target schema family;
3. computes target-relative schema and policy hashes;
4. locates the target Git repository, if present;
5. captures its full `HEAD` object ID;
6. checks schema and certification-policy inputs against that commit once;
7. creates an in-memory audit-run report.

If Git is absent or policy inputs are dirty, semantic auditing continues, but
no new node stamp may be written.

### Phase 2: Audit nodes bottom-up

For each node in deterministic postorder:

1. authenticate an existing health record before reading any field from it;
2. validate its health schema, identity, certifier, dependencies, and checks;
3. hash current node-local inputs;
4. verify direct child records and live child hashes;
5. run node-local semantic checks;
6. determine whether the existing record is reusable;
7. if refresh is required, determine stamp-worthiness;
8. immediately before stamping, verify local inputs against captured `HEAD`;
9. atomically replace the node health record;
10. make the resulting certified-health hash available to parents.

An already healthy child is reused without a Git check and without rewriting
its record. Its live files are still compared with its authenticated health
record. If the live state changed, the child becomes refresh-required and is
audited as the current node.

### Phase 3: Complete the root and pool

The root can be stamped only after every direct dependency has a current
authenticated stamp. After the root is current, tooling renders and checks the
pooled review independently.

Pool failure is reported but does not roll back valid canonical node stamps.

## Semantic Audit Without A Stamp

Audit and stamping are separate outcomes. A dirty or non-Git node still
receives its complete semantic result and expected live hashes.

The result shape includes:

```yaml
audit_result: passed
stamp_worthy: false
stamp_written: false
reasons:
  - node-local input differs from HEAD
```

`skill-audit` tells the user that certification can only be stamped from a
commit. A non-stamp-worthy child prevents dependent ancestors from being
stamped, but it does not prevent their semantic analysis.

## Commit-Backed Stamps

### Local cleanliness

Git cleanliness is checked only for the node currently being stamped.
Relevant paths are exactly the files that contribute to that node's local
hash:

- the node blueprint;
- its bound file, when present;
- additional declared files directly owned and locally hashed by that node.

Child interface and behavior-source files are excluded because they have their
own records. Generated health files and pooled artifacts are also excluded.

Every relevant local path must:

- be tracked by the captured commit;
- remain a regular, non-symlink file;
- have index and working-tree bytes equal to the captured commit;
- still match the bytes used to compute the proposed local hash.

Unrelated dirty files do not block the node.

### Commit identity

Every newly written health record contains:

```yaml
source:
  vcs: git
  commit: <full-object-id>
  input_paths:
    - <repository-relative-path>
```

The source metadata is authenticated by the record HMAC and record hash. It is
excluded from the stable certified-health hash so identical certified content
does not become semantically different merely because it was available in a
later commit.

The subject and sorted input paths identify what to restore from the recorded
commit. The existing local content hashes verify the restored bytes.

### Graphs may span commits

A reused child may name an older source commit than a newly stamped parent.
This is valid when the child's live inputs still match its authenticated
record. Each node remains independently restorable.

New stamps created in one invocation use the captured target `HEAD`. If
`HEAD` moves during the invocation, no further stamps are written and the
operation reports the interruption. Already completed node stamps remain
valid.

### Non-Git targets

A target without Git may be semantically audited and checked against existing
health, but it cannot receive a new stamp.

## Refresh Semantics

A node is refresh-required when:

- no health record exists;
- authentication or health-schema validation fails;
- subject, type, version, certifier, or dependency summaries disagree;
- a node-local semantic input changed;
- stable normalized check results changed;
- schema or certification-policy hashes changed;
- a child's certified-health hash changed;
- authenticated record metadata that should track live state, such as the raw
  blueprint-file hash, is outdated.

Refreshing a child does not automatically require every ancestor to be
rewritten. An ancestor requires refresh only when its own record is invalid or
its expected dependency projection changes.

A no-op audit preserves reusable health files byte-for-byte, including
timestamps, record hashes, HMACs, and mtimes.

## Check Evidence

Node certified-health hashes contain only stable normalized evidence:

- check ID and version;
- deterministic pass/fail result;
- stable structured findings;
- coverage identifiers when schema-defined.

Elapsed time, temporary paths, test counts containing timing, raw stdout, raw
stderr, and presentation text do not enter node certified-health hashes.
Those values remain available in the audit-run report.

Global mechanical gates determine whether the invocation may stamp. Their
stable gate-set identity belongs in policy metadata; their volatile output is
not copied into every node record.

Shared nodes therefore produce the same certified-health hash regardless of
which consumer root caused the traversal.

## Health Record Contract

Every canonical health record includes:

- health schema version and record type;
- node subject ID, type, version, blueprint path, and binding path;
- commit-backed source metadata;
- certifier interface identity and version;
- local, downstream, schema, policy, and certified-health hashes;
- direct dependency summaries;
- stable normalized checks;
- certification result and timestamp;
- record hash and HMAC-SHA-256 authentication.

Authentication is verified before schema validation or field use.
Unauthenticated content never influences an expected parent hash.

`certified_health_hash` excludes timestamps, HMAC, record hash, source commit,
and raw diagnostic output. `record_hash` authenticates the complete stored
payload.

## Atomic And Symlink-Safe Writes

Every generated health file and the HMAC key use the same write primitive:

1. open each existing parent directory without following symlinks;
2. reject a symlink final path;
3. create a unique temporary file in the destination directory;
4. write complete bytes and apply the intended mode;
5. flush and `fsync` the temporary file;
6. atomically replace the final filename;
7. `fsync` the directory where supported.

Temporary creation uses exclusive creation and no-follow flags where
supported. The implementation must not truncate the destination before a
complete replacement is ready.

Legacy `.last_audit.json`, typed node health, pooled artifacts, and key
creation all use this primitive.

Each node stamp is independently atomic. A valid child stamp is not rolled
back because a parent later fails. Multiple requested roots are also
independent transactions. The final result explicitly identifies successful,
semantically-passed-but-unstamped, and failed targets.

## Command Interface Rules

`_cx` contains direct executable command files, never inline shell commands.
For a command binding:

- `_cx` and every path component must be a real directory, not a symlink;
- the bound command must be a tracked regular non-symlink file;
- it must be physically located under the owning skill's `_cx/`;
- it must be executable;
- traversal and cross-skill direct path reach-through are invalid.

Git tracking is a source-repository validation rule. Dispatcher does not
require a `.git` directory at runtime.

Dispatcher does validate typed schema, graph relationships, access control,
path containment, file type, symlink prohibition, and executability before
execution. A schema-invalid interface cannot be dispatched merely because the
graph loader can parse it.

## Schema-First Authority

The normative schema family is sufficient to create and validate a complete
blueprint graph. It includes:

- JSON Schema field definitions for every blueprint and health type;
- a machine-readable relationship matrix;
- deterministic sidecar naming rules, including qualified sidecars;
- behavior-source visibility and namespace rules;
- file-only and non-symlink binding rules;
- `_rtx` and `_cx` layout and executability rules;
- source Git-tracking requirements;
- generated `SKILL.md` contract and interface-block requirements;
- pooled-review and health rules;
- template and synchronization behavior.

Rules that JSON Schema cannot enforce directly live in normative
`schema-meta.json` entries. Every such rule includes:

- a stable rule ID;
- complete creation guidance;
- validation semantics;
- the validator implementation path;
- regression-test paths;
- template behavior;
- audit-hash inclusion policy.

Implemented validators are referenced by these schema rules. Validators do
not create undocumented policy. A generator given only the schema family,
metadata, and templates must be able to produce artifacts accepted by every
referenced validator.

Standard schema resolution must work from the schema root without
tool-specific undocumented setup.

## Pooled Review

The pooled review remains a generated review artifact. It is healthy only
when:

- the canonical root is healthy;
- content exactly equals deterministic rendering from the current canonical
  graph and admitted health records;
- content validates against `pooled-review.schema.json`;
- the pooled file hash matches;
- pooled health is authenticated and names the expected root.

Pool content, pool health, and `pooled-review.schema.json` are excluded from
canonical graph schema hashes. Changing or deleting a pool cannot change root
health.

Pool-only damage is repaired without rewriting reusable canonical health
records.

## Ignored Local Certification State

Phase one keeps these artifacts Git-ignored:

- `.last_audit.json`;
- node `.*.health.json` sidecars;
- pooled review and pooled health files;
- the local HMAC key.

They do not participate in Git cleanliness checks. Their source commit fields
make certified node inputs restorable, but the certification files themselves
do not travel with clones. Losing them loses cached certification state, not
source history; the recorded source commit can be audited again.

Whether to commit health files or introduce a separate portable ledger is
explicitly deferred.

## Audit Result Model

The machine-readable result distinguishes:

- `semantic_status`: `passed` or `failed`;
- `stamp_worthy`: boolean;
- `stamp_status`: `reused`, `written`, `not-written`, or `failed`;
- `health_status`: `healthy`, `refresh-required`, `unstamped`, or
  `unhealthy`;
- stable reasons and affected node IDs;
- raw audit-run evidence outside node health.

Post-write verification must confirm the exact requested root and every newly
written node. It must not accept an unrelated nonempty list of healthy skills.
Pool status is reported separately from canonical root status.

## Failure Handling

- Dirty local inputs: complete semantic audit; do not stamp the node or
  dependent ancestors.
- No Git repository: complete semantic audit; do not create new stamps.
- Dirty schema or policy bundle: complete semantic audit; do not create any
  new stamps in that invocation.
- Missing or stale child stamp: recursively audit the child.
- Child semantically valid but unstampable: continue parent semantics; do not
  stamp the parent.
- Malformed reachable blueprint: fail that reachable branch and its
  dependents.
- Malformed unrelated skill: ignore it for an exact target.
- Node write failure: leave the prior complete record intact.
- Pool write or validation failure: report pool unhealthy; keep canonical
  stamps.
- Later target failure: retain earlier target results and report partial
  success explicitly.

## Required Regression Coverage

### Shared nodes

- Two roots reach one repository-root behavior source and obtain one canonical
  health identity.
- Different consumer-root audit logs do not change the shared node hash.
- Sequential audits leave both roots healthy.
- Shared behavior-source edges are deduplicated across roots.
- Skill-local and repository-root namespace/location mismatches are rejected.
- Behavior-source-to-behavior-source cycles are rejected.

### Commit-backed incremental behavior

- Dirty current node receives semantic results but no stamp.
- Dirty child prevents parent stamping without preventing parent semantics.
- Healthy child is reused without a Git-status check.
- A child from an older commit remains reusable when its live inputs match.
- Exact local input path changes are detected in the index and working tree.
- Unrelated dirty files do not block stamping.
- No-Git targets are semantic-only.
- A moving `HEAD` stops further writes.
- No-op audits preserve bytes and mtimes.
- Leaf changes refresh the leaf and only required ancestors.
- Volatile command output does not change certified-health hashes.

### Target isolation

- `--skill-root` returns exactly one root plus reachable closure.
- Unrelated malformed skills do not block exact-target audit or drift.
- Reachable schema-invalid nodes do block certification.
- Schema, policy, implementation, and dependency hashes come from the target.
- Target code is not imported or executed.
- Typed hash output exposes graph-native root and interface dependency hashes.

### Write safety

- Legacy and typed final symlinks are rejected without modifying their
  targets.
- Symlink parent components and parent-swap attempts cannot redirect writes.
- Interrupted writes leave the previous complete record or no final record.
- Interrupted key creation is retryable.
- Atomic replacement covers node, root, pool, and pooled-health files.
- Multi-target partial success is explicit.
- Post-write verification matches the requested target identity.

### Schema and runtime agreement

- A graph generated solely from schema metadata and templates passes every
  referenced validator.
- Every schema-valid relationship is accepted by validators, or the schema
  rejects it.
- Complete sidecar naming is derivable from schema metadata.
- Generated contract-block requirements are machine-readable.
- Dispatcher rejects schema-invalid typed sidecars.
- Runtime does not require Git tracking.
- `_cx` symlinks, traversal, untracked source bindings, and non-executable
  commands are rejected at their appropriate layers.

### Pooled review

- Arbitrary authenticated bytes are not a healthy pool.
- Schema-invalid or noncanonical pools are unhealthy.
- Pool-only changes do not affect root health.
- Pool-only damage is repaired without canonical restamping.

## Acceptance Criteria

- Exact audit touches only the selected root and reachable closure.
- Every newly stamped node has clean local inputs and a restorable source
  commit.
- Existing healthy children are reused without Git traversal.
- Shared-node health is independent of the consumer root.
- Dirty and non-Git nodes receive complete semantic results but no stamp.
- No-op audits perform no health-file writes.
- Generated writes are atomic and cannot follow final or parent symlinks.
- Source-repository validators require tracked `_cx` files; dispatcher does
  not require Git.
- Schema metadata completely accounts for validator-enforced behavior.
- Pooled artifacts remain exact, authenticated, repairable, and
  non-authoritative.
- Health files and keys remain ignored local state.
- The permanent tests cover every independently reproduced audit finding.
