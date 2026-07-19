# Machine-Module Contract Redesign

This directory is the consolidated draft authority for the machine-module,
caller-contract, consumer-local injection, and interface-admissibility work.
It incorporates the material content of the earlier transient plans and design
drafts; those drafts are superseded and are not retained separately.

## Status

The target design is consolidated but remains a reviewed draft until the
cross-cutting corrections recorded in this package are resolved. Implementation
has not started. The repository still contains the singular v3
`machine-interface` schema and stale earlier blueprint declarations; those
declarations are not design authority. The target v3 machine-module architecture
exists only in this plan package.

Implementation agents start at [IMPLEMENT.md](IMPLEMENT.md), not by reading the
entire package.

## Authority and reading order

1. [IMPLEMENT.md](IMPLEMENT.md) is the single execution guide and defines
   document precedence, phase workflow, stop conditions, and handoff evidence.
2. [01-decision-ledger.md](01-decision-ledger.md) fixes vocabulary, settled
   decisions, and deliberately deferred work.
3. [02-machine-module-contract.md](02-machine-module-contract.md) is the
   normative module and caller-contract design.
4. [03-inventory-graph-and-injection.md](03-inventory-graph-and-injection.md)
   is the normative discovery, graph, dispatcher, injection, and hook design.
5. [04-interface-admissibility.md](04-interface-admissibility.md) defines a
   proper interface, formal rule profiles, gates, conformance, and semantic
   certification.
6. [05-verification-matrix.md](05-verification-matrix.md) maps requirements to
   enforcement layers, check IDs, and test locations.
7. [implementation/README.md](implementation/README.md) indexes the five
   ordered phases. Each phase references normative requirement IDs rather than
   redefining fields.
8. [06-legacy-crosswalk.md](06-legacy-crosswalk.md) records earlier work as an
   active requirement, historical evidence, an explicit
   supersession, or a deliberate deferral.
9. [review-log.md](review-log.md) records the independent review iterations,
   material findings, and resulting design changes.

Examples under `examples/` illustrate the normative documents and introduce no
additional rules. `machine-module.yaml` is the compact read-only case;
`advanced-machine-module.yaml` covers mutating, long-running, helper-backed enum,
recursive file-content, warnings, and ownership branches. The injected example
preserves canonical module/interface/contract nesting;
`injected-llm-interfaces.yaml` shows the same-skill gateway versus cross-skill
canonical-routing boundary.

## Consolidated source material

The transient caller-contract sample plan, interface-resolution running plan,
and consumer-local injection draft have been fully absorbed into this package.
[06-legacy-crosswalk.md](06-legacy-crosswalk.md) records each material section
as active, historical evidence, superseded, or deferred; the source drafts are
intentionally removed to avoid competing authorities.

`docs/plans/interface-metadata-refactor.md` remains separate because its
role/kind/display/search workstream is deferred rather than implemented here.

## Implementation sequence

1. Freeze schemas, the machine-readable rule catalog, and negative fixtures.
2. Add strict filesystem inventory and normalize modules and nested exports.
3. Update graph resolution, ownership, authorization, and dispatcher lookup.
4. Add consumer-local selection and the minimized SessionStart vocabulary.
5. Add admissibility diagnostics, conformance probes, and certificate binding.
6. Only when Phase 5 is explicitly authorized, derive v3 blueprints from live
   skill/interface content and verified behavior, migrate generated
   documentation, and retire superseded declarations.
7. Run focused tests, full blueprint validation, synchronization, hook tests,
   standard checks, pre-commit, and diff-scope review.

## Global constraints

- Keep the legacy crosswalk aligned when a consolidated requirement is changed
  or deliberately superseded.
- Phases 1 through 4 do not preserve, repair, or migrate existing blueprint
  declarations. Live skill/interface content, tests, gateways, and observed
  behavior are implementation evidence the v3 design must support; stale
  blueprints are non-authoritative hints only.
- Phase 5 is never inferred from phase order or readiness. It is the only phase
  authorized to create or replace live blueprint declarations.
- Preserve unrelated dirty worktree changes and stage only exact files if a
  later commit is authorized.
- Once Phase 5 emits target v3 declarations, those authored blueprints are graph
  authority. Earlier declarations remain migration hints. Injected blocks,
  health reports, certificates, generated Markdown, and examples are derived
  artifacts.
- Dispatcher remains the only public machine-interface invocation boundary.
- No validator may claim a semantic fact that its evidence cannot establish.
- A public export that is structurally unsafe cannot enter the dispatcher index
  or dispatch.
- Public dispatcher execution and injection require current certification;
  private implementation tests may exercise gateways without creating a public
  dispatcher bypass.
