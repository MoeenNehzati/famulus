# Consumer-Local Injection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` or `superpowers:executing-plans`.

**Goal:** Implement certified, consumer-local canonical YAML selection and one
minimized dispatcher/schema vocabulary per session without migrating live
skills.

**Architecture:** A pure selector operates on the normalized repository graph,
then a deterministic block writer updates only each LLM interface's gateway.
The SessionStart hook derives a deduplicated vocabulary from selected constructs.

**Tech Stack:** Python, YAML, existing skill-maker syncer, hook adapters, pytest.

**Primary requirements:** `INJ-001` through `INJ-009`, `DEP-004`, `HOOK-001`,
`HOOK-002`, the
projection half of `ADM-004`/`ADM-009`, and cross-skill route tests for
`INJ-008`.

## Preconditions and required reading

- Phases 1 and 2 are accepted; the normalized graph, nested export resolver,
  and rejecting `CertificationView` protocol are stable.
  These are prerequisites; they do not authorize Phase 3.
- Read `../IMPLEMENT.md` and the requirement entries above in
  `../01-decision-ledger.md`.
- Read `Consumer-local selection` and `SessionStart vocabulary` in
  `../03-inventory-graph-and-injection.md`.
- Read `Tools and helpers` in `../02-machine-module-contract.md` for helper
  closure semantics.
- Read only the matching rows in `../05-verification-matrix.md`.

## Phase stop conditions

Stop if projection requires provider filesystem paths, sibling/transitive tool
authority, a prose rendering language, or an authorization guarantee the
dispatcher does not enforce. Stop if embedded definitions cannot remain
self-contained within the specified size gates or if host hooks require
different semantic payloads.

## Task 1: Pure consumer-local selector

**Files:**

- Create: `src/officina/common/interface_projection.py`
- Create: `references/blueprint/interface-projection.schema.json`
- Test: `tests/test_interface_projection.py`

**Produces:**

```python
@dataclass(frozen=True)
class InterfaceProjection:
    consumer_id: str
    document: Mapping[str, JsonValue]
    vocabulary: frozenset[str]


def project_consumer_interfaces(
    repository_graph: RepositoryBlueprintGraph,
    consumer_id: str,
    certification: CertificationView,
) -> InterfaceProjection: ...
```

Plan 2 defines the narrow read-only `CertificationView` protocol and its
rejecting production placeholder. Plan 3 imports it and uses fixture
implementations. Plan 4 supplies the certificate-backed adapter; this keeps
projection independent of transitional health.

- [ ] Add fixtures for root/default and named LLM consumers, no dependencies,
  multiple direct grants, one module with multiple exports, bounded helpers,
  shared/local provider tools, cross-skill LLM grants, and orphan exports.
- [ ] For LLM-interface grants, retain canonical ID/version/description. Include
  a relative gateway only for a same-skill target; for a cross-skill target,
  route through the provider skill implied by the canonical ID and never expose
  its instruction-file path.
- [ ] Assert selection includes only direct grants, exact nested exports, the
  complete normalized caller contract/direct I/O, and bounded helper expansions
  according to the normative field table. Validate every projection against
  `interface-projection.schema.json`. Assert it
  excludes gateways, content, sibling exports, private ownership, ordinary
  provider transitive tools, and unrelated schema vocabulary.
- [ ] Assert provider tool metadata cannot become an independent caller-facing
  prompt entry; only the consumer's authored edge or a bounded helper expansion
  can do so.
- [ ] Expand helper edges recursively to a bounded acyclic fixed point without
  traversing ordinary tool dependencies. Reject cycles, size overflow, and an
  enum-value helper whose target is mutating or whose result is not finitely
  schema-bounded.
- [ ] Assert consumer-local selection changes prompt visibility only. Preserve
  skill-wide dispatcher authorization, label helper bindings as guidance, and
  require an independently exported fixed interface in fixtures that need
  runtime enforcement of helper arguments.
- [ ] Assert stale/missing module certification, failed export result, profile
  mismatch, or version mismatch rejects projection with stable diagnostics.
- [ ] Implement schema validation/default normalization before selection and
  canonical key ordering after selection. Resolve retained schema/format
  references to digest-bound top-level definitions and replace locators with
  `definition_ref`; retain only the validation-equivalent reachable closure and
  named caller annotations; reject unresolved, escaping, or non-JSON values.
  Expose a standalone-export byte counter for Plan 4's 12,288-byte certification
  rule and reject only the combined consumer block at 16,384 bytes here.
  Return vocabulary tags from actually selected constructs.
- [ ] Run `pytest tests/test_interface_projection.py -q`.

## Task 2: Deterministic generated-block placement

**Files:**

- Modify: `skills/skill-maker/_rtx/_blueprint_syncer.py`
- Test: `skills/skill-maker/tests/test_blueprint_tools.py`

- [ ] Add tests mapping the inline default consumer to `SKILL.md` and named LLM
  sidecars to their own `llm_interfaces/*.md` gateways.
- [ ] Use exactly `<!-- BEGIN BLUEPRINT USED INTERFACES -->` and
  `<!-- END BLUEPRINT USED INTERFACES -->`. Place the root block immediately
  after its contract block and a named block before its authored body.
- [ ] Replace the root skill-wide union with one block per consumer. Preserve
  the root skill contract block independently of its dependency projection.
- [ ] Plan all target text before writes, reject duplicate/conflicting markers,
  remove stale blocks from zero-dependency consumers, write atomically, and
  prove a second synchronization is byte-identical.
- [ ] Keep hand-authored text outside generated markers unchanged. Reject a
  consumer whose gateway is missing, shared by two named consumers, or outside
  its owner boundary.
- [ ] Exercise writes only against test fixtures. Do not synchronize a live
  skill in this phase.
- [ ] Run `pytest skills/skill-maker/tests/test_blueprint_tools.py -q`.

## Task 3: Minimal session vocabulary

**Files:**

- Modify: `llmhooks/inject_dispatcher_context.py`
- Modify if payload plumbing requires it: `llmhooks/registry.py`
- Modify if the installed compatibility copy requires it:
  `hooks/inject_dispatcher_context.py`
- Test: `hooks/tests/test_inject_dispatcher_context.py`
- Test: `skills/install-assistant-tools/tests/test_claude_install.py`
- Test: `skills/install-assistant-tools/tests/test_codex_install.py`
- Test: `skills/install-assistant-tools/tests/test_claude_github_install.py`
- Test: `skills/install-assistant-tools/tests/test_codex_github_install.py`
- Test: `skills/install-assistant-tools/tests/test_dev_link_hooks.py`
- Test: `skills/install-assistant-tools/tests/test_uninstall.py`

**Produces:** One semantic payload shared by all host adapters.

- [ ] Add tests for a single deduplicated block, no-interface sessions, scalar,
  list, filesystem, enum/helper, stdin, outcomes, and execution vocabulary.
- [ ] Assert `--caller-skill` and `--dry-run` appear exactly once; `--stdin`
  appears only when selected; unverified global options never appear.
- [ ] Assert the glossary distinguishes `[<x>...]` from `<x>...` and explains
  that dry-run prints the compiled invocation without gateway execution or
  stdin reads. Keep `<skill>` generic across multi-skill sessions.
- [ ] Assert the provider-skill route definition appears exactly once only when
  a cross-skill LLM target is selected.
- [ ] Add the exact positional-before-named/fixed-argument rule from the
  normative design. Assert no interface-specific retry/effect/tmp/log facts are
  emitted.
- [ ] Keep the required dispatcher/notation core within 500 characters. Add
  optional vocabulary entries by descending use count then canonical name until
  the 750-character bound; omit overflow entries only when the selected YAML is
  self-describing. A valid projection must not fail solely from optional
  glossary overflow. Keep host wrappers responsible only for lifecycle/output
  envelopes.
- [ ] Run the seven named hook and install-assistant-tools test files.

## Task 4: Migration disposition report

**Files:**

- Create: `src/officina/common/interface_injection_migration.py`
- Test: `tests/test_interface_injection_migration.py`

**Produces:** A read-only report classifying every interface formerly exposed
through a skill-wide union.

- [ ] Compare old generated union membership with authored consumer-local edges.
- [ ] Require exactly one disposition per export: `add-direct-edge`,
  `keep-uninjected`, or `retire`. Reject duplicates and missing exports.
- [ ] Keep report output deterministic and machine-readable; do not mutate
  blueprints in this task.
- [ ] Run `pytest tests/test_interface_injection_migration.py -q`.

## Task 5: Phase gate

- [ ] Run `pytest tests/test_interface_projection.py
  skills/skill-maker/tests/test_blueprint_tools.py
  tests/test_interface_injection_migration.py
  hooks/tests/test_inject_dispatcher_context.py
  skills/install-assistant-tools/tests/test_claude_install.py
  skills/install-assistant-tools/tests/test_codex_install.py
  skills/install-assistant-tools/tests/test_claude_github_install.py
  skills/install-assistant-tools/tests/test_codex_github_install.py
  skills/install-assistant-tools/tests/test_dev_link_hooks.py
  skills/install-assistant-tools/tests/test_uninstall.py -q`.
- [ ] Run `git diff --check` and inspect generated markers in one root and one
  named LLM fixture.
- [ ] Do not migrate live skills; only explicitly authorized Phase 5 may do so.

## Phase completion evidence

Report selector/projection/synchronizer/hook APIs, requirement IDs, exact test
commands and counts, sample generated blocks and byte sizes, migration-report
coverage, and exact worktree scope. Confirm that no live skill was migrated,
then stop for review before Plan 4.
