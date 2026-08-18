# Skill Certifier LLM Interface Design

Status: Implemented

## Goal

Decompose `skill-certifier` semantic review into three internal instruction
interfaces and schedule only the stale semantic units identified by facet-aware
drift.

## Responsibility map

| Location | Responsibility |
|---|---|
| `SKILL.md` | Discoverable gateway and drift-selected, dependency-first certification algorithm. It identifies exact causes, selects the applicable audit interface, widens only on `needs-context`, and invokes the declared mechanical certification interface only after semantic review succeeds. |
| `blueprint.yaml` | Registers the gateway and three audit sources; its module content selector contains all four instruction files so source ownership can assign each audit file to its source. |
| `instructions/audit-interface.md` | Audit one source interface: its contract, binding, selected content, direct interface dependencies, effects, and behavior. Changed files are evidence supplied to this audit, not independent certification subjects. |
| `instructions/audit-behavioral-source.md` | Audit one behavioral source as the composition of its interface results, source-wide declarations, gateway, remainder content, and source dependencies. |
| `instructions/audit-module.md` | Audit one module as the composition of its module declaration, exports, namespace authority, and already-reviewed child nodes. |
| `blueprints/gateway.yaml` | Register the gateway's uses of drift status, all three audit interfaces, and the existing mechanical certification interface. It declares no synthetic default interface. |
| `blueprints/instructions-*.yaml` | Own one instruction file and one intrinsic LLM interface each, with prompt input/output contracts. |
| `_rtx/` | Continue to own validation, hashing, stale-node selection, route smoke, signing, append-only writes, and post-write currentness verification. No semantic LLM rules move into runtime code. |
| `skills/skill-drift/_rtx/` | Project canonical v6 currentness into exact file, facet, and direct-dependency deltas plus a dependency-first stale worklist. Dependency deltas cover interface and non-interface relations. |
| `skills/skill-drift/blueprints/gateway.yaml` | Advance its exact direct dependency pin to the rewritten certifier gateway source version and describe exact file/facet/dependency causes plus the stale worklist. |
| `docs/officina/docstring.md` | Replace examples that named the removed default export with the new intrinsic interface-audit id. |

## Interface surface

The module has no public instruction export. Skill discovery enters through
`SKILL.md`; the gateway uses these module-private source interfaces:

- `skill-certifier.source.audit-interface.interface.audit`
- `skill-certifier.source.audit-behavioral-source.interface.audit`
- `skill-certifier.source.audit-module.interface.audit`

There is no file-audit interface. Exact file changes are deterministic inputs
to the interface or node audit that owns them. There is no remainder-audit
interface: remainder content is part of the behavioral-source audit.

## Certification algorithm

1. Resolve the exact requested target, hold the reviewed repository state
   stable, and invoke drift status for an exact stale worklist.
2. Map each changed file, interface, and dependency cause to its owning facet
   and traverse only affected facets and ancestors dependency-first.
3. Audit stale leaf interfaces, then affected behavioral-source ancestors using
   those results and source-level evidence.
4. Audit each affected module only after its affected child nodes have review
   results. Reuse unrelated facet evidence only when its claim is authenticated
   by the latest valid signed certificate and still matches canonical state.
5. Do not issue a certificate when an audit rejects the subject, reports an
   unresolved evidence gap, or requires wider context that has not been read.
6. Invoke the deterministic certification interface for the exact reviewed
   repository and commit. It recomputes currentness, skips current nodes,
   route-smokes the stale worklist, and remains solely responsible for
   reconstructing and signing certificate payloads.

## Current behavior

Selective evidence reuse is implemented at the semantic-review boundary. Drift
names the stale worklist and exact file, interface, or dependency causes; the
gateway audits only affected leaf facets and their source/module ancestors.
Unchanged evidence is reused only from a matching facet claim authenticated by
the latest valid signed certificate. The mechanical certifier independently
recomputes currentness and skips current nodes, so the gateway's worklist cannot
authorize an unsafe append.

## Invariants

- One signed certificate continues to cover a whole node and all of its facets.
- Only deterministic runtime code signs or writes certificate history.
- Internal audit interfaces produce evidence and verdicts; they never sign.
- A narrow audit may request wider context, ultimately widening to the whole
  source or module.
- Generated `SKILL.md` contract/interface blocks are synchronized from
  blueprints rather than edited by hand.
