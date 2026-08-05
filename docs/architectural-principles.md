# Officina Architectural Principles

> **Status:** Normative.
>
> This document states the principles that govern Officina.

Officina is a framework for the continuous development of systems whose
behavior is expressed through both machine code and human-language
instructions. It seeks reliability and maintainability by dividing such a
system into cohesive parts, encapsulating those parts, and making the
relationships between them explicit.

Programming languages provide established ways to organize code into modules.
They do not provide equivalent standards for modules that combine executable
behavior with instructions interpreted by humans or language models. Officina
supplies this missing architecture.

The architecture follows one chain:

1. behavior is divided into encapsulated nodes;
2. each node describes itself in a structured form;
3. those descriptions form a graph of explicit relationships;
4. standards state the rules that govern the graph and the behavior it
   describes;
5. validators decide the mechanically checkable part of those rules;
6. humans or language models decide the semantic remainder;
7. certification retains the combined assurance; and
8. currentness is lost when relevant state differs or assurance can no longer
   be evaluated.

Detailed contracts, schemas, validators, documentation, and implementations
must conform to these principles.

> **Current scope, nonnormative:** Officina currently exists as an internal
> framework within Famulus. Its exact package and graph boundary is not yet
> fully declared. The settled direction is to separate Officina from the
> application skills of this repository and make it a standalone framework.
> The exact packaging and extraction design remain unspecified. Some principles
> describe approved targets beyond the current implementation. Implementation
> maturity does not qualify them.

## 1. Architectural method

Officina reduces the amount of behavior that must be understood and changed
together.

Behavior that can evolve independently should, when useful, be separated
behind an explicit boundary. Dependencies and authority must be declared
rather than inferred from physical proximity or convention.

Once the boundaries are explicit, every fact and rule must use the least
interpretive representation that can express it faithfully, and every
architectural fact must have one canonical owner.

These rules apply to machine code, human-language instructions, schemas,
configuration, and every other artifact that participates in behavior.

### 1.1 The most formal adequate representation

A rule's authoritative standard states the rule. Its enforcement must require
as little interpretation as its meaning permits.

1. A mechanically decidable rule must be enforced by deterministic code rather
   than left to human-language judgment.
2. A rule that cannot be implemented as code, but can be expressed
   structurally, must use a standardized, machine-readable format.
3. Free-form prose must be reserved for what neither code nor structured data
   can adequately express.
4. Human language does only what code cannot. Unstructured human language does
   only what structured human language cannot.

### 1.2 One authority for each fact

Every architectural fact has one canonical owner. Other descriptions refer to
that fact instead of copying it.

Generated views may repeat canonical facts for a particular consumer. They
remain derived views. A correction is made at the owner and propagated to the
views.

The same principle applies to interpretation. One concern must not acquire
parallel graphs, policies, resolvers, or definitions of currentness. A
specialized mechanism may apply the canonical authority. It may not become a
second authority.

## 2. Nodes and ownership

A **node** is a logically cohesive and encapsulated part of the system. A node
contains behavior that must be understood and changed together.

Every node has:

1. **a content scope**: the physical files and directories within its
   boundary, with direct ownership resolved to the most-specific node;
2. **a gateway**: the file or system through which its behavior is interpreted
   or invoked;
3. **a language requirement**: the language or notation of the gateway;
4. **machine compatibility claims**, when relevant: the machines claimed to
   be able to interpret the gateway; and
5. **a blueprint**: the machine-readable description of the node.

A node boundary keeps implementation inside the node and limits how the rest
of the system may depend on that implementation. A boundary is useful only
when it reduces what must be understood or changed together. A recognizable
directory, package, test suite, or document set does not need to become a node
merely because it forms a group.

### 2.1 Modules and behavioral sources

There are two kinds of nodes: **modules** and **behavioral sources**.

A module is an encapsulation and authority boundary. It owns its external
identity, namespace, discovery, exports, access policy, privileges, and
resource authority.

A behavioral source is a cohesive unit of behavior inside one module. It owns
its gateway, intrinsic interfaces, dependencies, inputs, outputs, effects, and
actions. It acts within the authority of its module and cannot create
independent privileges.

This separation keeps external authority apart from behavioral
implementation. A module can change how behavior is exposed without taking
ownership of the behavior. A behavioral source can change its implementation
without redefining the authority of the module.

### 2.2 Ownership and containment

Every node-owned artifact has one most-specific direct owner. Ownership
between sibling nodes cannot overlap. A module may contain the scope of a
behavioral source without directly owning the source's files.

Containment bounds and orders ownership. Explicit content declarations select
the most-specific direct owner within those bounds.

Containment does not create dependency or authority. A parent does not depend
on a child merely because it contains the child. A child does not gain access
to its parent or siblings merely because they share a containing module.

Dependencies and permissions require their own declarations. Physical
proximity cannot silently create either one.

### 2.3 State and secrets

Persistent operational state must have an explicit owner. Cross-node access to
that state must use a declared relationship authorized by the owner.

Secrets are not ordinary node content. They must remain outside versioned
behavioral content and enter a node only through a declared, controlled
boundary.

### 2.4 Shared infrastructure

Shared Officina utilities contain general mechanisms, not application
behavior. A mechanism may be shared when it is narrow, independent of the
product behavior of any one node, and exposed through a host-neutral contract.
Its implementation may delegate to platform-specific facilities behind that
contract. Behavior specific to a skill or application remains with its owner.

## 3. Operational and descriptive faces

The gateway is the **operational face** of a node. It is the artifact or system
through which the node does what it does.

The blueprint is the node's **descriptive face**. It describes the node in a
standardized form that tools and contributors can inspect.

The blueprint records, as applicable:

1. the identity and purpose of the node;
2. its content and gateway;
3. its containment and dependencies;
4. its interfaces and authority;
5. its inputs, outputs, effects, and outcomes;
6. its external resources and privileges; and
7. its discovery mechanism.

The blueprint makes the node understandable without requiring inspection of
its implementation. It also makes the architecture checkable as data rather
than leaving it as a convention known only to contributors.

A gateway role does not necessarily imply direct ownership of the gateway
bytes. A module may bind its operational gateway to a contained behavioral
source. The source owns the implementation; the module owns how that behavior
is exposed.

### 3.1 Alignment

The descriptive face must accurately and completely represent the operational
face and the behavior-relevant content of the node. A blueprint that satisfies
its schema may still be incomplete or false.

Some facts can be recovered mechanically. Other facts require semantic
judgment. Missing facts remain unknown until they are established; they do not
become permissive defaults.

Mechanical and semantic review establish that the two faces agree.
Certification records and retains the resulting assurance. Its evidence is
kept outside the blueprint so that the description of a node does not also
declare its own correctness.

### 3.2 Contracts and bindings

An interface contract describes the meaning of an interaction: its inputs,
outputs, preconditions, outcomes, effects, and lifecycle.

The contract belongs to the behavioral source that defines the behavior. A
module export refers to that contract while adding public identity and access
policy. It does not reproduce the contract as a second authority.

A binding describes how that meaning is realized through a particular gateway.
Transport, argument encoding, entry selection, output framing, and
cancellation belong to the binding rather than to the semantic contract.

This distinction lets the same behavioral contract survive changes in
implementation language or invocation mechanism.

### 3.3 Portable meaning

Portable meaning and platform-specific realization are separate.

Structured concepts such as identities, paths, and process targets should remain
structured inside the system. They are encoded as command arguments, text, or
platform-specific paths only at explicit boundaries. A transport
representation is not a source of semantic truth.

For every platform that a node or interface declares supported, its
architectural guarantees should remain stable. Platform-specific mechanisms may
differ, but each must provide the evidence required to preserve the declared
meaning.

A declaration alone does not establish support. A weaker platform fallback
must be explicit. Silent degradation makes the system's actual guarantees
unknowable.

## 4. Interaction and authority

An interface is a named and versioned contract for interacting with a
behavioral source. Invocation across a module boundary must use an explicit
exported interface. A non-invocation reference across nodes must use an
explicit source dependency.

A language-native call does not bypass the architectural boundary through
which it passes.

Behavioral sources own what interfaces do. Modules own who may use those
interfaces across their boundaries. This keeps behavior and authority from
becoming competing concerns inside one declaration.

### 4.1 Private by default

Behavior is private unless its owning module exposes it deliberately.
Filesystem visibility is not interface visibility. Discovery makes a module
findable; it does not make all of its contents public.

An import, file dependency, or content reference does not grant invocation
authority. Reading another node's behavior-shaping content is different from
being allowed to invoke one of its interfaces. Privileges are not inherited
from dependencies.

### 4.2 Context and discovery

For a human- or model-interpreted source, context is part of its effective
dependency surface. Giving a source unnecessary context creates opportunities
for unnecessary coupling.

Each source should therefore receive only the context needed for its decision.
Routing should select the source before its detailed instructions and
references are loaded. Required dependencies and state handoffs must remain
explicit.

Limited context is an encapsulation boundary. It is not a security boundary.

A discovery description states when a discoverable module applies. It is a
selection contract, not a summary of the module's workflow or implementation.
Detailed instructions belong behind the selected gateway.

### 4.3 Nested modules and authority across boundaries

A nested module is introduced only when a component needs an independently
addressable namespace, authority policy, or lifecycle. Ordinary behavioral
decomposition remains the responsibility of behavioral sources.

A nested module must be registered explicitly by its direct parent.
Registration establishes the containment topology. It does not expose the
child outside the parent or grant authority over the child.

When a skill separates human-language behavior from executable behavior, its
discoverable parent owns discovery and instructions. A non-discoverable runtime
child owns executable behavior and runtime authority. Each side remains
independently described and certified.

Authority may narrow as it crosses boundaries; it may not widen. A child's
export policy is the authority ceiling for the interface it exposes. Each
boundary crossed on the way to that interface may impose another restriction.

Namespace routing does not copy interfaces into an ancestor. It provides a
controlled route to the identity owned by the descendant.

An ancestor may expose a route to a descendant's canonical interface ID, but
it may not rename or copy that interface. Officina has no facade aliases:
callers address the module that owns the export, and every crossed namespace
may only narrow the terminal export's authority.

### 4.4 One resolution

Validation, projection, dispatch, tracing, and certification must not develop
separate interpretations of the same interaction.

They consume one canonical resolution that preserves:

1. the original caller;
2. the terminal target;
3. every boundary crossed; and
4. every restriction applied.

This prevents an interaction from being legal in one part of the system and
illegal in another because the two parts reconstructed authority differently.

## 5. The architectural graph

Blueprints state node-local facts. Taken together, those facts form the
machine-readable architectural graph.

Architectural facts and typed relationships include:

1. containment and direct ownership;
2. behavioral dependency;
3. interface definition and use;
4. module export and access control;
5. discovery; and
6. certification dependency.

These facts are not interchangeable. Containment bounds ownership. Interface
use records interaction. Export crosses an authority boundary. Certification
dependency records what assurance relies on.

One relationship may produce a derived relationship only when a canonical
rule specifies the derivation and the graph materializes its result.

### 5.1 Graph-level design

Making the structure machine-readable allows Officina to check design
properties that are difficult to maintain by convention.

The graph can, for example, reject:

1. overlapping ownership;
2. unresolved identities;
3. undeclared cross-boundary interaction;
4. authority that exceeds an export;
5. invalid containment; and
6. dependency cycles that prevent independent certification.

Additional design rules may be added when they follow from the same
architectural model. They should be checked against the canonical graph rather
than against a second model of the repository.

### 5.2 Change propagation

Change propagates through declared relationships, not through physical
proximity.

A node's local identity covers the behavior-relevant content it directly owns.
The identities of its dependencies remain separate. A dependency change can
therefore make the dependent's assurance suspect without changing the
dependent's local identity.

This distinction limits invalidation to the part of the graph that actually
relied on the changed state. Unrelated changes do not force unrelated semantic
review.

Node versions and interface versions remain distinct for the same reason. A
node may move or change internally without changing the contract seen by its
consumers.

## 6. Architectural authority and views

Contributors author architectural contracts. Generators produce views of those
contracts. Certifiers produce evidence about their conformity.

These artifacts may describe the same system, but they do not have the same
authority:

1. blueprints and other canonical contracts state architectural facts;
2. generated indexes, projections, and injected blocks present those facts for
   particular consumers; and
3. certificates record assurance about a particular state of those facts.

A generated view does not become an independent authority. A certificate does
not add relationships to the graph it certifies.

## 7. Rules and conformance

Officina uses a restrictive standard to make hidden coupling and ambiguous
authority invalid.

The graph makes architecture explicit. Standards state the rules that govern
the graph and the behavior it describes.

Officina defines rules that apply across the system. A node may define
additional local rules that govern itself. Each rule has one authoritative
standard: Officina owns system-wide rules; the node owns its local rules.

A new artifact type enters this conformance system rather than creating a
parallel conformance system.

The standards cover:

1. node identity and boundaries;
2. content ownership;
3. dependencies and interfaces;
4. authority and resource access;
5. the accuracy of blueprints;
6. the structure of the graph; and
7. the conditions under which assurance remains current.

### 7.1 Mechanical coverage and semantic remainder

Every rule must be designed for the greatest faithful mechanical coverage.

Mechanical requirements are those that can be decided reliably from the
repository and its graph. Semantic requirements are those whose truth depends
on meaning, including whether a blueprint faithfully describes its node.

1. The facts needed to check a rule must be explicit and machine-readable
   wherever possible.
2. Every mechanically decidable part of a rule must be implemented by a
   validator.
3. For each validator, the standard must identify:
   1. the assertion and aspects it checks;
   2. whether its coverage is full, partial, or supporting; and
   3. the limits of that coverage.
4. The standard must separately identify the semantic remainder and assign it
   to human or language-model review.
5. Mechanical success must not be presented as semantic assurance beyond the
   declared coverage.

A validator enforces a rule; it does not define one. A disagreement between a
rule and its validator is an implementation defect. The validator must be
corrected; it must not silently redefine the rule.

Neither form of checking does the work of the other. Semantic review is not
spent on facts a deterministic validator can establish. A validator does not
pretend to establish meaning merely because a document has the right shape.

The two forms of checking contribute to one certification result. They do not
create separate notions of conformity.

### 7.2 Consistent enforcement

The same rule must have the same meaning wherever it is enforced. Local
validation, continuous integration, certification, and runtime admission
consume the same architectural authority.

All validators must run through one canonical validator execution path.
Different gates may select different validators, but they must not create
different meanings for the rules.

Cheap violations should be caught as early as practical. Later gates may
require stronger assurance, but they must not reinterpret an earlier rule.

The state being checked must be the state being adopted. Passing checks against
different bytes does not justify a commit, release, or certificate.

## 8. Certification and drift

Mechanical validation and semantic review establish combined assurance.
Semantic review by humans and language models is expensive to repeat after
every repository change. Certification therefore records and retains the
combined assurance for the exact state reviewed.

A certificate remains current while the node, its relevant dependencies, and
the certification machinery remain in the certified state.

### 8.1 Issuance and currentness

Certification may be issued only when the repository state under audit
contains no relevant uncommitted changes. The issuance commit must represent
the bytes reviewed.

The source commit records the state reviewed at issuance. It is provenance,
not by itself a condition of later currentness.

Later currentness depends on the relevant node, dependency, and certification
basis state. It does not require the repository to remain at the issuance
commit.

Certification stamps an exact state. It does not assign a timeless property to
the node. A timestamp alone cannot establish that the evidence is current.

### 8.2 Local identity and dependencies

A node hashes what it owns and certifies what it depends on.

The local node hash covers the node's own behavior-relevant inputs. Dependency
identities are recorded separately in the certificate. Recursively including
dependency content in the local hash would obscure which node changed and
would spread invalidation further than necessary.

When a dependency changes, the dependent becomes suspect because the state on
which its review relied has changed. Its local identity remains unchanged.

### 8.3 The certification basis

Certification also depends on the machinery that established it. A change to
the standard, schemas, policies, validators, semantic checks, or evaluators may
change the meaning of an old certificate even when the node itself is
unchanged.

This machinery forms part of the certification basis. A relevant change to the
basis makes affected certificates suspect.

### 8.4 Retention and drift

A suspect certificate is retained. The latest retained certificate records
what was established. Drift evaluation reports why that certificate is not
currently accepted.

If every relevant part of the system returns to its certified state, the latest
certificate may become current again. An older historical entry remains
history and does not replace the latest entry.

Drift checking is read-only with respect to blueprints, certificates, and
authoritative certification state. It may produce derived reports. It
recomputes currentness from the same graph, hash, dependency, and
certification-basis rules used by the certifier. It does not repair blueprints,
repeat semantic review, or issue certification state.

Certification has one supported writer. The certifier reconstructs the
evidence from the reviewed state rather than signing a conclusion supplied by
its caller. Multiple supported writers or definitions of currentness would
create competing assurance states.

### 8.5 Certification and testing

Certification and ordinary testing establish different properties.

Certification establishes conformity between the node, its blueprint, and the
architectural standard. Unit, integration, and platform tests establish
development and operational behavior.

Tests do not issue certification state. Certification does not claim
performance, service availability, or the current state of a host machine.

### 8.6 Fail closed

Missing, malformed, unsupported, or suspect assurance is not approval. A claim
that Officina cannot evaluate fails certification rather than remaining
implicitly accepted.

Runtime admission depends on current certification, apart from bounded
bootstrap routes needed to establish certification itself. A fallback that
weakens assurance must be selected explicitly and must state what guarantee is
lost.

## 9. Evolution

An architectural migration should be a deterministic and reviewable transformation
from one valid state to another.

It should:

1. preserve authored facts unless a reviewed decision changes them;
2. separate structural movement from semantic change;
3. produce the same result in preview and application;
4. validate the exact transformed state; and
5. retire the old authority when the new one is adopted.

A breaking architecture change should leave one live architectural truth.
Long-lived mixed contracts create multiple interpretations of the same system
and weaken every rule spanning them.

Compatibility mechanisms should therefore be temporary and bounded. A
replacement retires its predecessor instead of coexisting with it
indefinitely.

## 10. Limits of assurance

Officina must not claim guarantees stronger than its trust model or mechanisms
can provide.

Cooperative same-user authority is not process isolation. Signatures and
certificate histories may detect specified drift and corruption within the
retained history without protecting against every process able to access the
same material.

Without an external anchor, retained history cannot prove that its newest
entries were not removed. Defense-in-depth mechanisms do not redefine that
boundary.

Certification applies to one explicit repository graph and reviewed state.
Installing a plugin does not enlarge the graph that was certified. External
installations may be inspected diagnostically without becoming nodes of the
certified package.

Compatibility claims must be specific and checkable. An uncheckable label
offers confidence without evidence. Compatibility does not imply performance
or current host availability.

Invalid, ambiguous, stale, or unsupported architectural state must fail with a
specific explanation. Visible failure is preferable to hidden weakening.

## 11. Documentation hierarchy

These principles are the normative architectural layer. Detailed documentation
may explain the mechanisms that implement them.

A mechanism document may be linked from a relevant principle. The link
explains how the principle is realized; it does not transfer normative
authority to the mechanism.

Principles must be explained before procedures, schemas, file layouts,
commands, or implementation choices. A mechanism may change without changing
its principle. It may not introduce a conflicting principle.
