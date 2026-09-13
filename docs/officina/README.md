# Officina

> **Status:** Nonnormative overview.
>
> [Architectural Principles](architectural-principles.md) states the governing
> rules. This page explains the problem, the working model, and where the
> current implementation lives.

Officina is a framework for continuously developing systems whose behavior
spans model-interpreted instructions and machine-executable code. It seeks
reliability and maintainability by dividing such a system into cohesive parts,
encapsulating those parts, and making their relationships explicit.

## Why Officina exists

Famulus is the motivating implementation. To make complex personal-assistance
and research tasks more reliable, it implements deterministically specifiable
operations in machine-executable code wherever practical and uses
model-interpreted instructions for work that requires semantic judgment. This
reduces the portion of a task delegated to the model, though the model still
interprets results and decides what to do next.

Conventional machine-executable software benefits from physical organization
into source modules and from language and toolchain constructs such as imports,
packages, interfaces, and types. These mechanisms do not eliminate hidden
coupling, but they make many references visible, inhibit many undeclared uses,
and expose dependency surfaces that tools can check.

Mixed systems do not receive equivalent coverage across their full behavior. A
human-language instruction can tell one component to rely on another
component's internals without creating an import, declaring an interface, or
recording a dependency. As the system evolves, such relationships can
accumulate until a change requires broad repository inspection and becomes
difficult to make safely.

Officina supplies the missing structure. It represents the repository as
explicit nodes with ownership and authority boundaries, records dependencies
and interfaces in machine-readable blueprints, checks the resulting graph
through schemas and validators, and retains semantic assurance through
certification and drift detection.

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

## How to read these documents

After this overview, read [Getting Started](getting-started.md) once from top to
bottom. Then use the groups below as a task-based map rather than another
required sequence.

### Start here

These pages are for newcomers: read the walkthrough sequentially, and consult
the principles when you need the governing rule behind it.

- [Getting Started](getting-started.md) follows one illustrative node profile
  from boundary to drift; open it for a first practical tour of Officina.
- [Architectural Principles](architectural-principles.md) states the normative
  model; open it when a design choice or another guide needs an authoritative
  answer.

### Understand the machinery

These guides are for readers tracing how the model works: open the page that
owns the mechanism you need to understand or verify.

- [Blueprints](blueprints.md) explains how nodes declare structure and
  relationships; open it when reading or authoring a blueprint.
- [Dispatcher](dispatcher.md) explains interface routing, authorization, and
  launch; open it when following or diagnosing an invocation.
- [Schemas](schema.md) explains machine-checkable structural
  contracts and configured variants; open it when choosing or validating a
  structured boundary.
- [Certification and Drift](certification_and_drift.md) explains how semantic
  assurance is recorded and becomes stale; open it when reviewing or
  certifying a node.
- [Standards](standards.md) explains how structured standards are represented,
  queried, and changed; open it when a rule has repository-wide authority.

### Build and change

These guides are for maintainers changing nodes: follow the relevant workflow
while making the change.

- [Skill-node Maintainer Scaffolding](scaffolding/README.md) explains the
  current Famulus authoring and validation machinery; open it when creating or
  maintaining that skill-node profile.
- [Refactoring Officina Nodes](refactor.md) explains in-place refactoring and
  relocation boundaries; open it before changing a node's structure or
  ownership.

### Inspect and operate

These pages are for maintainers interrogating or running the system: use them
as operational references for a specific task.

- [Blueprint Search](blueprint_search.md) explains repository-graph queries;
  open it when locating owners, dependencies, interfaces, or related nodes.
- [Compass and Rutter](compass-rutter.md) explains durable LLM-operated
  algorithms; open it when defining or operating a Rutter through its public
  dispenser.
- [Visualization](visualization.md) explains graph extraction and rendering;
  open it when choosing a projection or producing a visual view.

### Implementation reference

These references are for contributors working close to the current code: open
them for an exact implementation concern, not as onboarding material.

- [Docstring Contract](docstring.md) explains the structured Python docstring
  format; open it when authoring or validating those contracts.
- [Implementation Map](utility-map.md) explains current task-to-package
  ownership; open it when locating the code that implements a framework
  concern.

If you are extending Famulus rather than working on the framework, start from
the [Contributor Guide](../contributors/README.md).
