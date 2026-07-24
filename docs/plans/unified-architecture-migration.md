# Unified Node Architecture Migration

> **Status:** Closure candidate. Implementation and Linux verification are
> complete; native Windows verification and exact-commit certification remain.

## Goal

Move the repository to the `module` and `behavioral_source` architecture in
`docs/architecture.md` by generalizing existing owners, not by building
parallel replacements.

## Non-duplication rule

The migration retains one owner for each concern:

| Concern | Live owner |
| --- | --- |
| Schemas | `references/blueprint/` |
| Inventory and graph | `src/officina/common/blueprint_inventory.py` and `blueprint_graph.py` |
| Hashing and provenance | `certification_hashing.py` and `git_provenance.py` |
| Certification | `skills/skill-certifier/` |
| Currentness and drift | `certification_view.py` and `skills/skill-drift/` |
| Interface binding | `process_binding_compiler.py` |
| Projection and pooled review | `interface_projection.py` and `pooled_blueprint.py` |
| Dispatch and runtime | `src/officina/dispatcher/` and `src/officina/runtime/` |
| Migration | `interface_injection_migration.py` with the thin `scripts/migrate-blueprints-v4.py` CLI |
| Authoring and enforcement | `skill-maker`, validators, generated standards, and the blueprint hook |
| Dependency installation | `install-assistant-tools` consuming the generated runtime-dependency manifest |

No second graph, hash pipeline, certifier, drift judgment, interface DTO,
binding compiler, projection, dispatcher, migration engine, installer
dependency model, or validation authority is authorized.

## Adopted contracts

### Nodes and interfaces

- The only node kinds are `module` and `behavioral_source`.
- Skills are autodiscoverable modules whose gateway convention is `SKILL.md`;
  `skill` is not a node type or schema profile.
- Modules own containment, authority, discovery, exports, and access policy.
- Behavioral sources own gateways, intrinsic interface contracts, dependencies,
  inputs, outputs, effects, and actions.
- Interfaces are source-owned contracts. Module exports bind public identity
  and access to those contracts without copying them.
- Containment assigns ownership but creates no certification dependency.
- Selecting a module for certification selects its contained sources; selecting
  an exact source does not select its parent.

### Gateways and runtime

- A gateway is one whole existing file. Provider-specific entry selectors are
  process-binding mechanics, not gateway identity.
- The common interface contract describes semantic use across gateway
  languages. The existing process binding describes argv/stdin, entry
  selection, output framing, exit signals, and cancellation.
- Dispatcher resolution uses the graph-resolved module root and therefore does
  not assume `skills/<module-id>`.
- Route smoke remains a certifier-owned mechanical dependency audit and does
  not become a parallel conformance framework.

### Hashing, certification, and drift

- The certifier derives inputs from direct ownership, Git state, mandatory
  blueprint/gateway/contract closure, and the ordered project policy at
  `references/certification/node-hash-policy.yaml`.
- Certificates, signing material, histories, logs, caches, and generated state
  are not ordinary hash inputs. Project policy may deliberately include
  eligible ignored or untracked directly owned regular files.
- One `certification_basis_hash` covers the input policy and all other
  certification machinery. There is no separate policy hash.
- `skill-certifier` is the sole writer. It reconstructs payloads internally and
  appends complete Ed25519-signed entries to one history per node.
- `skill-drift` is read-only and shares the graph, hash, basis, and currentness
  implementations. It does not rerun semantic review or issue state.
- Admissibility and conformance are certifier checks, not separate authored
  authorities. Ordinary tests remain development verification.
- Certification is bound to one explicit reviewed repository and commit.
  Installed-source and host-plugin discovery is read-only drift diagnostics and
  does not extend the package's certifiable graph.

### Platforms and installation

- The existing `atomic_files.py` owner implements POSIX descriptor-relative and
  Windows handle-relative, reparse-point-safe operations.
- Secure atomic/no-follow behavior is the default. The non-atomic fallback is
  available only through explicit caller opt-in and is never selected silently.
- Native Windows tests remain the release-support gate for Windows-specific
  handles, ACLs, reparse points, locking, and replacement behavior; those tests
  are ordinary platform verification, not certificate machine evidence.
- The generated runtime-dependency manifest retains dependency kind, name,
  version, reason, and platform applicability. The existing installer filters
  dependencies for the current platform before merging versions.

## Adoption evidence

The detailed disposition record is
`docs/plans/unified-architecture-migration-map.yaml`. The retained converter and
active-reference checker provide reproducible migration evidence.

| Phase | Result |
| --- | --- |
| 0. Freeze authority and inventory | Existing owners, security rules, callers, plans, installed-source classes, and dispositions recorded; overlapping plans deferred for approved post-adoption rebase. |
| 1. Schemas and hash policy | V4 module/source schemas, one common caller contract, one process binding, gateway requirements, direct ownership, ordered input policy, certificate schema, and one basis hash adopted. |
| 2. Graph and runtime | One generic graph, ownership model, exports, dependencies, dispatcher, projection, pooled review, search, hashing, and runtime path adopted. |
| 3. Certifier | Existing writer converted to signed append-only certification with shared read-only drift, explicit fallback, Windows primitives, self-certification admission, and certifier-owned checks. |
| 4. Mechanical conversion | Sole migration engine maps legacy declarations into structurally valid v4 candidates and proves active-reference and projection reconciliation. |
| 5. Atomic cutover | Live blueprints, schemas, callers, validators, hooks, generated artifacts, runtime, certification, drift, and installer switched together; legacy runtime authorities removed. |
| 6. Adoption cleanup | Old node/interface/health/conformance authorities removed; architecture and certificate documentation updated; deferred plans now require an approved post-adoption rebase. |

Implementation history includes the schema, runtime, certification, conversion,
cutover, cleanup, and certifier-renewal commits from `063997c` through
`aa8cef7`. The final closure diff corrects remaining integration gaps found by
post-adoption review: pooled contained-source coverage, generic module-root
dispatch, platform-aware installation, explicit non-atomic certification,
single-root caller schema, case-insensitive legacy scanning, and bounded
inventory performance.

## Final closure

- [x] Migration map validates and the active-reference scan reports zero live
  legacy references.
- [x] The final graph has only `module` and `behavioral_source` nodes, authorized
  exports and dependencies, and an acyclic certification projection.
- [x] Focused schema, graph, dispatcher, installer, certifier, pooled-review,
  migration, and inventory regressions pass on Linux.
- [x] Architecture, certification, checkout/plugin boundary, Windows
  implementation, and deferred-plan documentation match the live owners.
- [ ] Run the exact final verification ladder:

  ```bash
  scripts/migrate-blueprints-v4.py --check-map --check-active-references
  python3 scripts/run-python-tests.py --suite full --verbose
  python3 validators/runner.py
  bash .githooks/skill/check-blueprints
  git diff --check
  git diff --cached --check
  ```

- [ ] Run the native Windows test job for the exact final commit.
- [ ] Commit the exact reviewed state, certify that commit's complete repository
  graph, and require clean drift.
- [ ] Make no tracked edit after exact-commit certification. Any later status or
  documentation commit requires fresh certification.

The plan becomes complete only when every closure item above is satisfied for
the same final commit.
