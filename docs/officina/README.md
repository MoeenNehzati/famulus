# Officina

Officina is a framework for the continuous development of systems whose
behavior is expressed through both machine code and human-language
instructions. It seeks reliability and maintainability by dividing such a
system into cohesive parts, encapsulating those parts, and making the
relationships between them explicit.

This page explains the problem Officina solves, the reasoning behind its
design, and what the framework is made of. It is not normative. The rules
themselves live in [Architectural Principles](architectural-principles.md).

## The problem

While most existing programming languages enforce structure to foster these
goals, such structures don't exist for modules composed of a mixture of LLM
instructions and code. Officina aims to fill this gap by providing a harness
for the continuous development of mixed LLM/code projects. One example of such
a project is a skill library. It's natural to think of each skill as a module
that can reuse the code and LLM instructions of other modules if needed.

The trouble is not the reuse itself but that nothing keeps track of it. In a
programming language, one module cannot quietly depend on the internals of
another: the dependency must be declared before it can exist, and the compiler
or module system refuses whatever was not declared. Between two skills, that
same dependency is just a sentence. Nothing resolves it, nothing records it,
and nothing can reject it. Undocumented coupling is therefore not a tendency
that some projects fall into. It is the default state of any system where
nothing prevents it, and its absence would be the surprising outcome.

LLM assistance turns this from a slow problem into a fast one. Architectural
decay is a function of how much change a system absorbs against how much of
that change is checked; LLM-assisted development multiplies the first and
leaves the second at zero. This matches what I have seen building this
library. The coupling accumulates quietly, and by the time it is visible,
neither a human nor a model can safely change one skill without breaking
another.

To be specific, the project addresses two main concerns:
1. Undocumented and unregulated dependencies grow ever more numerous over the
   course of development. Each skill may reuse any part of the existing
   project in any fashion.
2. The lack of a boundary between LLM instructions and machine instructions
   hinders reproducibility and performance. A good LLM-assisted module should
   do as much as it can with scripts and use an LLM only where a script won't
   do.

## The remedy

The proposed remedy is as follows: develop standards for what such mixed
projects should look like, then check them statically and periodically (via
git hooks). Officina calls such conformance tests validators. Failures are
accompanied by informative messages guiding the LLM to make the right
adjustments. An LLM may still find a way to forgo these checks (by forcing a
commit, for instance), but this is unlikely, since it is directly instructed
not to force commits unless the user approves.

## Nodes and blueprints

Any such standard presupposes that we can formally analyze mixed modules. The
first step is structuring the code base into logical components we call nodes.
A node is a logical unit that exists in the project and can be contained by
other nodes; it may be a mixed module, a Python file, or even a JSON schema.

Since nodes in mixed projects can be of different types, some of which (like
LLM instructions) have next to no structure, we accompany them with
machine-readable documentation files called blueprints. A blueprint documents
all the relevant information about the node. Once machine-readable blueprints
reflect the node, interactions between different nodes can be allowed or
prohibited based on the blueprints. We can even construct the graph of the
repository and put constraints on its shape, for example banning dependency
cycles.

## Where mechanical checking runs out

The catch is that quality assurance for blueprints is not trivial. This is
part of a bigger problem: not every standard we set for the objects in the
repository will be mechanically checkable. For example, we want to remove
direct references a skill makes to the content of another skill. We can ban
all exact paths from the blueprints and ban paths that look like
`../<other-skill-name>/`. But there are many ways of sneaking that address in,
for example by stating "go to the parent skills directory and look under
`<other-skill-name>`". As the example demonstrates, there are meaningful
mechanical harnesses that get some of the job done, but when dealing with
free-form instructions, you can rarely exhaust all the bad behaviors
mechanically.

The solution is a hybrid. Keep mechanical tests, and design the system to
favor them. For example, take skill names. We want to know whether a skill is
being addressed in another skill. If skill names are allowed to be single
words, like `design`, then it's next to impossible to mechanically assess
whether an occurrence of design is just the word design or a reference to
`design`. Officina's solution is to require skill names to contain a hyphen,
renaming `design` to, for example, `design-code`. Then an occurrence of
`design-code` can be interpreted as a reference to `design-code`. The trick is
to enrich the language with additional structure and then use that structure
for machine checks. This is a recurring pattern across Officina: when in need,
we enrich the problem with structure that allows for mechanical checks,
sometimes even building a domain-specific language.

## Certification

Still, these mechanical checks aren't exhaustive, and we occasionally need
human/LLM audits. Chief among the things only an audit can settle is the
question we started with: whether a blueprint faithfully describes the node it
claims to. The problem with human/LLM checks is that they are orders of
magnitude more expensive than mechanical ones. The solution is to do them only
when needed and to retain the checks that passed until relevant changes happen
in the repo. The certification process takes care of this. A certificate is
given to a node if it passes all its mechanical and human/LLM tests. The
certificate contains the relevant hashes for the node's content and its
dependencies. Hence a certificate is retained so long as those hashes do not
change, meaning the changes in the repo were not relevant to our node. If
hashes drift, the certificate goes stale and re-certification is required.

## In short

1. Officina contains a rich set of standards for how modules should be
   organized and interact, to ensure encapsulation, reproducibility, and
   maintainability.
2. The standards are designed to be checkable with mechanical validators as
   much as possible.
3. A certification process augments these with LLM/human-assisted validation.
4. The certification and validators are used to harness LLM-assisted
   continuous development.

## What Officina comprises

Officina is the shared code, the machine-readable contracts, and the
framework-authoring skills listed below. Everything else in this repository is
Famulus — the skill library that happens to be built on Officina.

One distinction matters when reading this list. Many Famulus skills import
`officina` in order to reach the dispatcher or the runtime. **That makes them
consumers of the framework, not parts of it.** `email-client`, `g-calendar`,
and `daily-plan` all import `officina`; none of them is Officina. Membership
follows from what a component is *for*, not from what it depends on — the same
reasoning principle 4.1 applies to authority, which is likewise not inherited
from a dependency.

### Shared code — [`src/officina/`](../../src/officina/)

- `dispatcher/` — the direct, read-only boundary through which one node invokes
  another node's exported interface: bounded blueprint resolution,
  authorization, CLI, and per-platform process handling
- `runtime/` — execution of Python machine interfaces in their own process
- `common/` — the shared machinery: blueprint graph, inventory, template and
  authorization; certificate records, hashing and views; configured-schema
  loading; git provenance; interface projection; process-binding compilation;
  atomic file writes; repository paths; the secret store; test discovery; and
  the docstring pipeline
- `install/` — installing an Officina project onto a machine: managed runtime,
  launcher entries, resolvers, runtime pointer, uv bootstrap, and the
  ownership-aware install manifest that makes uninstall exact
- `validators/` — validators shipped by the framework itself
- `wakeup/` — host-session lifecycle across supported hosts
- `blueprint_search.py` — querying the architectural graph

### Machine-readable contracts — [`references/`](../../references/)

- [`blueprint/`](../../references/blueprint/) — the blueprint schema, its
  metadata, and the authoring template
- [`node-standards/`](../../references/node-standards/) — the layered node
  standards: `node` at the root, specialized into `module` and
  `behavioral-source`, then into instruction- and Python-specific variants,
  plus the refactoring standard, authority disposition, and semantic-review
  criteria
- [`standards/`](../../references/standards/) — the standard-v6 schema, its
  validator and renderer, and the docstring standard and grammar
- [`skill-standards/`](../../references/skill-standards/) — skill-authoring
  guidelines
- [`certification/`](../../references/certification/) — node-hash policy and
  the certification-basis roots

`references/document-standards/` is **not** part of Officina. It holds the
research-document profile consumed by Famulus's writing skills. It is written
in Officina's standard format, but the format is Officina's and the content is
Famulus's.

### Framework-authoring skills — [`skills/`](../../skills/)

These skills exist to operate on the framework itself:

- [`skill-maker`](../../skills/skill-maker/) — author skills and keep
  blueprints and generated views in sync
- [`skill-certifier`](../../skills/skill-certifier/) — issue node certificates
  for an exact committed state
- [`skill-drift`](../../skills/skill-drift/) — read certificate currentness
  and canonical node hashes
- [`regenerate-blueprints`](../../skills/regenerate-blueprints/) — refresh an
  existing blueprint
- [`refactor-node`](../../skills/refactor-node/) — audit or refactor a node
  against the standards
- [`update-standards`](../../skills/update-standards/) — change a canonical
  standard together with its pinned dependents, generated views, and
  enforcement artifacts
- [`install-assistant-tools`](../../skills/install-assistant-tools/) — install
  or repair an Officina project on a machine
- [`llm-wakeup`](../../skills/llm-wakeup/) — schedule and manage host sessions
  around usage resets; the instruction side of `src/officina/wakeup/`

## Where to go next

**Read [Architectural Principles](architectural-principles.md) first.** It is
the normative layer: it states what must be true of any node, boundary,
dependency, and certificate, and every other document here is subordinate to
it. Everything below explains how those principles are realized.

Then, depending on what you need:

- [Architecture](architecture.md) — how the principles are implemented:
  nodes, gateways, blueprints, interfaces, discovery, and the graph
- [Dispatcher](dispatcher.md) — direct route resolution, authorization,
  execution, failures, and performance budgets
- [Certification and Drift](certification_and_drift.md) — certificate
  lifecycle, drift evaluation, and what makes a certificate stale
- [Skill Blueprints](skill-blueprints.md) — authoring blueprints in practice
- [Blueprint Search](blueprint_search.md) — querying the graph
- [Blueprint Discovery Metadata](blueprint-discovery-metadata.md) — how
  discoverable modules declare when they apply
- [Configured Schemas](configured-schema.md) — the configuration and
  JSON Schema loading boundary
- [Docstring Contract](docstring.md) — the docstring policy, grammar, and
  validation pipeline
- [Visualization](visualization.md) — the Officina visualization module
- [Scaffolding](scaffolding/README.md) — the scaffolding layer and why it
  exists
- [Installation](installation.md) — how an Officina project is installed, and
  the manifest-based uninstall process

If you are extending Famulus rather than working on Officina itself, start
from the [Contributor Guide](../contributors/README.md) instead.
