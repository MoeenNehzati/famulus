# Explicit Standard Query Design

Date: 2026-08-05

## Problem

`refactor-node.interface.query-standards` currently accepts a repository node or
path, scans the repository blueprint graph, resolves ownership, selects one or
more standard roots, validates their import closures, and projects the requested
records. This combines policy selection with policy querying.

The caller already has the context needed to select the relevant standard. The
query service should not reconstruct that context from every repository
blueprint. In the benchmark worktree, collecting 218 blueprint documents took a
median 0.486 CPU seconds and accounted for approximately 99.6 percent of the
ownership-resolution cost. It also caused an unrelated invalid blueprint to
block a standards query.

## Goals

- Make the caller explicitly select one canonical root standard.
- Return records from the root and its complete pinned transitive import closure.
- Remove all blueprint discovery and ownership resolution from the query process.
- Give `refactor-node` and `skill-maker` complete instructions for selecting and
  querying the relevant standard roots.
- Preserve requirements, context, evidence, remedies, full, and generic record
  query capabilities.
- Preserve fail-closed validation of the selected standard closure.

## Non-goals

- Do not infer a standard from a target path, node ID, filename, or blueprint.
- Do not make the query validate unrelated blueprints or unrelated standards.
- Do not remove validation of pinned revisions, digests, schemas, semantics, or
  imported documents.
- Do not update archival implementation plans that record the former interface.
- Do not select or omit tests to meet a performance target.

## Public interface

Create `common.interface.query-standard` as a process-bound interface owned by
the shared `common` module. Its first version accepts:

```text
dispatcher --caller-skill <caller> common.interface.query-standard \
  <standard-path> \
  [--repo-root PATH] \
  [--facts-json JSON] \
  [--view requirements|context|evidence|remedies|full] \
  [--refs-json JSON] \
  [--query-json JSON]
```

`standard-path` is the canonical repository-relative path to the selected root
standard. A path is used instead of a standard ID so the query can open the root
directly without building an ID index. The loaded document's canonical path and
standard ID must agree with its contents; otherwise the query fails.

The interface is read-only. It does not accept a target node or repository path
whose ownership must be inferred.

## Query behavior and result

The query:

1. Opens the selected root standard directly.
2. Follows every pinned import transitively.
3. Validates the schema, standard semantics, pinned revision, version, digest,
   and import relationships for every unique document in that closure.
4. Evaluates applicability using root domain facts plus caller-supplied facts.
5. Returns the selected projection.

Every successful result contains:

- the canonical root standard path and ID;
- effective facts;
- closure metadata for the root and every transitively imported document;
- projected records carrying their originating document ID;
- the selected view.

If multiple import paths reach the same standard, that document is validated,
listed, and extracted once. The closure order is deterministic. A view or filter
may return no matching records, but it never removes imported documents from the
closure metadata.

The requirements, context, evidence, remedies, full, refs, and generic-query
semantics remain those of the current query interface. Partition overlays and
cross-partition record interning are removed because one invocation has exactly
one root closure.

## Ownership and implementation placement

The generic process interface belongs to `common`, beside the existing
`common.interface.standard-extractor` Python API. A small process adapter owns
view parsing and JSON output and delegates closure loading and record extraction
to that API.

The adapter must not import blueprint inventory or blueprint graph modules.
`refactor-node` no longer owns a standards query runtime, facade, or public query
export. Its instruction interfaces consume `common.interface.query-standard`
directly.

`skill-maker` also consumes `common.interface.query-standard` directly rather
than reaching through `refactor-node`.

## Refactor-node selection contract

Before querying, `refactor-node` establishes the selected scope from the user's
request and the artifacts already under review. It classifies each scope by its
known node role and gateway family, then uses this fixed matrix:

| Selected scope | Root standard |
|---|---|
| Python module | `references/node-standards/python-module.standard.yaml` |
| Python behavioral source | `references/node-standards/python-behavioral-source.standard.yaml` |
| Instruction module | `references/node-standards/instruction-module.standard.yaml` |
| Instruction behavioral source | `references/node-standards/instruction-behavioral-source.standard.yaml` |

For a mixed request, `refactor-node` partitions the work using its available
context and invokes the query once for each applicable root. It combines the
resulting requirements in its reasoning; the query service does not combine
owners or infer partitions.

`refactor-node` supplies `task.kind=refactor`, establishes
`task.affects-executable-behavior` from the requested operation, and supplies
other material facts it actually knows. Unknown material facts remain unknown
and are handled from the returned applicability results.

Follow-up context, evidence, and remedy calls reuse the same root path and facts
as the requirements call and pass exact returned document/ref pairs.

## Skill-maker selection contract

`skill-maker` queries standards component by component:

- A skill gateway or parent skill module uses
  `instruction-module.standard.yaml`.
- A separately registered instruction behavioral source uses
  `instruction-behavioral-source.standard.yaml`.
- A Python runtime module uses `python-module.standard.yaml`.
- A registered Python behavioral source uses
  `python-behavioral-source.standard.yaml`.

Creating a schema-minimum skill begins with the instruction-module query. The
additional roots are queried only when the proposed skill actually contains
those components. This prevents both under-querying and querying irrelevant
standards.

`skill-maker` supplies `task.kind=author-skill`, establishes
`task.affects-executable-behavior`, and supplies known facts such as personal
override or repository-validator status when applicable. It follows the same
root-preserving requirements/context/evidence/remedy sequence as
`refactor-node`.

## Errors

The query fails with a specific error when:

- the root path is absent, outside the repository, noncanonical, or not a
  standard document;
- the root or an imported document violates the schema or semantic validator;
- an import pin has the wrong ID, version, revision, digest, or artifact path;
- an import cycle or conflicting identity is found;
- refs are outside the selected closure;
- facts contradict the root's declared domain facts;
- view and ref/query arguments are incompatible.

An unrelated blueprint or standard outside the selected closure cannot affect
the result.

## Migration

This is an intentional public-interface replacement:

1. Add and test `common.interface.query-standard`.
2. Update `refactor-node` and `skill-maker` declarations and authored
   instructions to use it.
3. Update active authority metadata and consumer tests.
4. Remove `refactor-node.interface.query-standards` and its private runtime
   ownership after both consumers migrate.
5. Regenerate blueprint-derived contract blocks and runtime-dependency
   manifests.

No compatibility wrapper is retained because accepting a target would preserve
the repository-wide ownership behavior being removed. Historical plans remain
historical and may continue to show the old command.

## Verification

Focused tests must prove:

- the root and all direct and transitive imports appear in results;
- a shared transitive import is loaded and returned once;
- records retain their originating document IDs;
- all supported views and generic queries preserve closure metadata;
- stale pins, invalid digests, cycles, contradictory facts, and out-of-closure
  refs fail closed;
- the query succeeds when an unrelated blueprint is invalid;
- the query process never calls blueprint collection or ownership resolution;
- `refactor-node` declares the four-root selection matrix and queries each root
  needed by a mixed scope;
- `skill-maker` starts with the instruction-module root and adds only roots for
  components it actually authors;
- no active declaration or instruction depends on the retired interface;
- generated blueprint artifacts are synchronized;
- the exhaustive precommit gate still runs every group without fail-fast.

Performance verification records cold and warm CPU time, wall time, peak RSS,
and call counts for one root requirements query and the six-view sequence. The
first acceptance criterion is structural: zero blueprint inventory calls and
one load/validation per unique standard document. Timing improvements are then
reported against the existing benchmark rather than encoded as flaky unit-test
thresholds.
