# Logical Resource Addressing and Implicit Dependence Design Proposal

Status: deferred; not implementation-ready.

The resource model has merit, but this proposal predates the repository's
current architecture. Before implementation, re-audit its proposed files,
interfaces, schemas, validators, graph integration, and certification boundary
against the live repository. The file map and task list below preserve useful
design detail; they are not verified execution instructions.

**Goal:** Define one canonical logical-resource language for local filesystems, Google Drive accounts, and calendar accounts, then use overlapping write/read patterns to generate evidence-backed `implicit_dependence` edges and mixed dependency reachability.

**Architecture:** One repository-owned registry defines system kinds, inferred resource types, reusable path snippets, and the three current logical instances. Blueprint `direct_io` entries name resources with `resource://` patterns. A focused `src/officina/resources/` package loads the registry, parses and evaluates patterns, decides ordered node-pair influence with evidence, derives a separate resource-flow overlay, and answers reachability over the union of authored direct dependencies and generated resource dependencies without adding derived edges to the certification graph in this version.

**Tech Stack:** YAML registries, JSON Schema, Python dataclasses and pure matching functions under `src/officina/common/`, typed and legacy blueprints, pytest, repository validators.

## Global Constraints

- Use forward slash `/` as the only hierarchy separator.
- A concrete service instance is tied to exactly one active root or credential/account binding. The `local` instance denotes the evaluating host's filesystem namespace.
- Instance keys are stable resource identities, not display names. Renaming an instance is an explicit identity migration.
- Repository instance declarations contain no tokens, credentials, account addresses, absolute host paths, or other secrets. Existing service-owned configuration supplies operational bindings.
- A shared skill may quantify over instances with a kind-constrained authority variable such as `{account:calendar-account}`.
- A concrete resource URI has no variables. A resource pattern may contain only the variable forms defined below.
- The resource language does not support regular expressions, globs, optional segments, alternation, query strings, fragments, user information, or ports.
- `direct_io.content` remains a coarse user-facing description such as `file` or `calendar-event`. Resource patterns provide identity and matching precision; they do not introduce field-level `content` values.
- The `cloud-files` instance is the logical path namespace exposed by the current `cloud-files` contract beneath its configured Google Drive root. Raw Google Drive object identity is outside v1.
- A resource-mediated influence test is directional: the first node supplies writes, the second node supplies reads. A dependency edge derived from that evidence points `second -> first`.
- The generated relation is named exactly `implicit_dependence`.
- Direct and implicit edges use the same dependency orientation: the dependent node is the source and the dependency is the target. Thus `first` may transitively influence `second` exactly when a nonempty mixed dependency path runs from `second` to `first`.
- Authored resource declarations are blueprint facts. The target certification design may hash them with the rest of the blueprint. Generated `implicit_dependence` edges are recomputed evidence and are not node-hash inputs or certificate dependency edges in this version.
- Resource-flow edges may contain cycles. Certification-graph acyclicity must not be applied to the resource-flow overlay.

---

## Draft Standard, Version 1

### 1. Terms

- **System kind:** A named resource grammar shared by conforming instances, such as `calendar-account`.
- **System instance:** A stable project key naming one logical binding slot, such as `g-calendar`.
- **Resource type:** The object class inferred from the instance kind and matched path, such as `calendar-event`. It is not repeated in a blueprint entry.
- **Resource URI:** One concrete logical object address, such as `resource://g-calendar/calendars/work/events/abc123`.
- **Resource pattern:** A URI-shaped set of possible resource URIs containing `{}` variables.
- **Resource snippet:** A registry-owned, named path pattern that authors can reuse consistently.
- **Active binding:** Existing service-owned configuration that connects an instance key to its operational filesystem, root, or credential set.
- **Implicit dependence:** A generated `implicit_dependence` edge `reader -> writer` supported by an overlap between one effective read pattern and one effective write pattern.
- **Resource influence:** The directional proposition that the first node may change a resource the second node may read.
- **Mixed dependency path:** A nonempty path composed of any sequence of canonical direct dependency edges and generated `implicit_dependence` edges.

### 2. URI Shape

Every concrete resource URI has this form:

```text
resource://<instance>/<kind-specific-path>
```

The scheme is always `resource`. The authority is one registered instance key. The path must match one named resource pattern belonging to that instance's system kind.

Examples:

```text
resource://local/$repo/docs/report.md
resource://cloud-files/plans/7-17-26.md
resource://g-calendar/calendars/work@example.com/events/a1b2c3
```

Concrete values containing reserved URI characters are percent-encoded within their segment. Matching operates on canonical decoded segment values, while `/` always remains the hierarchy separator.

### 3. Pattern Variables

Version 1 defines exactly three forms:

| Form | Position | Meaning |
| --- | --- | --- |
| `{name}` | One path segment | One nonempty substring; a literal prefix or suffix may occur in the segment |
| `{name...}` | Final path position only | One or more path segments |
| `{name:system-kind}` | Authority only | Any registered active instance of the named kind |

Rules:

1. Variable names match `[a-z][a-z0-9-]*` and are unique within one pattern.
2. A segment contains at most one `{name}` variable. Forms such as `{date}.md`, `categories.{name}.yaml`, and `{name}_blueprint.yaml` are valid.
3. Literal instance keys and standard collection segments use lowercase names.
4. `{name...}` must occupy the complete final segment and cannot be followed by another segment.
5. An authority variable must include a system-kind constraint; unconstrained `resource://{instance}/...` patterns are invalid.
6. Braces are syntax only in patterns and must not occur in a concrete resource URI.

Examples:

```text
resource://{filesystem:local-filesystem}/{path...}
resource://{drive:google-drive-root}/plans/{date}.md
resource://{account:calendar-account}/calendars/{calendar}/events/{event}
```

The last pattern means that an interface can operate on events through any conforming calendar-account binding. It does not mean that one installed interface is simultaneously authorized for every declared calendar account.

### 4. Canonical System Kinds

The complete initial registry is drafted in `docs/plans/logical-resource-registry.yaml`. After approval, its canonical machine-readable location is `references/resources/resource-registry.yaml`.

| System kind | Inferred resource type | Canonical path grammar | Evaluator |
| --- | --- | --- | --- |
| `local-filesystem` | `filesystem-entry` | `{path...}` | Expand `$repo`, `$home`, or `$tmp` when present, normalize the concrete local path, then compare canonical URI segments |
| `google-drive-root` | `file` | `{path...}` relative to the configured root | Normalize a logical relative path; the owning adapter must resolve it deterministically |
| `calendar-account` | `calendar-collection` | `calendars` | Exact collection identity |
| `calendar-account` | `calendar` | `calendars/{calendar}` | Calendar ID occupies one encoded segment |
| `calendar-account` | `calendar-event` | `calendars/{calendar}/events/{event}` | Calendar and event IDs occupy separate encoded segments |

Resource type is inferred after the authority resolves to a system kind. A blueprint never repeats it. Every accepted path must match exactly one resource type within that kind. Named snippets are authoring vocabulary and do not create additional resource types.

#### Local filesystem

The `local` instance denotes the evaluating host's filesystem namespace. Fixed declarations preserve the existing leading macros `$repo`, `$home`, and `$tmp`; evaluation expands them before canonical comparison. Caller-supplied paths use `resource://local/{path...}` until a concrete argument is available. `.` segments, `..` segments that escape a bounded root, empty interior segments, and encoded separators are invalid.

The current predefined local snippets are:

```text
$repo/{path...}
$home/{path...}
$tmp/{path...}
$home/.config/cloud-files/client.json
$home/.config/cloud-files/credentials.json
$home/.config/cloud-files/config.json
$home/.config/g-calendar/client.json
$home/.config/g-calendar/credentials.json
$home/.cache/datalab/models/{path...}
$repo/skills/{skill}/blueprint.yaml
$repo/references/blueprint/schema.annotated-draft.json
$tmp/{name}_blueprint.yaml
$repo/skills/list-manager/tmp/categories.{name}.yaml
```

#### Google Drive

The current public abstraction is not raw Google Drive. `cloud-files` owns one configured Google Drive LLM root and exposes deterministic logical paths beneath it, including `lists/{path...}` and `plans/{path...}`. The `cloud-files` instance therefore uses kind `google-drive-root`, and the canonical patterns are:

```text
resource://cloud-files/{path...}
resource://cloud-files/lists/{path...}
resource://cloud-files/plans/{path...}
resource://cloud-files/plans/{date}.md
resource://cloud-files/plans/{date}.meta.json
```

This is a logical adapter identity: the owning adapter must ensure that one logical path resolves deterministically. The standard does not claim that raw Google Drive display-name paths are unique; the official [Drive file resource](https://developers.google.com/workspace/drive/api/reference/rest/v3/files) explicitly says names need not be unique within a folder.

#### Calendar

A calendar-account instance represents one authenticated account binding. It can expose primary, secondary, subscribed, or shared calendars. The official [CalendarList resource](https://developers.google.com/workspace/calendar/api/v3/reference/calendarList) describes this as the calendars in the authenticated user's calendar list. Those calendars are resources inside the account instance rather than separate account instances. Events are nested beneath the calendar because the official [Events resource](https://developers.google.com/workspace/calendar/api/v3/reference/events) defines an event ID as unique per calendar.

The current canonical patterns are:

```text
resource://g-calendar/calendars
resource://g-calendar/calendars/{calendar}
resource://g-calendar/calendars/{calendar}/events/{event}
```

### 5. Project System Instances

The project registry declares exactly these current logical instances:

```yaml
system_instances:
  local:
    kind: local-filesystem
    owner: officina
    provider: execution-host

  cloud-files:
    kind: google-drive-root
    owner: cloud-files
    provider: google-drive

  g-calendar:
    kind: calendar-account
    owner: g-calendar
    provider: google-calendar
```

`local` is evaluated from the current host plus `$repo`, `$home`, `$tmp`, or concrete caller arguments. `cloud-files` means the single OAuth account and configured remote LLM root owned by the current `cloud-files` service. `g-calendar` means the single active calendar account currently supported by `g-calendar`. The registry records only these logical identities; credentials, account addresses, absolute roots, and remote folder IDs remain in service-owned configuration.

Adding another Drive root or calendar account creates another stable instance key. Replacing the operational account behind an existing key is a deliberate rebind of that logical slot; every interface using the key moves together.

### 6. Blueprint Declarations

`resource` becomes the canonical identity/matching field for addressable reads and writes:

```yaml
direct_io:
  reads:
    - resource: resource://{account:calendar-account}/calendars/{calendar}/events/{event}
      medium: network-request
      access: read
      content: event
      sensitivity: user-private
  writes:
    - resource: resource://{account:calendar-account}/calendars/{calendar}/events/{event}
      medium: network-request
      access: write
      content: event
      sensitivity: user-private
  network: []
```

During migration, existing `system`, `path`, and `path_match` fields remain accepted for compatibility. They do not participate in logical-resource matching once `resource` is present. The target standard removes their resource-identity role after all supported blueprints migrate; `medium`, `access`, `content`, `format` or `formats`, `auth`, `sensitivity`, and `reason` remain descriptive metadata.

### 7. Validation

A resource declaration is valid only when:

1. it parses under the version-1 grammar;
2. its literal authority names one registered instance, or its authority variable names one registered system kind;
3. its path matches exactly one resource type of that kind after named snippets are ignored;
4. every concrete literal instance has the kind required by the matched path grammar;
5. local root macros occur only as the first path segment of a `local-filesystem` resource;
6. percent encoding is canonical and does not hide a separator or traversal segment; and
7. no registry pair makes one concrete resource URI match two different resource-type definitions.

The registry validator fails closed on ambiguous resource grammars.

### 8. Pattern Overlap

Two resource patterns overlap when at least one concrete resource URI belongs to both sets.

The version-1 unifier applies these rules:

1. Two literal authorities overlap only when they are the same instance key.
2. A literal authority and a kind-constrained authority variable overlap only when the registry assigns that literal instance to the constrained kind.
3. Two kind-constrained authority variables overlap only when they name the same system kind.
4. Equal literal path segments overlap.
5. A segment template such as `{date}.md` overlaps a literal or another template only when their literal prefixes and suffixes permit at least one nonempty variable binding.
6. Two identical local root macros overlap. Different root macros are expanded before comparison when evaluation context is available; otherwise their relationship is unresolved rather than assumed disjoint.
7. Different incompatible literal segments do not overlap.
8. Final `{name...}` overlaps a compatible nonempty remaining suffix.
9. Path patterns that consume different segment counts do not overlap unless a valid final tail variable accounts for the difference.

Examples:

```text
resource://{account:calendar-account}/calendars/{calendar}/events/{event}
resource://g-calendar/calendars/work/events/{event}
```

overlap because `g-calendar` is a `calendar-account` and `work` can bind `{calendar}`.

```text
resource://g-calendar/calendars/work/events/{event}
resource://g-calendar/calendars/personal/events/{event}
```

do not overlap.

### 9. Ordered Node-Pair Influence Evaluation

The public API lives in `src/officina/resources/influence.py`. Registry, graph, and binding context are loaded once into an evaluator; the ordered-pair methods then take only the two nodes:

```python
@dataclass(frozen=True)
class ResourceInfluenceResult:
    may_influence: bool
    certainty: Literal["none", "possible", "resolved"]
    witnesses: tuple[ResourceInfluenceWitness, ...]


class ResourceInfluenceEvaluator:
    def evaluate(
        self,
        first: BlueprintNode,
        second: BlueprintNode,
    ) -> ResourceInfluenceResult: ...

    def may_influence_through_resources(
        self,
        first: BlueprintNode,
        second: BlueprintNode,
    ) -> bool:
        return self.evaluate(first, second).may_influence

    def direct_dependency_path(
        self,
        dependent: BlueprintNode,
        dependency: BlueprintNode,
    ) -> DependencyPath | None: ...

    def mixed_dependency_path(
        self,
        dependent: BlueprintNode,
        dependency: BlueprintNode,
    ) -> DependencyPath | None: ...

    def may_influence_transitively(
        self,
        first: BlueprintNode,
        second: BlueprintNode,
    ) -> bool:
        return self.mixed_dependency_path(second, first) is not None
```

`first` is the potential influencer and `second` is the potentially influenced node. Evaluation compares every effective write of `first` with every effective read of `second`. Effective accesses include immediate authored `direct_io` plus accesses inherited through `uses_interfaces`, retaining the originating interface in each witness. `network` entries describe transport and are not compared as resource objects.

`may_influence_through_resources` identifies only a one-hop resource-mediated channel. `direct_dependency_path` traverses only the canonical authored `BlueprintEdge` relations. `mixed_dependency_path` traverses the union of those direct edges and generated `implicit_dependence` edges. Delete and replacement operations are write-like because they can change what a later reader observes.

Formally, let `E_direct` be the canonical direct dependency edges and `E_implicit` the generated `implicit_dependence` edges. The mixed graph is `G_mixed = (V, E_direct ∪ E_implicit)`. The proposition "`first` may transitively influence `second`" holds exactly when `G_mixed` contains a nonempty path `second -> ... -> first`.

Both path methods follow dependency orientation: `dependent -> ... -> dependency`. They return one deterministic shortest nonempty path, using breadth-first traversal and a stable edge order. A node does not influence itself merely through a zero-edge path. The mixed graph may contain cycles, so traversal tracks visited nodes and does not apply certification-graph cycle rejection.

Each `DependencyPath` retains every step's source, relation, target, and evidence. A mixed path has `certainty: resolved` when all of its implicit steps are resolved and `certainty: possible` when at least one implicit step is possible; direct steps do not lower certainty. This gives diagnostics the exact alternating chain rather than only a boolean transitive-closure result.

Each witness records the originating writer interface, originating reader interface, both authored resource patterns, resolved resource types, variable bindings or constraints, and certainty. The boolean method delegates to the evidence-returning method so graph generation and diagnostics cannot disagree.

### 10. `implicit_dependence` Evidence

For every ordered node pair `(first, second)`, call `evaluate(first, second)`. When it returns `may_influence: true`, materialize the dependency-oriented edge `second -> first`. The evidence record is:

```yaml
relation: implicit_dependence
source: reader.interface.id
target: writer.interface.id
evidence:
  reader_resource: resource://g-calendar/calendars/work/events/{event}
  writer_resource: resource://{account:calendar-account}/calendars/{calendar}/events/{event}
  instance_constraints:
    account: g-calendar
  certainty: resolved
```

Use `certainty: resolved` when registry evaluation identifies one shared literal instance and a concrete overlap witness. Use `certainty: possible` when overlap depends on two unresolved kind variables, caller-supplied paths, or local macros that cannot yet be normalized. Possible edges remain evidence candidates and must not be promoted silently to certification dependencies.

The resource-flow overlay is regenerated from blueprints, registries, and active bindings. It is never hand-edited.

### 11. Certification Boundary

The authored `resource` declaration describes node behavior and belongs to the blueprint. The generated `implicit_dependence` edge is an observation derived from two declarations plus project binding state. Mixed paths are derived queries over direct and implicit edges, not new stored edges. Version 1 therefore:

- includes no generated edge in a blueprint;
- includes no generated edge in `node_hash(x)`;
- does not add generated edges to the acyclic certification graph;
- does not mutate or replace canonical direct `BlueprintEdge` relationships when answering mixed reachability;
- records enough evidence to reproduce each match; and
- defers promotion rules, separate overlay hashing, and drift treatment until the overlay has real repository results.

### 12. Storage and Enforcement

The design separates normative explanation, machine vocabulary, and operational secrets:

1. `references/skill-standards/logical-resources.standard.yaml` is the canonical version-6 prose standard. Its generated Markdown view is `references/skill-standards/logical-resources.md`.
2. `references/resources/resource-registry.yaml` is the canonical machine vocabulary containing system kinds, inferred resource types, snippets, and project instance identities. The approved content begins from `docs/plans/logical-resource-registry.yaml`.
3. `references/resources/resource-registry.schema.json` validates the registry's structural shape.
4. Existing skill-owned files remain the operational bindings: `cloud-files` owns its OAuth account and configured Drive root; `g-calendar` owns its one active calendar account; the shared evaluator receives local host roots from runtime context. No second credential registry is introduced.

Enforcement has four layers:

- JSON Schema validates registry and blueprint field shape.
- `src/officina/resources/registry.py` enforces cross-reference integrity, unique resource-type inference, snippet validity, and instance-to-kind agreement.
- `src/officina/resources/patterns.py` enforces URI syntax, variable placement, macro rules, normalization, and overlap semantics.
- `skills/skill-maker/validators/blueprints.py` and a repository validator call the shared implementation; `.githooks/pre-commit` and `.githooks/skill/check-blueprints` make failures commit-blocking.

This enforcement proves that declarations are well-formed, mutually interpretable, and mechanically matchable. It does not prove runtime confinement. Dispatcher argument constraints, service-owned adapters, credentials, and ordinary behavior certification remain responsible for whether implementation behavior agrees with the declaration.

## File Map

- `docs/plans/logical-resource-registry.yaml` — reviewable draft of every current registry definition.
- `references/resources/resource-registry.yaml` — canonical system kinds, resource types, snippets, and project instances.
- `references/resources/resource-registry.schema.json` — structural schema for the registry.
- `references/skill-standards/logical-resources.standard.yaml` — canonical version-6 normative standard.
- `references/skill-standards/logical-resources.md` — generated human-readable standard.
- `src/officina/resources/__init__.py` — stable public imports for resource evaluation.
- `src/officina/resources/model.py` — immutable registry, pattern, access, witness, influence-result, and dependency-path dataclasses.
- `src/officina/resources/registry.py` — registry loading and semantic validation.
- `src/officina/resources/patterns.py` — parsing, normalization, evaluation, and overlap logic.
- `src/officina/resources/influence.py` — effective-access closure, ordered node-pair influence, overlay derivation, and mixed dependency reachability.
- `references/blueprint/legacy-skill.schema.json` — add the compatible `resource` field to `directIoEntry`.
- `skills/skill-maker/validators/blueprints.py` — call shared resource validation during blueprint checks.
- `validators/resource_registry.py` — repository-level registry and declaration validation.
- `src/officina/common/blueprint_graph.py` — expose normalized authored resource declarations and deterministic direct-edge path traversal without mixing the overlay into `BlueprintEdge`.
- `tests/test_officina_resource_registry.py` — registry schema, uniqueness, and ambiguity tests.
- `tests/test_officina_resource_patterns.py` — grammar and overlap unit tests.
- `tests/test_officina_resource_influence.py` — ordered-pair, effective-access, edge, and evidence tests.
- `docs/skill-blueprints.md` — blueprint authoring guidance and examples.

---

### Task 1: Install the Approved Registry as Canonical Data

**Files:**
- Create: `references/resources/resource-registry.yaml`
- Create: `references/resources/resource-registry.schema.json`
- Test: `tests/test_officina_resource_registry.py`

**Interfaces:**
- Consumes: the approved `docs/plans/logical-resource-registry.yaml` draft.
- Produces: the exact version-1 registry containing `local-filesystem`, `google-drive-root`, `calendar-account`, their five resource types, all current snippets, and instances `local`, `cloud-files`, and `g-calendar`.

- [ ] **Step 1:** Write failing schema tests for the approved registry, unknown kinds, duplicate keys, invalid variable forms, ambiguous resource-type patterns, invalid snippets, secret-bearing fields, absolute host paths, and unsupported versions.
- [ ] **Step 2:** Run `pytest tests/test_officina_resource_registry.py -v`; expect failure because canonical registry files do not exist.
- [ ] **Step 3:** Copy the approved draft content into the canonical registry and add the schema that validates its complete structure.
- [ ] **Step 4:** Run `pytest tests/test_officina_resource_registry.py -v`; expect every registry fixture to pass or fail as specified.
- [ ] **Step 5:** After the task review gate is approved, commit the registry, schema, and test with message `feat: define logical resource registry`.

### Task 2: Implement Registry Models and Semantic Loading

**Files:**
- Create: `src/officina/resources/__init__.py`
- Create: `src/officina/resources/model.py`
- Create: `src/officina/resources/registry.py`
- Test: `tests/test_officina_resource_registry.py`

**Interfaces:**
- Produces: `load_resource_registry(path: Path) -> ResourceRegistry` and `validate_resource_registry(registry: ResourceRegistry) -> tuple[ResourceRegistryError, ...]`.
- Consumes: `references/resources/resource-registry.yaml` and its JSON Schema.

- [ ] **Step 1:** Add failing tests for immutable model construction, duplicate semantic identities, unknown instance kinds, snippet-to-type disagreement, and deterministic registry ordering.
- [ ] **Step 2:** Run the registry suite; expect imports from `officina.resources` to fail.
- [ ] **Step 3:** Implement the immutable dataclasses and one safe loader that performs schema validation followed by semantic cross-reference checks.
- [ ] **Step 4:** Run `pytest tests/test_officina_resource_registry.py -v`; expect all tests to pass.
- [ ] **Step 5:** After the task review gate is approved, commit the package initializer, model, loader, and tests with message `feat: load logical resource registry`.

### Task 3: Implement Pattern Parsing, Evaluation, and Overlap

**Files:**
- Create: `src/officina/resources/patterns.py`
- Test: `tests/test_officina_resource_patterns.py`

**Interfaces:**
- Produces: `parse_resource_pattern(text: str, registry: ResourceRegistry) -> ResourcePattern`, `evaluate_resource_pattern(pattern: ResourcePattern, context: ResourceEvaluationContext) -> EvaluatedResourcePattern`, and `patterns_overlap(left: EvaluatedResourcePattern, right: EvaluatedResourcePattern) -> ResourceOverlap | None`.
- Consumes: immutable models and the validated registry from Task 2.

- [ ] **Step 1:** Write failing tests for concrete authorities, kind-constrained authorities, whole and embedded segment variables, final tail variables, local root macros, percent encoding, traversal denial, every current snippet, and every overlap rule in Section 8.
- [ ] **Step 2:** Run `pytest tests/test_officina_resource_patterns.py -v`; expect import failure for the missing pattern module.
- [ ] **Step 3:** Implement the parser, local macro evaluator, logical-root evaluator, calendar evaluator, canonical renderer, and deterministic intersection witness.
- [ ] **Step 4:** Run `pytest tests/test_officina_resource_registry.py tests/test_officina_resource_patterns.py -v`; expect both suites to pass.
- [ ] **Step 5:** After the task review gate is approved, commit `src/officina/resources/patterns.py` and its test with message `feat: evaluate logical resource patterns`.

### Task 4: Add Resource Patterns to Blueprint I/O and Repository Validation

**Files:**
- Modify: `references/blueprint/legacy-skill.schema.json`
- Modify: `references/blueprint/schema-meta.json`
- Modify: `references/blueprint/template.yaml`
- Modify: `skills/skill-maker/validators/blueprints.py`
- Create: `validators/resource_registry.py`
- Test: `tests/test_typed_blueprint_schemas.py`
- Test: `skills/skill-maker/tests/test_blueprint_tools.py`
- Test: `tests/validate_blueprints.py`

**Interfaces:**
- Produces: optional compatibility-phase `directIoEntry.resource`, validated against the canonical registry whenever present.
- Consumes: `load_resource_registry(...)` and `parse_resource_pattern(...)`.

- [ ] **Step 1:** Write failing tests for valid current resources, unknown instances and kinds, kind/path disagreement, forbidden regex/glob syntax inside `resource`, and legacy entries that still use only `path` and `path_match`.
- [ ] **Step 2:** Run the three focused schema and validator suites; expect the new resource cases to fail.
- [ ] **Step 3:** Add the schema field and metadata, call shared semantic validation from skill-maker, and add the repository validator to the normal runner manifest.
- [ ] **Step 4:** Run `pytest tests/test_typed_blueprint_schemas.py skills/skill-maker/tests/test_blueprint_tools.py tests/validate_blueprints.py -v`; expect all focused tests to pass.
- [ ] **Step 5:** Run `.githooks/pre-commit`; expect repository validation to pass without requiring immediate migration of every existing declaration.
- [ ] **Step 6:** After the task review gate is approved, commit only the listed schema, metadata, template, validator, and test changes with message `feat: validate blueprint resource declarations`.

### Task 5: Implement Resource Influence and Mixed Dependency Reachability

**Files:**
- Create: `src/officina/resources/influence.py`
- Modify: `src/officina/resources/__init__.py`
- Modify: `src/officina/common/blueprint_graph.py`
- Test: `tests/test_officina_resource_influence.py`
- Test: `tests/test_officina_blueprint_graph.py`

**Interfaces:**
- Produces: `ResourceInfluenceEvaluator.evaluate(first, second) -> ResourceInfluenceResult`, `ResourceInfluenceEvaluator.may_influence_through_resources(first, second) -> bool`, `ResourceInfluenceEvaluator.derive_implicit_dependence_edges(nodes) -> tuple[ImplicitDependenceEdge, ...]`, `ResourceInfluenceEvaluator.direct_dependency_path(dependent, dependency) -> DependencyPath | None`, `ResourceInfluenceEvaluator.mixed_dependency_path(dependent, dependency) -> DependencyPath | None`, and `ResourceInfluenceEvaluator.may_influence_transitively(first, second) -> bool`.
- Consumes: normalized blueprint nodes, effective `uses_interfaces` access closure, the canonical registry, and pattern overlap from Task 3.

- [ ] **Step 1:** Write failing tests proving ordered direction: first-write/second-read is true, first-read/second-write is false, and a materialized dependency edge points from the second node to the first.
- [ ] **Step 2:** Add failing tests for immediate and inherited accesses, originating-interface evidence, concrete/concrete, generic/concrete, generic/generic, local macro evaluation, embedded filename variables, deletes, network-entry exclusion, self-edge suppression, deterministic witness ordering, duplicate evidence, and cyclic overlay results.
- [ ] **Step 3:** Add failing reachability tests for a direct-only path, an all-implicit path, a path alternating direct and implicit steps, reversed direction, a disconnected pair, cycle termination, deterministic shortest-path selection, non-reflexive zero-edge behavior, retained step evidence, and propagation of `possible` certainty.
- [ ] **Step 4:** Run `pytest tests/test_officina_resource_influence.py tests/test_officina_blueprint_graph.py -v`; expect imports or assertions to fail before implementation.
- [ ] **Step 5:** Implement effective-access closure, the evidence-returning evaluator, its boolean wrapper, all-pairs overlay derivation, deterministic direct-edge traversal, and mixed traversal over the edge union without modifying existing `BlueprintEdge` semantics.
- [ ] **Step 6:** Run both focused suites; expect all tests to pass, cycles to terminate, and direct certification relationships to remain unchanged.
- [ ] **Step 7:** After the task review gate is approved, commit the influence module, public exports, blueprint-graph normalization, and focused tests with message `feat: evaluate resource-mediated influence`.

### Task 6: Migrate Every Currently Relevant Declaration

**Files:**
- Modify through `skill-maker`: `skills/cloud-files/blueprint.yaml`
- Modify through `skill-maker`: `skills/daily-plan/blueprint.yaml`
- Modify through `skill-maker`: `skills/email-client/blueprint.yaml`
- Modify through `skill-maker`: `skills/g-calendar/blueprint.yaml`
- Modify through `skill-maker`: `skills/list-manager/blueprint.yaml`
- Modify through `skill-maker`: `skills/pdf-to-markdown/blueprint.yaml`
- Modify through `skill-maker`: `skills/regenerate-blueprints/blueprint.yaml`
- Create: `docs/plans/logical-resource-migration-inventory.md`
- Create: `tests/test_logical_resource_migration.py`

**Interfaces:**
- Produces: resource declarations for every current local-filesystem, cloud-files Google Drive, and g-calendar access found by the canonical blueprint extractor.
- Consumes: compatibility-phase blueprint validation and the exact snippets in the canonical registry.

- [ ] **Step 1:** Generate the inventory with `scripts/search_blueprints.py`, preserving interface paths and classifying each entry as fixed, variable, inherited through `uses_interfaces`, or non-resource transport.
- [ ] **Step 2:** Write failing migration tests for every fixed path and path family listed in `docs/plans/logical-resource-registry.yaml`.
- [ ] **Step 3:** Use `skill-maker` to add canonical `resource` patterns and remove any copied remote I/O that is not immediate, including Drive accesses already inherited from `cloud-files`.
- [ ] **Step 4:** Run `pytest tests/test_logical_resource_migration.py skills/skill-maker/tests/test_blueprint_tools.py -v`; expect all migrated declarations and transitive effective accesses to match the inventory.
- [ ] **Step 5:** Generate the repository overlay and manually inspect every edge involving the seven migrated skills against its stored evidence.
- [ ] **Step 6:** After the task review gate is approved, commit only the seven migrated skill contracts, generated artifacts, inventory, and migration test with message `feat: declare current logical resources`.

### Task 7: Publish and Enforce the Normative Standard

**Files:**
- Create through `update-standards`: `references/skill-standards/logical-resources.standard.yaml`
- Generate: `references/skill-standards/logical-resources.md`
- Modify: `docs/skill-blueprints.md`
- Modify: `references/blueprint/README.md`
- Test: `tests/test_standard_v6.py`
- Test: `tests/test_migrated_standards_fidelity.py`
- Test: `tests/validate_standard_documents.py`
- Test: `tests/validate_documentation_validators.py`

**Interfaces:**
- Produces: one canonical author-facing standard covering registry ownership, resource types, instances, variables, evaluation, human display, ordered influence, enforcement limits, and the certification boundary.
- Consumes: verified registry and behavior from Tasks 1 through 6.

- [ ] **Step 1:** Write failing standard and documentation expectations for every normative section of this plan and every initial registry definition.
- [ ] **Step 2:** Create the version-6 YAML standard through `update-standards`, generate its Markdown view, and document blueprint examples plus enforcement limits.
- [ ] **Step 3:** Run the four focused standards and documentation suites; expect all to pass.
- [ ] **Step 4:** Run the complete resource registry, pattern, influence, blueprint, and migration suites; expect all to pass.
- [ ] **Step 5:** Run `.githooks/pre-commit` and `.githooks/skill/check-blueprints`; expect both checks to pass.
- [ ] **Step 6:** After the task review gate is approved, commit the canonical standard, generated Markdown, blueprint documentation, README, and exact updated fixtures/tests with message `docs: standardize logical resource influence`.

## Acceptance Criteria

1. The approved draft and canonical registry enumerate every system kind, resource type, snippet, and project instance required by the seven currently relevant skills.
2. Every concrete resource URI resolves to exactly one registered instance and one inferred resource type.
3. Shared skills can quantify over instances of one kind without claiming active access to every instance.
4. Local fixed paths and caller-supplied paths have explicit evaluation semantics; traversal and encoded separators fail closed.
5. `cloud-files` paths remain logical paths under its configured Drive root, and calendar events remain scoped by account instance plus calendar ID.
6. `ResourceInfluenceEvaluator.may_influence_through_resources(first, second)` returns true exactly when an effective write of the first may overlap an effective read of the second.
7. Every positive result has reproducible evidence from the richer `evaluate(first, second)` result.
8. Dependency-oriented edges point from influenced reader to influencing writer and remain outside the certification graph in version 1.
9. `mixed_dependency_path(dependent, dependency)` returns a deterministic nonempty path exactly when the dependency is reachable through any sequence of direct and `implicit_dependence` edges.
10. `may_influence_transitively(first, second)` is true exactly when `mixed_dependency_path(second, first)` exists, including chains that alternate edge types.
11. Mixed traversal terminates on cyclic overlays, preserves per-step evidence and certainty, and does not mutate the canonical direct graph.
12. Existing blueprints remain valid during the compatibility phase, while migrated declarations are commit-blockingly validated.
13. No registry or generated evidence contains credentials, account addresses, absolute local roots, or other private binding material.
