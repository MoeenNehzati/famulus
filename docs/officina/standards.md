# Officina Standards

> **Status:** Nonnormative mechanism guide.
>
> The [architectural principles](architectural-principles.md) govern Officina.
> This document explains how its standards are represented, queried, and
> changed.

Officina standards answer a practical question: given this kind of node and
this kind of work, what must be true, why does it matter, how can we check it,
and what should we do if it is false?

A prose checklist can state some of those answers, but it cannot reliably
select the relevant ones, connect a rule to its evidence, or carry a change to
the documents that depend on it. Officina therefore represents standards as
structured semantic documents. The structure preserves the meaning and
relationships of a rule while allowing a query to return only what a caller
needs for the decision at hand.

This gives the system two useful properties at once:

1. A standard remains an inspectable, versioned authority rather than advice
   copied into each skill.
2. Skills can ask focused questions without loading or interpreting the whole
   standards collection on every task.

If nodes, blueprints, validation, and certification are unfamiliar,
[Getting Started](getting-started.md) provides background.

## 1. Standards are semantic authorities

The canonical node standards consumed by Officina's authoring and refactoring
skills are YAML documents under
[`references/node-standards/`](../../references/node-standards/). They state
rules for nodes, modules, behavioral sources, instruction nodes, Python nodes,
and more specialized combinations of those concepts.

The documents are layered. A specialized standard imports the standards on
which it depends. For example, an instruction-module query starts from the
instruction-module standard and receives its complete import closure, including
the more general module, instruction-node, and refactoring rules it pins.
Callers select one applicable root; they do not reconstruct the hierarchy
themselves.

Each import pins the imported version, revision, and digest. The pin makes the
meaning of a standard closure exact: a consumer cannot silently receive a
changed rule.

Within that closure, stable identifiers and typed relationships connect:

1. normative requirements and their applicability conditions;
2. definitions and guidance needed to interpret them;
3. checks, tests, assurances, semantic reviews, and their limitations; and
4. remedies and ordered procedures for resolving violations.

These relationships carry more meaning than a matching paragraph of prose.
They distinguish the rule from its explanation, the evidence from the claim it
supports, and the remedy from the violation it addresses. A validator can
enforce a rule, but it does not become the authority that defines the rule.
Likewise, mechanical success does not erase a declared semantic-review
remainder.

The JSON
[`standard-v6` schema](../../references/standards-schema/standard-v6.schema.json)
governs the permitted document shape. Each standard document is the authority
for its semantic policy; satisfying the schema does not establish that a rule
is correct, applicable, or supported by its stated evidence.

This structure becomes operational when a consumer can retrieve the relevant
meaning without reinterpreting the entire standards collection. That is the
role of the query layer.

## 2. Queries turn standards into task context

The existing query machinery lives in
[`src/officina/standards/`](../../src/officina/standards/). The
[`extractor`](../../src/officina/standards/extractor.py) validates and
materializes a selected standard with its pinned import closure. The
[`query interface`](../../src/officina/standards/query.py) projects that
material into a task-sized result through the exported
`standards.interface.query-standard` interface.

A query does not infer which node is being changed or which standard owns it.
The calling skill establishes the node role and selects the canonical root
from the repository graph and the current artifact. It then supplies known
task facts. Applicability conditions sort requirements into three states:

1. **true**: the requirement applies and must be followed;
2. **false**: the requirement is known not to apply; and
3. **unknown**: a required fact is missing and must be established if the
   requirement could materially affect the work.

Unknown is not another spelling of false. This is what lets a standard respond
precisely to a narrow query without making missing context permissive.

The interface provides several views over the same validated closure:

- `requirements` returns applicable requirements, unresolved requirements, and
  a compact context index;
- `context` returns definitions and guidance for exact selected references;
- `evidence` returns connected checks, tests, assurances, semantic reviews,
  artifacts, and limitations;
- `remedies` returns the procedures linked to selected violations; and
- `full` exposes the complete projection for unusual inspection and debugging.

Follow-up views use exact `document` and `ref` pairs returned by the
requirements query. The pair preserves provenance even when a result came from
an imported document. It also prevents a broad keyword search from quietly
substituting a similar-looking rule.

The consumer skills invoke the interface through Officina's dispatcher. A
requirements query has this shape:

```json
{"caller":"skill-maker","interface":"standards.interface.query-standard","version":1,"arguments":{"positionals":["references/node-standards/instruction-module.standard.yaml"],"options":{"--repo-root":"/path/to/repository","--facts-json":"{\"task.kind\":\"author-skill\"}","--view":"requirements"},"stdin":null},"dry_run":false}
```

The caller, selected root, repository, facts, and view are explicit. A caller
can set `dry_run` to `true` first to verify that the query targets the intended
interface and checkout before it reads the closure.

## 3. How authoring and refactoring skills use the system

[`skill-maker`](../../skills/skill-maker/) uses the query when creating or
changing a skill. It selects a root for each component actually being
authored: an instruction module, an instruction behavioral source, a Python
module, or a Python behavioral source. It queries with task facts, applies
the true requirements, resolves material unknowns, and requests only the
context, evidence, or remedies needed for the proposed change.

[`refactor-node`](../../skills/refactor-node/) follows the same query contract
for behavior-preserving work. It first establishes the registered node and the
affected source, then selects standards according to the affected node and
source type. A whole-skill audit may require several roots; a narrow source
refactor should not query unrelated components. The returned evidence and
remedies guide its behavior-preservation checks, while unresolved facts remain
visible.

For either skill, work that touches executable test artifacts also uses the
independent code-testing standard.

Neither skill owns the standards it consumes. Both depend on one shared query
interface so that authoring and refactoring cannot develop separate
interpretations of repository policy.

## 4. Changing a standard

Users change canonical policy through
[`update-standards`](../../skills/update-standards/). A request should describe
the intended semantic change: what should be required, permitted, explained,
checked, or repaired, and for which kinds of nodes or tasks. The skill then
identifies the canonical owner and the dependent closure rather than editing a
convenient consumer copy.

A request can be as direct as: "Use `update-standards` to require `<policy>`
for `<node or task scope>`." The user supplies the intended meaning; the skill
identifies its canonical owner and dependent closure, stopping rather than
guessing if ownership or the intended compatibility boundary is unresolved.

The maintenance flow is:

1. Locate the canonical target, its pinned imports and direct dependents, any
   registered generated view, the relevant schema definitions, and current
   validator findings.
2. Make the smallest semantic change that expresses the policy. Pair violated
   requirements with applicable remedies, and keep declared evidence and
   enforcement mechanisms, including their limitations, honest.
3. Bump the edited document's revision, update the revision and digest pinned
   by each direct dependent, and repeat outward. Change `standard_version` only
   when compatibility changes. The skill and schema define the exact digest
   rules.
4. Regenerate registered Markdown views from their YAML authority. A generated
   view is never edited as policy, and standards without one remain YAML-only.
5. Run focused validation, the repository standards validator, tests for any
   changed enforcement, and inspect the exact diff.

The schema validator and renderer are maintained beside the schema in
[`references/standards-schema/`](../../references/standards-schema/), while
[`validators/standard_documents.py`](../../validators/standard_documents.py)
provides the repository validation path for canonical standard documents and
registered generated views.

`update-standards` stops rather than inventing policy when ownership, the
compatibility boundary, the correct remedy, or an evidence claim remains
unresolved. On completion it reports the semantic change, revision and digest
cascade, regenerated views, enforcement or evidence changes, validation
results, and any remaining semantic-review work.

The result is a standards system that can evolve without turning its consumers
into competing authorities. Users adjust policy once at its canonical owner;
pinned closures propagate that decision, and focused queries deliver its
meaning where authoring and refactoring work need it.

## Related documentation

- [Overview](README.md)
- [Getting Started](getting-started.md)
- [Architectural Principles](architectural-principles.md)
- [Blueprints](blueprints.md)
- [Schemas](schema.md)
- [Refactoring Officina Nodes](refactor.md)
- [Canonical node standards](../../references/node-standards/)
- [Standards schemas and validator](../../references/standards-schema/)
