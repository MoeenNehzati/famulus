# Blueprints

> **Status:** Guide.
>
> This document explains what Officina blueprints mean and how their parts fit
> together. The schemas under
> [`references/blueprint-schema/`](../../references/blueprint-schema/) are the
> authority for exact fields, shapes, and required values.

Software can usually expose its structure through its programming language.
Mixed systems cannot. A skill may combine human-language instructions, Python,
schemas, configuration, and host resources, while the relationships among
those pieces remain implicit. Inspecting the files tells us what is present,
but not necessarily what is owned, callable, permitted, or depended upon.

Officina addresses this by dividing the repository into nodes and giving every
node a machine-readable blueprint. The node's gateway is its operational face:
the artifact through which it behaves. Its blueprint is its descriptive face:
the declaration that tools and contributors use to understand that behavior in
architectural terms.

A blueprint is therefore more than file metadata. It is the node-local portion
of the repository graph. Taken together, blueprints make containment,
ownership, dependencies, interfaces, authority, discovery, and certification
inputs explicit enough to validate. A schema-valid blueprint may still be
false or incomplete; certification checks the description against the node it
claims to represent.

This guide explains that model and the judgment involved in authoring it. It
does not repeat every schema rule. When an exact key, type, pattern, or required
field matters, consult the concrete schema.

## 1. The blueprint model

Every blueprint describes exactly one node. Officina has two node kinds:

1. A `module` is a directory-rooted identity, namespace, discovery, access,
   and authority boundary.
2. A `behavioral_source` is a cohesive implementation or instruction unit
   contained by exactly one module.

This division separates authority from behavior. A module decides what the
outside world may find and use. A behavioral source defines what a piece of
behavior does and how it is realized. A module may expose a source interface,
but it does not copy or take ownership of that interface's contract.

Every node declares its identity, maturity, gateway, and owned content. Other
fields follow from the role of the node: modules own containment and outward
authority; sources own behavior, contracts, dependencies, effects, and
bindings.

### 1.1 One authority for each fact

Blueprints refer to facts owned elsewhere instead of copying them. A source
owns its interface contract. A module export refers to that interface while
adding a public ID and access policy. A consumer records that it uses the
interface without reproducing the provider's contract.

Generated `SKILL.md` blocks, runtime-dependency indexes, search results, and
certificate records are derived views or lifecycle state. They do not add
nodes or architectural relationships.

### 1.2 Schema and guide

The concrete schemas define what can be written. This guide explains why the
declarations exist and how to choose among valid alternatives. In particular:

- [`module.schema.json`](../../references/blueprint-schema/module.schema.json)
  defines module declarations;
- [`behavioral-source.schema.json`](../../references/blueprint-schema/behavioral-source.schema.json)
  defines behavioral-source declarations;
- [`caller-contract.schema.json`](../../references/blueprint-schema/caller-contract.schema.json)
  defines interface contracts and process bindings;
- [`common.schema.json`](../../references/blueprint-schema/common.schema.json)
  defines shared shapes; and
- [`config.yaml`](../../references/blueprint-schema/config.yaml) supplies the
  configured discovery vocabulary.

The [schema reference](../../references/blueprint-schema/README.md) routes to
the remaining contracts and explains the live schema family.

## 2. Layout, identity, and ownership

A typical skill contains a discoverable instruction module and a
non-discoverable runtime child:

```text
skills/<skill-id>/
  blueprint.yaml
  blueprints/
    gateway.yaml
  SKILL.md
  _rtx/
    blueprint.yaml
    blueprints/
      runner.yaml
    runner.py
```

Module blueprints live at `<module-root>/blueprint.yaml`. Directly contained
source blueprints live under `<module-root>/blueprints/`. A parent registers
each direct child in `children`; physical nesting by itself does not create a
node or relationship.

The canonical identity forms are:

```text
module:              <module-id>
behavioral source:   <module-id>.source.<local-source-id>
source interface:    <source-id>.interface.<local-interface-name>
module export:       <module-id>.interface.<export-name>
```

A repository-managed skill that separates executable behavior may register at
most one code child at `_rtx/`, identified as `<skill-id>._rtx`. The skill
parent owns instruction behavior, discovery, and parent-facing authority. When
present, the child owns executable behavior, runtime assets, machine
interfaces, and their tests.

Containment bounds ownership but does not imply dependency or permission.
Every regular file has one most-specific direct owner. A parent cannot claim a
child's files, sibling sources cannot overlap, and proximity cannot silently
grant access.

## 3. Declarations shared by nodes

Both node kinds declare `schema_version: 6`, their `node_type`, canonical ID,
version, maturity, gateway, and content scope. They may also carry a concise
description.

The gateway names one whole file and the language or notation needed to
interpret it. `content` identifies the node's directly owned files. Gateway
fragments and implementation-specific entry selection do not belong here;
callable transport details belong to a source interface's process binding.

`maturity` expresses readiness: `stable` identifies a ready node and
`experimental` one still being evaluated. It is independent of installation.
A node may be experimental and still belong to the core installation, or be
stable and optional.

Persistent state, filesystem authority, platform support, runtime
dependencies, and direct I/O must be declared at the node that owns or uses
them. These declarations describe architectural facts, not observations about
one host.

## 4. Module blueprints

A module blueprint owns:

1. the module's identity, gateway, and direct content scope;
2. its contained sources and registered child modules;
3. discovery and installation metadata;
4. filesystem authority and host-resource discovery; and
5. exports, namespace routes, and caller access policy.

Behavior remains private unless a module deliberately exports it. An export
adds a module-facing ID and access policy to an interface owned by a source. It
does not copy the source contract or binding.

`children` registers a namespace; it does not expose that namespace outside
the parent. `namespace_exports` may route selected child interface IDs across
the parent boundary. Routes preserve the descendant's identity and may narrow
access, but cannot widen the child's policy or turn a private interface public.

Every discoverable skill module declares `installation_tier: core` or
`installation_tier: optional`. Core modules are selected by default. Optional
modules are selected as complete module units with their contained sources and
applicable runtime-dependency closure. `personal_preference` records a genuine
user-specific workflow choice; it is not a general description field.

## 5. Behavioral-source blueprints

A behavioral-source blueprint owns:

1. its whole-file gateway and directly owned content;
2. intrinsic interfaces and their semantic contracts;
3. source dependencies and exact interface uses;
4. process bindings, direct I/O, effects, and outcomes; and
5. platform support and runtime dependencies.

A source declares another source in `dependencies` when its behavior depends
on that source as a unit. It records callable relationships in
`uses_interfaces`. Same-module uses may target private source interfaces;
cross-module uses must target an authorized module export.

An interface contract states the meaning of an interaction: arguments,
preconditions, outputs, outcomes, effects, and lifecycle. Its optional process
binding states how that meaning is invoked through a particular gateway,
including entry selection and transport. Keeping meaning separate from binding
allows an implementation mechanism to change without redefining the contract.

## 6. Discovery metadata

Discovery makes a module findable; it does not expose the module's internal
behavior. A discovery description states when the module applies and should be
specific enough to support routing before detailed instructions are loaded.

Discoverable modules also declare compact catalog metadata for generated
documentation and repository navigation. The configured vocabulary in
[`config.yaml`](../../references/blueprint-schema/config.yaml) controls the
allowed spellings. The following rules define how those values are chosen.

- Choose one `catalog.domain`: the module's primary direct user outcome.
- List each `catalog.topics` value the module directly serves, excluding subjects
  merely used by its implementation or dependencies.
- Set `catalog.visibility` from documentation policy, not implementation size.
- List only `activated_by` mechanisms that can actually initiate the module.
- Set `persistent_modifier` only when invocation intentionally changes
  assistant behavior after the invocation ends.

When two domains seem plausible, choose the one that best answers “Why would a
user seek this module?” and represent other directly served concerns as topics.

### 6.1 Domains

| Value | Use when the primary direct outcome is |
| --- | --- |
| `personal-assistance` | Managing a user's plans, communications, personal information, or everyday actions. |
| `research` | Producing or checking scholarly reasoning, evidence, mathematics, or research documents. |
| `software-development` | Creating, understanding, testing, reviewing, or maintaining software and repositories. |
| `assistant-development` | Creating or changing assistant skills, standards, architecture, assurance, or installation machinery. |
| `assistant-operations` | Operating assistant runtimes, scheduled automation, integrations, storage, synchronization, or host maintenance. |
| `assistant-interaction` | Controlling how a user and assistant collaborate across a request or session. |

### 6.2 Topics

| Value | Direct capability represented |
| --- | --- |
| `planning` | Constructing or revising plans and schedules. |
| `communications` | Reading, composing, organizing, or acting on messages. |
| `personal-organization` | Maintaining personal tasks, lists, files, or routines. |
| `mathematical-reasoning` | Proving, auditing, or structurally analyzing mathematics. |
| `research-writing` | Drafting or revising scholarly arguments and exposition. |
| `scholarly-documents` | Processing citations, PDFs, LaTeX, or publication artifacts. |
| `visualization` | Producing or interacting with visual representations of structured information. |
| `repository-workflow` | Managing source-control, review, testing, or repository change workflows. |
| `assistant-authoring` | Creating or editing assistant-facing skills, hooks, prompts, or standards. |
| `assistant-architecture` | Designing assistant module boundaries, contracts, and dependency structure. |
| `assistant-assurance` | Validating, certifying, auditing, or detecting drift in assistant components. |
| `assistant-installation` | Installing or propagating assistant components and host integration. |
| `external-integrations` | Connecting assistant behavior to external services or APIs. |
| `storage-and-sync` | Persisting, retrieving, or synchronizing data across locations. |
| `task-automation` | Running repeatable or scheduled work without step-by-step user control. |
| `system-maintenance` | Diagnosing or repairing host-level operational state. |
| `session-management` | Starting, ending, resuming, or handing off assistant sessions. |
| `reasoning-control` | Intentionally changing the assistant's reasoning or collaboration mode. |

### 6.3 Visibility and activation

| Value | Meaning |
| --- | --- |
| `featured` | Prominently present the module as a primary supported entry point. |
| `listed` | Include the module in inventories without primary prominence. |
| `hidden` | Omit the module from ordinary generated indexes while retaining its metadata for tooling. |
| `user-request` | The host can discover and invoke the skill from a matching user request. |
| `skill-workflow` | Another skill can invoke it through a declared repository interface. |
| `scheduled-job` | A configured scheduler can initiate it without a contemporaneous user request. |

The schema checks vocabulary, shape, uniqueness, and the relationship between
`persistent_modifier` and `reasoning-control`. Whether a valid label tells the
truth about runtime behavior or workflow intent still requires semantic review.

## 7. Authoring workflow

1. Define the module boundary, registered children, sources, gateways, and
   direct ownership.
2. Describe each source's behavior, dependencies, interfaces, contracts, and
   realization details.
3. Export only interfaces intended to cross a module boundary. Add namespace
   routes only when descendant IDs must cross the parent boundary.
4. Declare discovery, installation, authority, platform, and resource facts at
   their canonical owners.
5. Run the blueprint synchronization check and repository validators.
6. Review each blueprint against its gateway and owned content, then certify
   the exact committed state.

For the live commands and public interfaces, use the relevant authoring or
certification skill rather than reaching into its private runtime files.

## Related documentation

- [Architectural principles](architectural-principles.md)
- [Blueprint search](blueprint_search.md)
- [Certification and drift](certification_and_drift.md)
- [Dispatcher](dispatcher.md)
- [Refactoring Officina nodes](refactor.md)
- [Blueprint schema reference](../../references/blueprint-schema/README.md)
