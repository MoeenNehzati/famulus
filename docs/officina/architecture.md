# Famulus Architecture

> **Status:** Adopted version-6 architecture. The live schemas, validators, and
> implementation provide the executable details of this contract.

## Scope

Famulus treats code, instructions, schemas, configuration, and other
behavior-shaping artifacts as parts of one inspectable system. This document
defines the common architecture of that system: its nodes, physical content,
gateways, blueprints, interfaces, authority boundaries, dependency graph,
discovery, and certification.

More specialized documents refine this architecture:

- `docs/officina/skill-blueprints.md` explains concrete blueprint authoring;
- `docs/officina/certification_and_drift.md` defines certificate lifecycle and drift;
- `docs/officina/blueprint_search.md` defines graph-query behavior.

## Nodes

A **node** is a logically cohesive and encapsulated part of the codebase. Nodes
may depend on one another, but their dependencies and cross-boundary
interactions must be explicit.

Every node has:

- **content**: the files and directories in the node's containment scope;
- **a gateway**: one existing file in that scope through which the node is
  interpreted or invoked;
- **a language requirement**: the language or notation used by the gateway;
- **machine requirements**, optionally: the machines known to interpret or
  execute the gateway;
- **a blueprint**: the standardized, machine-readable description of the node.

The gateway is the node's **operational face**. The blueprint is its
**descriptive face**. Certification verifies that the descriptive face
accurately and completely represents the behavior available through the
operational face and the node's behavior-relevant content.

### Content and certification inputs

Content membership is hierarchical. A module's scope includes the scopes of
its contained behavioral sources, but each regular file has exactly one
**direct owner**: its most specific declared behavioral source, or the module
when no source owns it. Behavioral-source ownership cannot overlap between
siblings. Repository infrastructure such as registered blueprints and
certifier outputs may be outside node content even when stored below a module
root.

Containment scope is used for boundary and access checks; direct ownership is
used for writer authority, undeclared cross-node access, and ordinary hash
candidates. A module may explicitly bind its gateway to the gateway file of
one contained behavioral source. That shared gateway is in both containment
scopes but retains one direct owner, the behavioral source. A node blueprint
does not separately declare which content is hash-relevant.

The certifier derives each node's certification inputs from ownership, Git
state, non-configurable safety rules, and one projectwide ordered policy at
`references/certification/node-hash-policy.yaml`. The policy is validated by
the central `src/officina/common/configuration.schema.json`; its exact syntax
and examples belong in the existing `docs/officina/certification_and_drift.md`. The old
`references/certification/node-hash-policy.schema.json` remains only as frozen
certification-basis evidence for historical records.

The certifier loads and validates the policy once per repository. It checks
include-only `require_match` globally, then starts each node from its tracked,
directly owned regular files plus its blueprint and gateway. Sequential
Git-wildmatch `include` and `exclude` rules use normalized repository-relative
paths, with the last match winning. Includes may add ignored or untracked files
owned directly by that node, but never another node's content. Unowned paths,
boundary crossings, traversal, symlinks, special files, and reserved certifier
outputs are errors; an unmatched exclusion is a no-op.

The blueprint, gateway, and transitive same-owner closure of authored contracts
referenced by them are mandatory inputs. A cross-owner contract reference is a
certification dependency, not a local hash input. A policy rule that excludes a
mandatory input is an error. Certificates, certificate histories, signing
material, and other certifier outputs are non-configurable forbidden inputs.
Logs, caches, runtime state, and generated output are excluded
through project policy unless a later rule deliberately re-includes an eligible
directly owned regular file.

The resolved manifest records repository-relative path, digest, and Git
provenance (`tracked`, `ignored`, or `untracked`). At issuance, tracked inputs
and the blueprint must match the certificate's `source_commit`; included
ignored or untracked files are signed local-state claims that must stay
unchanged during certification. `source_commit` therefore records the snapshot
needed to reproduce the tracked subset when local inputs exist. Later
currentness does not require current `HEAD` to equal that issuance commit.

Repository paths have filesystem identity, not textual identity. The same
checkout may have equivalent absolute spellings, such as `/Users/...` and
`/private/Users/...` on macOS. Repository-aware code must derive relative paths
through `officina.common.repository_paths`, which accepts lexical containment
or an equivalent-root ancestor without resolving descendants. Callers then use
the selected repository root's spelling for Git keys, diagnostics, and
confined reads; ad hoc lexical `Path.relative_to()`, descendant resolution, or
string-prefix comparisons are not repository containment checks. Authored
repository text is UTF-8 with LF line endings in Git so hashes, generated
views, and diagnostics are host-independent. Code that decodes authored
repository text must request UTF-8 explicitly rather than inherit the host
locale.

The local node hash covers canonical node identity and blueprint data plus the
paths and exact bytes of the resolved inputs. Dependency hashes remain
separate certificate data: a dependency change invalidates certification but
does not change the dependent's local node hash.

When a gateway evaluator discovers implementation files or invoked interfaces
dynamically, every discovered item must resolve to one of three existing
authorities: a directly owned certification input, an explicit certification
dependency, or the certification basis. An unmapped discovered item is a
certification error. Before any certificate append, the certifier derives this
mapping twice over the selected dependency closure and requires identical
results. The certificate records the versioned passed check, not the
implementation-path mapping itself.

Certificate currentness also binds a signed `certification_basis_hash` covering
the project node-input policy, certifier, schemas, hash/safety implementation,
checks, binding compilers, and machine evaluators. Any change to the policy or
another basis component, node, dependencies, input manifest, signature, or
history link makes the certificate suspect.

Certification evaluates whether gateway-language, gateway-machine,
runtime-dependency, and platform declarations accurately describe the node's
content and behavior. These are blueprint-correctness checks, not performance
or availability tests of the host running the certifier. Their versioned review
results belong in `checks`; certificates contain no host-runtime
`machine_evidence`.

## Gateways

In the initial architecture, a gateway is exactly one existing file, and the
whole file is the gateway. A gateway cannot identify a symbol, section, line
range, or other sub-file fragment. A later schema version may add logical
sub-file addressing without changing the meaning of existing whole-file
gateways.

That restriction applies to gateway identity, ownership, and hash scope. An
interface's gateway binding may still select an invocation entry within the
file, such as a Python class, when the language provider requires one. The
selector is an invocation detail and does not create another gateway or
behavioral source.

The gateway path already identifies the file's physical representation through
its suffix. The blueprint therefore does not repeat a `format`, media type, or
file-extension field. The language requirement supplies semantic information
that the path alone cannot provide. For example, two `.json` files may contain
ordinary JSON and JSON Schema, while two `.md` files may contain human
documentation and instructions intended for an LLM.

Gateway language and machine requirements use `X`, `X==1.2`, or intersections
such as `X>=1.2,<2`, with operators `==`, `>=`, `>`, `<=`, and `<`. Ranges are
valid only for ordered version families; named or date-based editions normally
use equality. Exact grammar and examples belong in the requirement schema and
blueprint-authoring documentation.

The language requirement describes the gateway's actual language or notation;
it does not imply that Famulus has defined a common Codex-Claude language.
Entries in `machines` are alternatives: each listed machine is claimed to be
capable of consuming the gateway. Each compatibility claim requires its own
certification evidence. When a machine exposes a stable version, certification
records the exact evaluated version even if the blueprint uses a range or bare
name.

## Blueprints

Every node has one blueprint: its authoritative structured description in the
Famulus graph. As applicable, it records identity and purpose; content and
gateway; containment and dependencies; interfaces, bindings, and access;
inputs, outputs, effects, outcomes, and external resources; privileges and
filesystem authority; discovery; and certification requirements. Certification
evidence belongs to the certificate, not the blueprint.

Schema validity and certification are distinct. A schema-valid blueprint may
be an uncertified draft: it must have enough identity, gateway, containment,
and relationship structure to enter the graph, but semantic facts that cannot
be recovered mechanically may still be absent. Absence never means approval
or an implicit default. It is a certifier finding. Runtime routing and
authorization remain fail-closed on blueprint policy, while missing, expired,
stale, malformed, unavailable, or unknown certification status is surfaced as
a warning and does not deny an otherwise authorized invocation.

Migration therefore has two stages. The converter losslessly moves every
authored fact into the generic module, source, interface, process-binding, and
direct-I/O vocabulary without inventing behavior. The certifier-owned workflow
then audits the draft against its gateway and content, repairs missing or
incorrect descriptive facts, reloads the graph, and repeats until the final
blueprint is exact or certification fails. No certification status is authored
in the blueprint; the certificate is the only persisted certification state.

Blueprints point to facts owned by other blueprints rather than copying those
facts. This single-owner rule prevents a module export and its implementing
behavioral source from becoming competing authorities for the same interface
contract.

Blueprint identity and placement follow these rules:

- a module blueprint is `<module-root>/blueprint.yaml`;
- behavioral-source blueprints are
  `<module-root>/blueprints/<local-source-id>.yaml`;
- module IDs are globally unique;
- behavioral-source IDs are `<module-id>.source.<local-source-id>`;
- source-interface IDs are
  `<behavioral-source-id>.interface.<local-interface-name>` and are private to
  the containing module unless exported;
- exported interface IDs are `<module-id>.interface.<export-name>`;
- module and behavioral-source content and gateway paths are relative to the
  module root;
- `module.exports` contains only externally addressable interfaces, including
  restricted exports;
- a skill's `SKILL.md` is normally both the module gateway and the gateway of
  `<module-id>.source.gateway`, which directly owns the file;
- an export's version is derived from its bound source-interface version and is
  never an independently authored competing value.

Repository inventory discovers modules through canonical
`<module-root>/blueprint.yaml` markers, not through a `skills/*` path
assumption. The bounded marker walk excludes registered infrastructure and
ignored working directories, rejects duplicate identities, nested module
roots, and marker/identity collisions, and follows behavioral-source
blueprints only from each accepted module's `blueprints/` directory. Placement
does not imply host discovery: modules without a `discovery` declaration are
reachable only through explicit graph relationships.

## Node kinds

There are two node kinds: **modules** and **behavioral sources**.

### Modules

A module is an encapsulation, ownership, and authority boundary rooted at a
directory. Its containment scope includes every contained behavioral source.
Its directly owned content is the subset not directly owned by a contained
source. Files registered as repository infrastructure need not belong to a
node merely because they reside below the module root.

A module owns its external identity, namespace, privileges, resource authority,
cross-module access, exports, and discovery. An export adds public identity, a
source-interface binding, and unrestricted or restricted access; it never
duplicates the source contract. An unexported source interface is internal to
the module.

### Behavioral sources

A behavioral source is a cohesive behavioral implementation unit contained
within exactly one module. Its content consists of one or more files, exactly
one of which is its gateway. In the simplest case, the behavioral source
contains only its gateway file.

A behavioral-source blueprint owns the source's intrinsic behavior, interface
contracts, dependencies, inputs, outputs, effects, and external actions. These
facts must be complete before certification, but a mechanically converted
uncertified draft may omit facts that require semantic review. Sources have no
independent privileges: their actions must fit both
the applicable interface contract and their module's authority. As defined
under Content, a source may directly own a gateway also referenced by its
module.

When a source has machine compatibility claims, its blueprint declares
`platform_support` and `runtime_dependencies` together at the source level.
The pair covers the gateway implementation and every intrinsic interface; an
interface cannot carry a narrower competing copy. Omitting both fields means
the source makes no source-wide platform/dependency declaration. A process
binding may provide a non-empty provider-specific `entry` selector, but the
selector is invocation mechanics and does not narrow the source gateway.

## Interfaces and boundaries

An **interface** is a named contract for interacting with a behavioral source.
A behavioral-source blueprint defines the contract. A module blueprint may
export that interface across the module boundary.

An interface contract must explain use without implementation inspection,
including identity/version, invocation, inputs/outputs, preconditions/outcomes,
effects, lifecycle, and interface-specific machine capabilities as applicable.

Caller authorization is not part of a source-owned interface contract.
Cross-module authorization belongs only to the target module's export or
namespace policy. A runtime call identifies the immediately calling module;
the graph resolves the target export to the exact implementing behavioral
source. A source may separately declare `uses_interfaces` or an exact source
dependency as static relationship and certification metadata. Neither grants
runtime interface access or privilege. One common interface contract describes
inputs, outputs, preconditions, outcomes, effects, and lifecycle across gateway
languages. An optional gateway binding describes only how those contract
values map onto the gateway's invocation mechanics.

Gateway language, machine compatibility, and interface binding are orthogonal.
The initial structured binding is the existing process binding for argv/stdin,
language-provider entry selection, output channels and framing, exit signals,
and cancellation; it applies across executable gateway languages. A
natural-language whole-file gateway needs no additional binding object. New
binding kinds require concrete mechanics that the common contract and gateway
do not already express.

An interface may designate another authorized interface as a bounded helper and
bind its arguments. Helper role, authorization, finite-cardinality/read-only
constraints, resolved definitions, and projection-size limits remain explicit
contract and graph facts; a generic interface namespace does not erase them.

Behavioral sources within the same module may interact through their internal
gateways and interfaces. Famulus-mediated interaction across modules must use
an interface exported by the target module, including when the physical
mechanism is a language-native import. An authored non-invocation reference may
instead create an exact cross-module source dependency, but does not bypass
module authority or expose the target source as a public Famulus interface.

### Direct dispatcher resolution

The operational contract and examples are collected in
[Dispatcher](dispatcher.md).

The launcher supplies the exact repository configuration path. That file owns
the ordered module roots. The dispatcher derives caller and target paths from
their dotted module IDs, opens only their registered ancestry and the selected
behavioral-source blueprint, evaluates crossed namespace and terminal access
policies, compiles the process binding, and launches it.

Host dispatch is read-only and fail-closed. It does not inventory roots, load a
repository-wide graph, inspect Git, derive certification, repair blueprints,
synchronize generated artifacts, build a manifest, cache a route, or write
routing state. Missing, malformed, ambiguous, or unauthorized relevant state
is an error. Certification currentness is separate and warning-only; it cannot
grant permission or deny an otherwise authorized route.

Modules may register direct child modules. Registration establishes physical
containment and makes the child addressable inside the registered subtree; it
does not expose that namespace outside the parent. A `namespace_exports`
declaration routes selected child or descendant interface IDs across the parent
boundary without renaming them. Every target-side boundary crossed from outside
its subtree needs its own namespace route.

Version 6 has no facade exports. A callable interface is exported by the module
that owns it. A parent can expose the child's existing fully qualified ID
through an explicit namespace route, but cannot rename it or copy its contract.

Caller allowlists use either globally unique module IDs or Python-style
leading-dot references resolved against the certified registration tree.
`._rtx` names a module's code child; `..parser` names its sibling `parser`.
Each reference identifies a registered module subtree: it admits that module
and its descendants. Naming a child does not admit its parent or siblings.
Thus `A/B` may call `A/C` only when `A/C` is public or names `A`, `A/B`, or an
ancestor of `A/B`. An empty false allowlist is private to the owning module;
the owner always admits itself.

The interaction path for a nested call is:

```text
calling behavioral-source gateway
  -> declared cross-module interface use
  -> each crossed parent namespace route
  -> child module export
  -> child behavioral-source gateway
```

The dispatcher or equivalent boundary mechanism checks the immediate caller at
each namespace route and the terminal export. After a namespace owner accepts
a call, that owner—not the original upstream
caller—is the caller of the next hop. No caller chain or source identity is
propagated as permission. Source-level `uses_interfaces` agreement is a static
graph invariant; the public dispatcher does not need to trust a caller-supplied
source identity. The implementation may collapse redundant local routing
steps, but the graph must preserve the same hop-local authority checks and
contract ownership.

## Discovery

Discovery is a module property, not a separate node kind. A module that can be
found without an explicit dependency declares a discovery mechanism in its
blueprint. Skills are autodiscoverable modules whose host-facing convention
uses `SKILL.md` as the module gateway.

A discovery declaration identifies the mechanism and carries the skill catalog
contract. `catalog.domain` gives the skill one stable documentation section,
`catalog.topics` supplies nonempty cross-cutting filters, and
`catalog.visibility` controls whether generated user documentation features,
lists, or hides it. `activated_by` declares one or more supported initiator
classes (`user-request`, `skill-workflow`, or `scheduled-job`).
`persistent_modifier` states whether invocation intentionally changes assistant
behavior beyond the activating invocation; when true, the catalog must include
`reasoning-control`. These are discovery and documentation semantics, not
authorization. Named callers and actual dependency relationships remain graph
edges. A Boolean `autodiscoverable` flag alone is insufficient because it does
not explain how discovery occurs.

The authoritative meanings and selection rules for configured discovery values
are documented in [Blueprint discovery metadata](blueprint-discovery-metadata.md).
The schema guarantees structural validity and mechanically decidable
cross-field rules. Semantic review remains responsible for whether a valid
domain or topic truthfully describes the module.

## Graph

Combining the blueprints produces the repository graph. The graph contains:

- module-containment edges;
- registered-child and namespace-route edges;
- interface-definition and module-export edges;
- behavioral-source dependency edges;
- interface-use edges;
- access-control relationships;
- discovery declarations;
- certification dependencies.

Containment and interface use have different meanings. Containment assigns
ownership and authority. An interface-use edge grants or records interaction;
it does not transfer ownership. Cross-module reachability is valid only through
an authorized exported-interface path.

The certification graph is the following acyclic projection of that repository
graph:

```text
behavioral_source --uses-source--> behavioral_source
behavioral_source --uses-private-interface--> sibling behavioral_source
behavioral_source --uses-export--> implementing behavioral_source
node --references-cross-owner-contract--> owning node
```

Containment is ownership-only and adds no certification edge in either
direction. A public export is admitted only when both descriptive faces are
current: the exporting module certificate covers the boundary identity,
binding, and access declaration, while the exact implementing behavioral-source
certificate covers the behavior. The consumer's `uses-export` dependency lands
on that implementing source, so unrelated source drift in the exporting module
does not invalidate the consumer. Mutual source uses that create a certification
cycle remain invalid and must be rejected before certification.

Derived analysis overlays, including `implicit_dependence` from logical-resource
flow, do not affect certification currentness unless an approved contract
explicitly promotes one of their edges into the certification graph.

## Certification and drift

Certification keeps a node's descriptive and operational faces aligned through
structural, graph, ownership, authorization, semantic, gateway-language/binding,
and machine-compatibility checks, then records the node and dependency
identities. Admissibility is a phase of certification, not a parallel authority;
only certification establishes blueprint accuracy.

Node authors do not supply conformance manifests, probes, fixtures, adapter
seams, or independent admissibility results. The certifier owns its check
catalog and semantic review procedure. A claimed gateway language, binding, or
machine without a supported certifier check fails certification; it is never
accepted as unevaluated. This migration does not create a general behavioral
probe framework. Ordinary unit and integration tests remain separate
development checks and do not issue certification state.

The certifier workflow may edit a blueprint to resolve findings, but the
signing core never accepts a caller-authored payload or treats a repair as
evidence. After every repair it reloads schema, graph, ownership, dependency,
and semantic state. Only a clean final pass is hashed, signed, and appended.

Certification uses one cooperative same-user writer: the existing certificate
writer, renamed to `skill-certifier`. It owns signing and certificate writes by
architecture contract; `skill-drift` remains read-only and verifies with the
public key. Every node has one append-only certificate log. Each complete entry
contains a payload and its signature; the signature covers the payload only,
so no self-reference is introduced. The final complete valid entry is current
and preceding entries are history. Restrictive user-only permissions,
atomic/no-follow writes where available, explicit opt-in non-atomic fallback,
and post-write verification are defense-in-depth, not a
filesystem-enforced boundary between processes running under the same UID.

Within that cooperative contract, signatures and currentness checks detect
drift, corruption, and changes outside the cooperative writer contract. They
do not defend against a malicious same-UID process that can access signing
material or certificate outputs. The architecture introduces no broker,
service identity, second writer, or parallel signing path.

Drift checking recomputes the node hash, dependency manifest, and certification
basis and validates the signed evidence, including the expected versioned check
records. It does not rerun gateways, independently redefine the node, perform
semantic conformance review, or repair its blueprint.

## Package boundary

Certification applies to the module graph in one explicit reviewed repository
checkout and commit. Installed-source adapters may inspect direct installations
or host-authorized plugin records for read-only drift diagnostics, but installing
a plugin does not extend the package's certifiable graph. A plugin is a consumer
of the installed package, not an additional part of that package.
