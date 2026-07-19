# Legacy Requirements Crosswalk

This document preserves the earlier plan content without allowing historical
and target architectures to coexist normatively.

Disposition meanings:

- **active** — retained in the target design;
- **historical evidence** — completed work or observed behavior used by migration;
- **superseded** — retained here with its replacement and reason;
- **deferred** — intentionally outside this implementation sequence.

## July 18 sample-resolution plan

| Source section | Disposition | Target location |
|---|---|---|
| Global constraints: evidence-backed behavior, certification external, unsafe behavior explicit | active | README constraints; ADM/EXE rules |
| Global constraint: inspect only two samples | historical evidence | migration plan sample fixtures; no longer repository-wide scope |
| Tasks 1-2: compute-hashes and watermark contracts | historical evidence | migration fixtures and conformance corpus |
| Tasks 3-4: `/tmp` reading artifacts and focused verification | historical evidence | examples are illustrative; old `/tmp` files remain non-authoritative |
| Task 5: v3 vocabulary and dispatcher prefix separation | active | MOD/BND decisions |
| Task 5 accepted shared-gateway error | superseded | machine modules own shared gateways; migration must eliminate the error |
| Task 6: path/file/dir, flat syntax, selection cardinality and symlinks | active | ARG-003/004/011 and contract design |
| Task 7: `syntax`, `flag`, value-bearing boolean | active | ARG-004/005 |
| Task 8: machine-module boundary and nested exports | active | MOD-001..005 |
| Task 8: simple-interface invariant | active | IFC-001..006 |
| Task 8: module/interface ownership and direct tool union | active | IO-001..003, DEP-001..003 |
| Task 8: internal tmp/log exception | active | IO-003 |
| Task 8: dispatcher ordering and hook sentence | active | BND-005, HOOK-001 |
| Task 8: effect/lifecycle axes and canonical renames | active | EXE-001/004 |
| Task 8 `requires_serialization`, `idempotent_with_key`, `resume` and companion keys | superseded | DEF-004 and narrowed EXE-003 vocabularies |
| Task 8 module-level fixed `--dry-run` example | superseded | dispatcher owns `--dry-run`; use an implementation-only fixed option |
| Task 8 certification checks | active and expanded | complete admissibility/certification design |

## July 17 caller-contract implementation plan

| Source section | Disposition | Target location |
|---|---|---|
| Combined minimized session vocabulary and dispatcher options | active | HOOK-001 |
| No profiles; future inherited network behavior comment | active/deferred | DEF-001 |
| v3 `node_type`, `gateway`, `content`, `invocation_binding` | active | MOD/BND design |
| Structurally complete contract; no draft/unresolved/certification fields | active | ADM schema rules |
| `calls`, call-scoped helpers, visibility, accepts, constraints | superseded | IFC-001..004; helpers are interface-local |
| Schema/sample/design/verification tasks | historical evidence | implementation plans replace unchecked original steps |

## Consumer-local injection design

| Source section | Disposition | Target location |
|---|---|---|
| Goal/current problem/selected canonical-YAML approach | active | injection design |
| Blueprint inventory and strict parsing | active | INV-001/002 |
| Recursive terminal argument contracts, numeric bounds, and units | active | ARG-001..007/010 |
| Helper mapping/routing/freshness/failure/cycle/secret rules | active | DEP-003/004 and contract design |
| Call modes, selectors, guards, visibility, accepts, constraints | superseded | simple nested interfaces with interface-local authorization |
| Outputs and outcomes | active | contract design and ADM output/outcome checks |
| Preconditions, effects, execution guarantees | active with renamed fields | EXE and contract design |
| Dynamic default resolvers | superseded | DEF-005: helper-backed explicit arguments, fixed exports, or separate interfaces replace hidden runtime defaults |
| Operational wait/cost warnings | active | OUT-003 interface-wide `caller_warnings` |
| Per-effect reversibility | active | EXE-004 tagged `reversibility`; distinct from rollback on failure |
| Generic policy objects and older opaque names | superseded | explanatory tagged choices and EXE-004 names |
| Canonical caller-contract examples based on `machine-interface.calls` | superseded | examples/machine-module.yaml |
| Compact selected YAML and minimal schema vocabulary | active | INJ-001, HOOK-001 |
| Cross-repository review findings | active where behavior remains | verification fixtures and migration evidence |
| Generated artifacts | active | deterministic consumer-local blocks and one session context |
| Consumer-local dependency selection | active | INJ-002/003 |
| Recursive inclusion of helper interfaces | active, bounded | DEP-004; helper edges only, with cycle and size rejection |
| Authored instruction boundary | active | generated blocks only in owning LLM gateways |
| Validation and test coverage | active and decomposed | verification matrix and implementation plans |
| Full prose interface renderer | superseded | selected canonical YAML; no second interface language |
| Blueprint-local draft/unresolved fields | superseded | external admissibility diagnostics and certification |
| Access-control fields in selected YAML | superseded | authorization/certification runs before projection; access control is always omitted |
| Relevance-filtered direct-I/O projection | superseded | all three normalized direct-I/O lists of the selected simple export are retained so references remain closed |
| Concrete caller name substituted into the global hook | superseded | generic `<skill>` is correct because one session can load multiple caller skills |
| Endpoint/protocol fields for every long-running interface | narrowed | require service metadata only when the export actually exposes a service endpoint; lifecycle readiness/stop remains universal |

## Interface metadata refactor plan

| Source section | Disposition | Target location |
|---|---|---|
| Interface-first metadata | active | nested interface ownership; module only shared facts |
| One interface description as user outcome | active | IFC-005 |
| Controlled vocabularies | active | closed schemas and admissibility catalog |
| Semantic content separate from format | active | contract/direct-I/O design |
| Path roots | active, narrowed | MIG-003 closed `relative_to`/resource migration and ownership/path validation |
| Direct I/O immediate-only | active | IO-001 |
| Behavior sources versus operational inputs | active | module behavior sources; interface direct I/O |
| Immediate versus transitive I/O | active | authored direct, analytical transitive only |
| Proposed old interface shape/schema sketches | superseded | machine-module/nested-interface schemas |
| `role`, `kind`, display/search vocabularies, category compatibility, pilot migration, grouped search, and graph/docs deliverables | deferred | DEF-003; `docs/plans/interface-metadata-refactor.md` remains the authority for that independent workstream |
| medium/access/system/content/format/auth vocabularies | active where used by direct I/O; broader docs work deferred | direct-I/O schema and standards |
| Schema update rules and validator expectations | active | canonical schema metadata + rule catalog |
| Inferred skill-level views and documentation/graph uses | active analytical output | graph/injection design |
| Migration process and pilot learnings | historical evidence plus active fixtures | migration plan |
| Same-skill shared state open question | resolved | module-shared ownership |
| Remote logical ownership/open logical filesystem | deferred | DEF-002 |

## Session decisions made after the appended plan

| Decision | Disposition | Target location |
|---|---|---|
| Module and interface both have `uses_interfaces`; effective set is direct union | active | DEP-001/002 |
| `uses_interfaces` means tools, not platform support | active | DEP-001 |
| `direct_io` is interface-local; ownership can be module-shared or interface-private | active | IO-001/002 |
| tmp/log exception belongs in schema docstrings/docs, not hook | active | IO-003 |
| Dispatcher does not serialize/dedupe/resume calls | active simplification | narrowed EXE-003, no dispatcher consequences |
| Interface admissibility must be mostly machine-runnable without pretending semantics are static | active | full admissibility design |
| Standard and field names should maximize standalone clarity | active | decision ledger and closed rule catalog |

## Explicitly rejected or deferred paths

- Current behavior profiles or transport profiles: deferred (`DEF-001`).
- Logical filesystem standardization: deferred (`DEF-002`).
- External CLI documentation standards as schema authority: deferred; they may
  inform later import/export adapters without replacing the blueprint model.
- Mapping/object terminal types: rejected; structured data belongs in encoded
  stdin/file schemas.
- `__init__` as a default Python module gateway: deferred and not required.
- Machine-module/gateway ownership redesign beyond the selected v3 model: no
  longer a separate problem; the module is the gateway owner.
