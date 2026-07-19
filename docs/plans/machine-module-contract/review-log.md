# Consolidation Review Log

This log records review scope and material resolutions. It is not a second
design authority; the ledger and normative documents remain authoritative.

## Iteration 1: preservation and architecture

Independent reviews audited the accumulated plans for lost decisions,
contradictions, schema enforceability, and execution order.

Resolved:

- separated completed prototype/sample evidence from unimplemented target work;
- made `machine-module` the gateway/certification owner with nested public
  exports and separate runtime/certification edge projections;
- retained interface-local direct I/O, module/interface ownership, direct tool
  unions, helpers, dispatcher ordering, execution axes, and deferred work;
- reused `schema-meta.json` as the rule catalog rather than creating a parallel
  authority;
- separated admissibility, semantic certification, and operational health;
- added a crosswalk so every historical section has an explicit disposition.

## Iteration 2: gates, APIs, and claim boundaries

Independent reviews tested the first consolidation for runnable APIs and false
machine-enforcement claims.

Resolved:

- defined strict inventory, normalized graph record classes, and two-stage
  caller parse/gateway compile APIs;
- required structural validity for indexing/public dispatch and current
  certification for module dispatch/injection;
- narrowed static rules to facts they can prove and assigned semantic facts to
  certification;
- made consumer locality a prompt boundary, not a claimed dispatcher capability;
- removed legacy call/profile/dynamic-default structures;
- added explicit recursive sensitivity, helper-backed enum, mutation,
  long-running, warning, reversibility, and ownership examples;
- defined SessionStart core/optional budgets and deterministic overflow.

## Iteration 3: implementation blockers and example conformance

Three final-area audits checked graph completeness, certificate bootstrap,
conformance safety, projection self-containment, direct-I/O consistency, and
requirement traceability.

Resolved:

- restored ordinary authored node edges alongside export/helper/derived
  certificate edges and named each traversal;
- integrated the audit-to-certification source contracts, exact private
  self-certification bootstrap, and certificate-backed view;
- required an enforceable Python conformance boundary and denied effect
  claims based on manifest declarations alone;
- specified exact skill-root conformance locators and completed the example
  parser, stream, effect, helper, startup, stop, cleanup, and observation cases;
- made every caller-visible output reference compatible immediate direct I/O;
- replaced string-encoded effect references with discriminated objects;
- specified literal directory ownership and narrowed secret/tmp/log runtime
  claims to demonstrated implementation/sandbox evidence;
- defined a closed, self-contained injection projection with digest-bound
  embedded definitions and actionable cross-skill LLM routes;
- expanded the requirement ledger and one-ID-per-row verification matrix.

## Iteration 4: currentness and protocol refinement

Targeted re-review of the repaired areas produced further refinements:

- certificate currentness now rehashes the manifest and transitive referenced
  definition closure;
- Python conformance uses one schema-checked boundary-operation registry and
  stable error model;
- contract-derived binding probes are generated, while non-inducible outcomes
  receive explicit not-applicable evidence and mandatory semantic review;
- dynamic filesystem arguments and direct I/O link through typed references;
- effect/outcome occurrence lists are exact inverses;
- the mutation example uses `stop` for uncertain completion rather than
  pretending a same-call read is a reliable post-failure verifier;
- projection limits are separated into per-export certification and
  per-consumer injection limits.

One review recommendation was rejected after checking the consolidated
historical contract: stdout/stderr are immediate direct I/O in the existing design, so the
new examples keep output-to-direct-I/O links instead of redefining direct I/O to
exclude process streams.

## Iteration 5: historical-preservation audit

A section-by-section comparison against the three transient source drafts and
the existing interface-metadata refactor plan found five material invocation
semantics that had been compressed too far. The package now
restores numeric bounds/units, filesystem match cardinality and fixed symlink
semantics, recursive bounded helper closure with safe enum sources, exact
dry-run/repetition hook vocabulary, and deterministic legacy path-root
migration. Older access-control projection, selective direct-I/O projection,
concrete global caller substitution, and universal service endpoint metadata
are explicitly superseded or narrowed in the crosswalk rather than silently
reintroduced.

After consolidation, the three transient source drafts were removed. The
decision ledger, normative designs, implementation plans, verification matrix,
and crosswalk are the complete retained record.

## Iteration 6: execution-scope audit

The package was re-audited as a target v3 contract redesign rather than a plan
to repair stale existing blueprint declarations.

Resolved:

- Phases 1-4 use target fixtures and live implementation/tests as evidence; only
  explicitly authorized Phase 5 writes live blueprint declarations.
- Phase readiness establishes prerequisites but never authorizes the next phase.
- Migration derives v3 modules from live Python interfaces and treats existing
  v2/prototype declarations only as hints.
- Command gateways are deferred; a later design may add tracked `_cx/`
  executables with a separately specified boundary.
- Phase gates and cross-cutting file/test scopes are explicit, and status is a
  reviewed draft rather than implementation-complete.

Pending design decisions:

- the exact `node_hash` versus certificate-currentness boundary;
- whether and how the schema-meta catalog evolves for the target rule model.

The package is not implementation-ready until those two decisions are resolved
and the affected normative text is reconciled.
