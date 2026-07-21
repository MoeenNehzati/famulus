# Task 1 Report: Define v4 schemas and certification inputs

Status: **DONE_WITH_CONCERNS**

## Work completed

- Staged direct-tested version-4 candidates without changing the live graph
  route:
  - `module.schema.json` for discovery, authority, source containment, and
    access-controlled exports;
  - `behavioral-source.schema.json` for whole-file gateways, source
    dependencies, interface uses, intrinsic contracts, and optional process
    bindings.
- Preserved `machine-module.schema.json`, `behavior-source.schema.json`, and
  their `schema.json` routes byte-for-byte through Task 4. Task 5 owns their
  atomic replacement.
- Generalized common gateway, requirement, locator, interface-ID, source-edge,
  and filesystem-reader definitions while retaining the version-3 skill root
  as an explicit migration input.
- Generalized `caller-contract.schema.json` into one source-owned semantic
  contract. Moved argv/stdin, entry selection, fixed parameters, output
  framing, exit signals, cancellation, and stop mechanics under
  `processBinding`; kept helpers and direct I/O in the semantic contract.
- Added the closed certificate schema with one signed payload, local Git
  provenance, direct dependency node hashes, one
  `certification_basis_hash`, certifier identity, checks, history linkage, and
  algorithm-explicit base64 signatures.
- Added the ordered project node-hash policy schema and canonical policy. It
  starts from tracked directly owned regular files, applies sequential
  last-match-wins Git-ignore rules, permits `require_match` only on includes,
  and records mandatory closure/reserved-output safety as non-configurable
  certifier invariants.
- Updated architecture, certification, authoring, template, schema metadata,
  relationship-matrix, and migration-plan documentation.
- Deferred interface projection and pooled review to Task 2 so each schema can
  change atomically with its existing producer and tests. This corrects an
  impossible schema/producer split without changing final migration scope.
- Changed no production graph, runtime, hashing, certification, or projection
  code.

## RED/GREEN evidence

Each behavior was introduced with a focused failing test before its schema or
artifact implementation:

| Behavior | RED evidence | GREEN evidence |
| --- | --- | --- |
| Requirement grammar | missing `requirement` definition (`KeyError`) | focused test passed |
| Whole-file gateway | missing `gateway` definition (`KeyError`) | focused test passed |
| Process binding | missing `processBinding` (`KeyError`) | focused test passed |
| Semantic/mechanical separation | semantic contract rejected required helpers/direct I/O | focused test passed after contract generalization |
| Behavioral source | v4 document rejected by predecessor schema | direct candidate test passed |
| Module | candidate schema absent | direct candidate test passed |
| Export access | false/empty allowlist was accepted | invalid combinations rejected |
| Generic filesystem readers | export interface ID failed legacy machine/LLM pattern | generic interface ID passed |
| Staged root isolation | live root accepted a v4 candidate during an intermediate route edit | live predecessors restored; both v4 candidates rejected by root and accepted directly |
| Ordered hash-policy schema | schema file absent | valid ordered rules accepted; traversal and exclude `require_match` rejected |
| Canonical hash policy | policy file absent | exact 14-rule exclusion list validated |
| Certificate | schema file absent | closed signed payload with untracked input provenance validated; duplicate legacy hash fields rejected |
| V4 relationship matrix | metadata key absent | exact staged matrix passed |
| Template manifest | `v4_examples` absent | exact module/source manifest passed |

The first full-suite run exposed the unsafe intermediate deletion boundary:
`78 failed, 1394 passed, 3 skipped`. Most failures cascaded from pre-v4 code
copying the removed predecessor schema. Restoring both predecessors and live
routing eliminated that cascade without production changes.

## Verification

- Focused Task 1 suite:
  `79 passed in 1.66s`.
- Affected predecessor/runtime suites after the staging correction:
  - artifact health: `56 passed`;
  - blueprint graph: `51 passed`;
  - skill audit: `45 passed`;
  - skill drift: `65 passed`;
  - total: `217 passed`.
- `python3 validators/runner.py`: exit 0; no output.
- `bash .githooks/skill/check-blueprints`: exit 0; no output.
- `git diff --check`: exit 0; no output.
- Predecessor/root exactness:
  `git diff --exit-code -- references/blueprint/machine-module.schema.json references/blueprint/behavior-source.schema.json references/blueprint/schema.json`:
  exit 0; no output.
- The three sandbox-only installer failures were rerun with network and local
  Git-config access: Codex GitHub install and the development lifecycle passed;
  the standing Claude plugin hook-response test remained the sole failure.

The full suite was run once as requested. It was not rerun after the staging
correction; all suites containing Task-linked failures were rerun and passed.

## Changed files

- `.superpowers/sdd/task-1-report.md`
- `docs/architecture.md`
- `docs/certification_and_drift.md`
- `docs/plans/unified-architecture-migration.md`
- `docs/plans/unified-architecture-migration-map.yaml`
- `references/blueprint/README.md`
- `references/blueprint/module.schema.json`
- `references/blueprint/behavioral-source.schema.json`
- `references/blueprint/certificate.schema.json`
- `references/blueprint/caller-contract.schema.json`
- `references/blueprint/common.schema.json`
- `references/blueprint/direct-io.schema.json`
- `references/blueprint/schema-meta.json`
- `references/blueprint/skill.schema.json`
- `references/blueprint/template.yaml`
- `references/certification/node-hash-policy.schema.json`
- `references/certification/node-hash-policy.yaml`
- `tests/test_blueprint_schema_metadata.py`
- `tests/test_officina_blueprint_template.py`
- `tests/test_typed_blueprint_schemas.py`

## Concerns

- The full suite retains its standing external Claude plugin failure: the
  GitHub marketplace and plugin install complete, but the unauthenticated
  session does not expose dispatcher context in the hook response. This is
  outside Task 1 and matches the pre-task standing-health concern.
- The v4 candidates are intentionally not production-routable yet. Task 2 owns
  graph/projection generalization, and Task 5 owns atomic live-schema cutover
  and predecessor removal.
