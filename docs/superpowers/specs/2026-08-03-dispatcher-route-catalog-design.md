# Dispatcher Route Catalog Design

**Status:** Superseded by `2026-08-04-fast-dispatcher-design.md`.

This document records the former self-rebuilding cache design. It is not the
current runtime contract.

## Objective

Make repeated dispatcher resolution a bounded cache read instead of repeated
repository inventory, graph validation, and certification derivation.

## Diagnosis

`PythonMachineInterface.dispatch()` calls `_resolve_dispatch()` without a graph
or certification view. `_resolve_export_dispatch()` consequently inventories
the repository, attempts a full graph, builds a closure-scoped fallback when an
unrelated blueprint is invalid, and derives repository certification state for
every invocation. Separate dispatcher processes repeat the same work.

## Design

The dispatcher owns a versioned, route-scoped catalog in the user cache. A
route key contains the canonical repository root, caller module, and target
interface. Version and host/nested checks are replayed from the cached graph;
they are not separate cache identities. Each entry stores:

- the validated closure graph and unrelated-blueprint diagnostics;
- the final route certification decision;
- fingerprints for every file whose contents determined the entry; and
- the catalog format version.

The representation is JSON with an allow-listed decoder for dispatcher graph
and certification dataclasses. It never evaluates cache content as code.

On lookup, the dispatcher verifies the entry's dependency fingerprints. A
matching entry is loaded without blueprint discovery, schema validation, Git
inspection, or certificate hashing. Lookup distinguishes `hit`, `missing`,
`stale`, `malformed`, and `unavailable`. Any non-hit is rebuilt through the
existing canonical loaders and atomically replaces only that route. Stale state
is never used. Route-scoped generation preserves the existing rule that defects
proven unrelated to the requested closure are warnings rather than global
blockers.

Host and nested calls use the same catalog API; no caller-chain context is
propagated and hop-local authorization remains unchanged.

After a successful rebuild, the resolved invocation includes a
`dispatcher-catalog-rebuilt` warning naming the lookup status. A failed graph
or certification cache write includes `dispatcher-catalog-write-failed` while
leaving an otherwise valid invocation executable. Cache hits emit neither
warning. Authorization, certification, and blueprint diagnostics retain their
existing codes and severity.

## Safety and invalidation

Graph entries fingerprint their blueprint documents and the concrete schemas
used to validate them. Certification entries fingerprint the certification
basis, certificate logs, and node input manifests. An unavailable
certification result is conservative and may be reused only while the graph
inputs remain unchanged.

Malformed, unsupported, incorrectly rooted, or stale cache entries are ignored
and rebuilt. Catalog writes are atomic and contain no credentials or gateway
arguments.

## Scope

The change is limited to a dispatcher catalog module, dispatcher resolution,
focused tests, and dispatcher architecture documentation. Gateway execution,
package snapshot isolation, authorization semantics, blueprint schemas, and
caller identity rules do not change.

## Verification

Tests must prove JSON round-tripping, typed missing/stale/malformed/unavailable
lookup results, route separation, rebuild/write warning behavior, warning
preservation, and that a second resolution succeeds when repository graph and
certification loaders are replaced by failures. A live benchmark compares two
identical dry runs and reports resolution time without including gateway or
network execution.
