# Officina

> **Status:** Nonnormative overview.
>
> [Architectural Principles](architectural-principles.md) states the governing
> rules. This page explains the problem, the working model, and where the
> current implementation lives.

Officina is a framework for continuously developing systems whose behavior is
expressed through both machine code and human-language instructions. It seeks
reliability and maintainability by dividing such a system into cohesive parts,
encapsulating those parts, and making their relationships explicit.

## Why Officina exists

Programming languages make many architectural relationships visible. Imports,
interfaces, packages, and type systems constrain how one machine-code component
can use another. Mixed LLM/code systems do not receive an equivalent structure
automatically. A dependency between two skills can be only a sentence: nothing
resolves it, records it, or rejects it when it crosses a boundary.

That default becomes costly under LLM-assisted development. The volume of
change increases while undocumented coupling remains unchecked. Eventually a
human or model cannot change one part confidently without inspecting much of
the repository.

Officina supplies the missing structure. It represents the repository as
explicit nodes, records their architectural facts in machine-readable
blueprints, and checks those declarations through schemas, validators, and
certification.

## The working model

Officina has two node kinds:

1. A **module** is an identity, namespace, discovery, access, and authority
   boundary.
2. A **behavioral source** is a cohesive unit of instructions or implementation
   contained by one module.

Each node has an operational face and a descriptive face. The gateway is the
file through which the node behaves. The blueprint describes the node's
identity, ownership, dependencies, interfaces, authority, effects, and other
architectural facts in a form tools can inspect.

Blueprints taken together form the repository graph. Mechanical validators can
then check document shape, ownership, references, dependency rules, interface
access, and generated views. These checks are necessary but not exhaustive: a
schema-valid blueprint may still describe its gateway inaccurately.

Semantic review supplies the assurance that mechanical checks cannot. The
certification process records and retains the combined evidence for the exact
committed state, and the resulting certificate becomes stale when relevant
inputs drift. The
[Certification and Drift](certification_and_drift.md) guide owns the lifecycle
details.

## Current implementation map

Officina currently exists inside Famulus. The exact future standalone package
and graph boundary is not yet fully declared. The following map describes the
current repository implementation; it is not a permanent packaging contract.

### Shared code — [`src/officina/`](../../src/officina/)

- `blueprints/` — blueprint loading, graph construction, authorization,
  projection, process binding, templates, and search
- `certification/` — node hashing, certificate records, and currentness views
- `common/` — small shared primitives for atomic files, command files, paths,
  TOML, dates, and source caching
- `configuration/` — configured schemas and repository configuration
- `credentials/` — Google credentials, OAuth data, and secret storage
- `dispatcher/` — bounded interface resolution, authorization, and launch
- `docstring/` — structured-docstring parsing, policy, and validation
- `git/` — repository provenance and pinned snapshots
- `launchers/` — managed agent-launch policy and backend selection
- `recurring/` — recurring-task control, execution, health checks, and native
  scheduler rendering
- `repository/` — repository-check discovery, selection, and execution
- `runtime/` — confined execution of machine interfaces
- `rutter/` — durable algorithm definitions, Voyage state, persistence, and
  dispenser interfaces
- `standards/` — pinned-standard extraction and deterministic queries
- `validators/` — framework-level validation support
- `visualization/` — graph extraction, projection, and rendering
- `wakeup/` — host-session lifecycle and reset scheduling

These packages are current implementation owners, not compatibility facades.
For task-to-module routing, use the [Utility Map](utility-map.md).

`launchers/` and `recurring/` carry a Famulus roster as data — the agent names
one launches and the jobs the other can schedule — but neither is Famulus.

### Machine-readable contracts — [`references/`](../../references/)

- [`blueprint-schema/`](../../references/blueprint-schema/) — live blueprint,
  interface, and certificate schemas plus configured vocabulary
- [`node-standards/`](../../references/node-standards/) — layered node and
  refactoring standards
- [`standards-schema/`](../../references/standards-schema/) — schemas and
  metadata for structured standards
- [`skill-standards/`](../../references/skill-standards/) — skill-authoring
  guidance
- [`certification-policy/`](../../references/certification-policy/) — node-hash
  policy and certification-basis roots

`references/document-standards/` contains research-document policy used by
Famulus skills. It uses Officina's standards machinery, but its subject matter
is not part of the framework.

### Framework-facing skills — [`skills/`](../../skills/)

The main workflows that author or operate the current framework are:

- [`skill-maker`](../../skills/skill-maker/) — author skills and synchronize
  blueprints and generated views
- [`regenerate-blueprints`](../../skills/regenerate-blueprints/) — refresh an
  existing blueprint
- [`refactor-node`](../../skills/refactor-node/) — audit or refactor a node
  against applicable standards
- [`relocate-nodes`](../../skills/relocate-nodes/) — move registered nodes and
  their owned files coherently
- [`node-certify`](../../skills/node-certify/) — certify an exact committed
  node state
- [`node-drift`](../../skills/node-drift/) — inspect certificate currentness
  and canonical node hashes
- [`update-standards`](../../skills/update-standards/) — change a canonical
  standard and its pinned dependents
- [`distill-to-rutters`](../../skills/distill-to-rutters/) — transform a
  Markdown procedure into a Rutter and operable Voyage dispenser
- [`using-compass`](../../skills/using-compass/) — operate a named Rutter
  through its public dispenser
- [`llm-wakeup`](../../skills/llm-wakeup/) — manage scheduled host sessions
  around usage resets

This is a routing list for the current repository, not a declaration that these
skills must belong to a future standalone package.

## Where to go next

Start with [Architectural Principles](architectural-principles.md) for the
normative model. Then choose the guide that owns the concern:

- [Blueprints](blueprints.md) — node declarations, discovery, and authoring
- [Dispatcher](dispatcher.md) — direct routing, authorization, and execution
- [Certification and Drift](certification_and_drift.md) — assurance lifecycle
- [Standards](standards.md) — representation, queries, and change workflow
- [Blueprint Search](blueprint_search.md) — repository-graph queries
- [Refactoring Officina Nodes](refactor.md) — in-place refactoring versus
  relocation
- [Configured Schemas](configured-schema.md) — configuration-derived schema
  constraints
- [Docstring Contract](docstring.md) — structured Python documentation
- [Compass and Rutter](compass-rutter.md) — durable LLM-operated algorithms
- [Visualization](visualization.md) — graph extraction and rendering
- [Maintainer Scaffolding](scaffolding/README.md) — repository authoring and
  validation machinery
- [Utility Map](utility-map.md) — task-to-package routing

If you are extending Famulus rather than working on the framework, start from
the [Contributor Guide](../contributors/README.md).
