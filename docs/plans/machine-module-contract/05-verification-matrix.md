# Verification Matrix

Every active ledger requirement has one primary owner and one test family.
Secondary layers may reject earlier, but they do not redefine the rule. Each
implementation phase names the exact test files in its gate; changing those
files, requirement IDs, or primary owners requires updating the plan.

## Module and simple-interface structure

| ID | Primary owner | Mechanical/semantic check | Target tests |
|---|---|---|---|
| MOD-001 | module schema | module has nonempty export map | typed module fixtures |
| MOD-002 | module schema | module-owned field closure; no module `direct_io` | typed module fixtures |
| MOD-003 | export schema | interface-owned field closure | typed module fixtures |
| MOD-004 | certificate core | one module subject with per-export results | certificate sibling-export tests |
| MOD-005 | hashing/version comparison | shared drift invalidates certificate; public bump only on breaking export change | artifact-health and compatibility tests |
| MOD-006 | conformance schema/certificate | exact manifest locator, all exports covered, evidence digest bound | conformance and certificate tests |
| MOD-007 | graph model | edge classes retained and traversed separately | `tests/test_officina_blueprint_graph.py` |
| MOD-008 | hashing/certificate view | referenced manifest/definition closure participates in currentness | reference-drift and restoration tests |
| IFC-001 | semantic certifier | one coherent operation | single-operation fixtures |
| IFC-002 | caller-contract schema | no call-family/mode/constraint structures | typed negative fixtures |
| IFC-003 | semantic certifier | argument meaning/effect independence | argument-interaction fixtures |
| IFC-004 | schema/binding compiler | static argument defaults and fixed implementation choices only | typed and binding fixtures |
| IFC-005 | schema/semantic certifier | module/interface descriptions have correct scope | semantic exactness fixtures |
| IFC-006 | caller-contract schema | no `contract.title` | typed negative fixture |

## Binding and arguments

| ID | Primary owner | Mechanical/semantic check | Target tests |
|---|---|---|---|
| BND-001 | binding schema | interface/argument binding scopes are distinct | typed binding fixtures |
| BND-002 | semantic certifier | fixed binding contains no transformation/business logic | binding semantic fixtures |
| BND-003 | binding compiler | typed fixed values; no secret/global/override/collision | `tests/test_machine_interface_binding.py` |
| BND-004 | binding schema/compiler | four public variants and arity semantics | typed and binding tests |
| BND-005 | parser/compiler | public positional-first grammar and deterministic fixed merge | binding round-trip and dispatcher tests |
| BND-006 | dispatcher/binding validator | runner prefix and globals cannot enter public syntax | dispatcher-option negative tests |
| BND-007 | parser/compiler | unbounded-option language disjointness and standard `--` behavior | ambiguity and round-trip tests |
| ARG-001 | recursive type schema | closed terminal-kind vocabulary | one positive/negative fixture per kind |
| ARG-002 | recursive type schema | kind-specific recursive child name and shape | nested list/file/dir fixtures |
| ARG-003 | recursive type schema | sibling path/file/dir branches | filesystem-kind fixtures |
| ARG-004 | matcher/graph validator | syntax and pre-resolution symlink selection order | path-selection fixtures |
| ARG-005 | binding/type validator | flag only with switch; typed boolean spellings | flag/boolean fixtures |
| ARG-006 | schema/helper validator | inline enum xor one helper | enum/helper fixtures |
| ARG-007 | schema/static validator | recursive sensitivity and safe transport | sensitivity fixtures |
| ARG-008 | conformance/semantic certifier | platform-specific protected-file handling is evidenced | protected-file adapter and semantic fixtures |
| ARG-009 | schema/reference validator | filesystem argument/direct-I/O bidirectional typed linkage | dynamic path-source fixtures |
| ARG-010 | recursive type schema | ordered scalar bounds, in-range defaults, closed units, enum-only literal sets | numeric-bound/unit fixtures |
| ARG-011 | matcher/schema validator | match cardinality distinct from argv arity; fixed presented-path/symlink semantics | path-selection and symlink fixtures |

## Caller behavior, I/O, and execution

| ID | Primary owner | Mechanical/semantic check | Target tests |
|---|---|---|---|
| PRE-001 | schema/reference/semantic checks | stable check, outcome, action, and accurate interaction | precondition and interaction fixtures |
| OUT-001 | output schema | stable typed output including cardinality/empty semantics | output fixtures and conformance |
| OUT-002 | outcome schema/reference/semantic checks | closed cases, signals, refs, actions, substantive coverage | outcome fixtures and certifier |
| OUT-003 | schema/semantic certifier | interface-wide closed warnings, never argument-conditional | warning fixtures |
| OUT-004 | direct-I/O reference validator | every output references compatible immediate write | output/direct-I/O fixtures |
| OUT-005 | signal-overlap validator/semantic certifier | mechanical identical overlap; interpreted overlap review | signal overlap fixtures |
| DEP-001 | graph schema | `uses_interfaces` means runtime tool edge | relationship fixtures |
| DEP-002 | graph authority resolver | exact module plus selected-export direct union | sibling/transitive leakage tests |
| DEP-003 | helper graph validator | local bounded helper target is in effective direct set | helper routing/cycle tests |
| DEP-004 | helper closure/projector | recursive helper-only fixed point; read-only finite enum source | nested-helper, cycle, size, and enum-source fixtures |
| IO-001 | direct-I/O schema | immediate interface-local I/O only | direct-I/O fixtures |
| IO-002 | ownership graph validator | shared/private authorization and nonoverlap | ownership fixtures |
| IO-003 | schema docs/semantic certifier | narrow tmp/log omission rule | schema metadata and semantic fixtures |
| IO-004 | ownership matcher | literal directory subtree/exact file/full glob-regex/no escape | ownership matcher tests |
| IO-005 | static plus semantic/conformance boundary | authored confinement only unless sandbox coverage exists | path-static and sandbox-coverage tests |
| EXE-001 | execution schema | independent state-effect and lifecycle axes | all four combination fixtures |
| EXE-002 | execution schema | only caller-relevant execution facts; no dispatcher-consequence fields | typed negative fixtures |
| EXE-003 | schema/cross-field rules | one closed explained decision tag and valid retry relations | decision vocabulary fixtures |
| EXE-004 | schema/reference validator | closed effects, structured sources/evidence, verification, long-running fields | execution and reference fixtures |
| EXE-005 | cross-reference validator | outcome/effect occurrence relations are exact inverses | mismatched-relation fixtures |

## Inventory, projection, and hook

| ID | Primary owner | Mechanical/semantic check | Target tests |
|---|---|---|---|
| INV-001 | inventory parser | filesystem discovery, deterministic JSON-compatible strict parse, fail-before-yield | `tests/test_blueprint_inventory.py` |
| INV-002 | inventory parser | diagnostic skip covers parse errors only | inventory classification tests |
| INV-003 | inventory filesystem boundary | no-follow regular files and owner-root confinement | symlink/escape fixtures |
| INJ-001 | projection schema/serializer | normalized canonical YAML, no prose interface renderer | `tests/test_interface_projection.py` |
| INJ-002 | projection selector | per-consumer direct grants plus bounded helpers only | consumer/sibling leakage fixtures |
| INJ-003 | projection selector | provider runtime tools never become grants | provider-tool fixtures |
| INJ-004 | dispatcher/projection boundary | prompt locality does not claim runtime capability | helper guidance/authorization tests |
| INJ-005 | projection schema/resolver | exact field table, explicit defaults, embedded digest-bound definitions | projection schema/reference fixtures |
| INJ-006 | skill-maker syncer | exact markers/placement, atomic plan, byte-idempotence | `skills/skill-maker/tests/test_blueprint_tools.py` |
| INJ-007 | migration reporter | exactly one disposition per old union export | `tests/test_interface_injection_migration.py` |
| INJ-008 | LLM projection/hook | same-skill gateway xor cross-skill provider route | cross-skill LLM projection/host tests |
| INJ-009 | projection resolver/size gates | validation-equivalent closure; 12,288-byte export certification limit; 16,384-byte consumer injection limit | schema-pruning, independent-limit, and exact-boundary fixtures |
| HOOK-001 | SessionStart renderer | used vocabulary only, verified dispatcher options, 500/750 budgets | hook and all host-output tests |
| HOOK-002 | SessionStart renderer | exact repetition notation and verified dry-run semantics | hook notation/dry-run tests |

## Admissibility, certification, and migration

| ID | Primary owner | Mechanical/semantic check | Target tests |
|---|---|---|---|
| ADM-001 | profile/result schemas | admissible/certified/health distinction and canonical profile hash | catalog/result tests |
| ADM-002 | schema-meta catalog | stable versioned rule, exact blocks/owner/evidence/tests | catalog matrix tests |
| ADM-003 | graph index/dispatcher | structural failures block indexing and public dispatch; no bypass | dispatcher gate tests |
| ADM-004 | dispatcher/projection certificate view | current module and selected-export pass required | certificate-view and gate tests |
| ADM-005 | semantic runner | heuristics advisory-only | semantic advisory fixtures |
| ADM-006 | compatibility checker/semantic certifier | mechanical and ambiguous breaking-change classification | compatibility fixtures |
| ADM-007 | certificate projection retriever | exact predecessor reconstruction; first N/A; fail closed | source-commit retrieval tests |
| ADM-008 | conformance runner | adapter seam plus demonstrated sandbox coverage | Python boundary negatives |
| ADM-009 | certificate view/bootstrap | full certificate verification and exact-node private self-certification | certificate-view and certifier bootstrap tests |
| ADM-010 | conformance case generator/runner | generated binding boundaries and complete outcome case/N-A disposition | generated-case and outcome-coverage tests |
| MIG-001 | migration grouper | live-gateway grouping, explicit collision map, stable IDs | migration grouping/golden tests |
| MIG-002 | dispatcher migration gate | certificate gate for target v3 modules; no health promotion | target fixtures in Phase 4 and live migration tests in Phase 5 |
| MIG-003 | migration mapper | every legacy root maps to a typed base/resource; raw absolute paths rejected | legacy path-root migration fixtures |

Deferred/rejected `DEF-001`, `DEF-002`, `DEF-003`, `DEF-004`, and `DEF-005`
are checked by schema absence,
negative fixtures, and the Plan 5 zero-reference scan. They do not create
runtime validators for features intentionally outside the design.

## Required fixture coverage

Inventory fixtures cover roots plus unreachable sidecars, duplicate keys,
custom tags, non-string keys, nonmapping roots, non-JSON values, unreadable and
symlinked files, aggregate errors, and diagnostic skip classification. Graph
fixtures cover duplicate IDs, exact version pins, all edge classes, cycles,
authorization, platforms, ownership, helper routing, and authority leakage.
Binding fixtures cover every kind/arity, defaults, fixed values, stdin,
dispatcher collisions, `--`, and ambiguous unbounded positionals. Conformance
fixtures cover parser boundaries, streams, outcomes, mutation effects, helper
results, readiness/startup failure/stop/cleanup, and evidence scope.

## Phase and release gates

Phases 1-4 are independently reviewable infrastructure phases. Each must pass
the exact focused command in its implementation file plus `git diff --check`.
Their gates use target v3 fixtures and live implementation/tests as behavioral
evidence; stale existing blueprint declarations neither satisfy nor fail them.

1. Phase 1: schema, catalog, standard, fixture, and `check-blueprints` checks in
   `implementation/01-schema-standards-and-fixtures.md`.
2. Phase 2: inventory, graph, binding, dispatcher, template, and artifact checks
   in `implementation/02-inventory-graph-and-dispatcher.md`.
3. Phase 3: projection, syncer-fixture, migration-report, SessionStart, and host
   installation checks in `implementation/03-consumer-local-injection.md`.
4. Phase 4: admissibility, conformance, certificate, dispatcher, projection,
   certifier, drift, and artifact checks in
   `implementation/04-admissibility-and-certification.md`.
5. Phase 5: only after explicit user authorization, run its migration tests and
   the repository-wide validator, blueprint hook, sync check, standard checks,
   pre-commit hook, and exact-path diff review listed in
   `implementation/05-migration-docs-and-release.md`.

The implementation log records exact failures. Passing one phase establishes
only the next phase's prerequisites; it does not authorize that phase.
