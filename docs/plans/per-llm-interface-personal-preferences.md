# Per-LLM-Interface Personal Preferences Implementation Plan

> **For Codex:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task by task.

**Goal:** Establish a strict repository convention in which every LLM interface has exactly one personal-preference file, force-loads that file as the final instruction in its binding, can have its user-specific non-substantive preferences updated through `skill-maker`, and keeps the tracked canonical preference blob empty through a repository-defined Git filter.

**Architecture:** The shared skill guideline defines the normative convention. `skill-maker` applies it while creating and maintaining skills, exposes a named LLM interface for preference-only updates, and exposes machine interfaces that implement and configure the Git clean/smudge filter. A tracked `.gitattributes` rule applies the filter pattern in every clone, while checkout-local Git configuration activates the repository-owned filter commands. Skill-system validators derive the expected preference artifacts from the canonical blueprint graph, reject structural mismatches, and require empty staged preference blobs. Existing skills are not migrated by this implementation; consequently, repository-wide validation is intentionally expected to fail on legacy interfaces until a later migration.

**Tech Stack:** Markdown skill instructions, typed blueprint YAML, Python 3, `pathlib`, `posixpath`, `subprocess`, Git attributes and clean/smudge filters, PyYAML, the existing `officina.common.blueprint_graph` loader, pytest, and the repository validator runner.

---

## 1. Scope and decisions

### In scope

- Define one personal-preference file for every LLM interface.
- Put all preference files for a skill under
  `skills/<skill>/personal-preferences/`.
- Name each file after the local LLM interface name:
  `personal-preferences/<interface-name>.md`.
- Represent each preference file as a typed behavior source consumed by exactly
  its corresponding LLM interface.
- Require the interface instruction binding to end by force-loading its own
  preference file through a canonical relative `@` include.
- Teach `skill-maker` to create the complete preference scaffolding whenever
  it creates a skill or an LLM interface.
- Teach `skill-maker` to preserve the invariant when it renames, moves, or
  removes an LLM interface.
- Add `skill-maker.llm.update-personal-preferences` for user-specific changes
  that do not alter the substance of a skill.
- Route non-substantive skill-update requests from
  `skill-maker.llm.default` to the named preference updater.
- Add strict repository-wide validation of correspondence, graph edges, file
  placement, and force-loading.
- Add focused tests for the path model, validator, updater contract, and
  scaffolding instructions.
- Track canonical preference files as empty blobs while allowing nonempty
  checkout-local working-tree content.
- Ship one `.gitattributes` pattern and repository-owned clean/smudge filter
  implementations to every clone.
- Give `skill-maker` a machine interface that installs, checks, and removes the
  checkout-local Git filter configuration.
- Make the updater verify filter protection before writing personal content.
- Validate that staged/index preference blobs are empty independently of the
  working-tree contents.

### Explicitly out of scope

- Migrating existing skills or making the full repository conform now.
- Suppressing, baselining, grandfathering, or otherwise exempting legacy
  validation failures.
- Changing audit hashes, audit certification, or drift semantics.
- Converting unrelated legacy blueprints or regenerating unrelated skills.
- Giving `skill-maker` ownership of another skill or its artifacts.
- Giving preferences authority to alter safety rules, interface architecture,
  dependencies, deterministic machine behavior, IO authority, filesystem
  ownership, side-effect limits, or confirmation requirements.
- Adding preferences to machine interfaces.
- Treating the Git filter as encryption, privacy protection, backup, or
  synchronization across clones.
- Automatically changing Git configuration merely by running an ordinary
  validator or blueprint check.
- Configuring preference paths individually; one pattern and one filter driver
  cover the namespace.

### Accepted temporary state

The new validator is deliberately strict from its first version. It will
report every legacy LLM interface that lacks the new artifacts. Because the
validator runner is part of the pre-commit hook, the full hook will remain red
until the later migration. During this implementation:

- focused tests for the new feature must pass;
- validator diagnostics against the live repository must be reviewed and
  confirmed to be only expected legacy-conformance failures;
- the implementation must not weaken the validator merely to make the current
  repository green;
- completing the future migration is a separate project.

The structural validator must allow nonempty working-tree preference content.
Canonical emptiness is enforced against staged/index blobs, not by requiring a
personalized checkout's files to remain empty.

## 2. Canonical artifact model

For every canonical LLM interface ID:

```text
<skill>.llm.<interface-name>
```

derive exactly one preference target:

```text
skills/<skill>/personal-preferences/<interface-name>.md
```

Examples:

```text
email-triage.llm.default
-> skills/email-triage/personal-preferences/default.md

email-triage.llm.triage
-> skills/email-triage/personal-preferences/triage.md
```

The mapping comes from the canonical interface ID, never from a caller-supplied
path and never from the instruction binding's filename.

Each preference file has a colocated hidden behavior-source sidecar:

```text
personal-preferences/default.md
personal-preferences/.default.md.blueprint.yaml
```

Use this behavior-source identity:

```text
<skill>.source.personal-preferences.<interface-name>
```

The behavior-source sidecar binds the Markdown file and describes it as local
user configuration. The matching LLM interface declares exactly one edge to
that source. The interface's immediate `direct_io.reads` also declares the
preference read as user-private Markdown configuration. This is an IO
declaration, not filesystem ownership.

The canonical Git blob for every preference file is empty. The working-tree
file may contain local personal instructions. Empty working-tree content means
the canonical interface instructions apply without user-specific refinements.

## 3. Forced-load path contract

Every LLM instruction binding must end with an `@` include that resolves to its
own preference file. The include is the final line before the file's final
newline; no headings, prose, blank lines, or other instructions follow it.

Examples:

```markdown
# skills/example-skill/SKILL.md
@./personal-preferences/default.md
```

```markdown
# skills/example-skill/llm_interfaces/review.md
@../personal-preferences/review.md
```

Derive the serialized include lexically:

```text
binding = repository-relative instruction binding path within the skill
target  = personal-preferences/<interface-name>.md
include = POSIX relative path from parent(binding) to target
```

Canonicalization rules:

- use `/` separators on every host;
- use `posixpath.relpath`, or an equivalent lexical POSIX operation, for the
  serialized path;
- prefix `./` when the result does not begin with `../`;
- reject absolute paths, drive-letter paths, backslashes, redundant
  components, and traversal outside the skill root;
- resolve the include from the binding's parent and verify that it equals the
  derived target;
- require exact canonical spelling rather than merely accepting any path that
  resolves to the same file.

The canonical instructions immediately before the include must state the
authority boundary: preferences can refine only behavior left discretionary by
the canonical interface. Canonical rules win on conflict.

## 4. Instruction ownership and placement

### Shared skill guideline

Modify `references/skill-standards/skill-guidelines.md` under the LLM-interface
standards. Keep this section normative and implementation-independent. It must
state:

- the one-to-one mapping between LLM interfaces and preference files;
- the canonical directory, filename, and behavior-source identity;
- the required interface-to-source edge and immediate read declaration;
- the computed final relative include requirement;
- the rule that machine interfaces do not receive preference files;
- the preference authority boundary;
- the requirement that skill authors preserve the invariant across create,
  rename, move, and removal operations;
- the fact that the validator enforces the convention repository-wide.

Do not put `skill-maker` routing details or updater workflow steps in the shared
guideline. Those are behavior of the authoring skill, not universal artifact
semantics.

### `skill-maker.llm.default`

Modify the hand-authored portion of `skills/skill-maker/SKILL.md` to add two
concise responsibilities:

1. **Scaffolding:** whenever creating a skill or LLM interface, create the
   complete preference artifact set and final include in the same logical
   change.
2. **Routing:** distinguish canonical skill maintenance from personal
   preference maintenance and route the latter to
   `skill-maker.llm.update-personal-preferences`.

The default interface remains responsible for substantive skill work. It must
not silently store a requested canonical change as a preference.

### `skill-maker.llm.update-personal-preferences`

Create:

- `skills/skill-maker/llm_interfaces/update-personal-preferences.md`;
- its typed LLM-interface sidecar beside the instruction file;
- `skills/skill-maker/personal-preferences/update-personal-preferences.md`;
- the matching behavior-source sidecar;
- the matching behavior edge, direct read, and final relative include;
- `skills/skill-maker/personal-preferences/default.md` and its behavior-source
  sidecar for `skill-maker.llm.default`;
- the default interface edge, direct read, and final include.

Update the canonical `skill-maker` graph using the applicable concrete schemas,
then refresh generated `SKILL.md` contract/interface blocks only through:

```bash
dispatcher --caller-skill skill-maker skill-maker.machine.sync-blueprints
```

Do not hand-edit generated blocks.

The updater interface declares its repository-wide preference-file reads and
writes in `direct_io`, but keeps `owns_filesystem: []`. This describes the
user-authorized maintenance operation without claiming ownership of any skill
or preference namespace.

Add three machine interfaces to the canonical `skill-maker` graph:

- `skill-maker.machine.personal-preference-clean` consumes bytes on stdin and
  emits an empty byte stream;
- `skill-maker.machine.personal-preference-smudge` copies stdin bytes to stdout
  unchanged;
- `skill-maker.machine.personal-preference-filter-config` checks, installs, or
  removes only this repository's checkout-local filter configuration.

The updater declares and invokes the filter-config interface before its first
write. The clean and smudge interfaces are Git plumbing surfaces and must never
emit diagnostic text on stdout.

## 5. Substantive versus preference-only routing

`skill-maker.llm.default` must route a request to the updater only when the
requested change is user-specific and leaves the canonical contract intact.

Preference-only examples:

- preferred tone, concision, report organization, or response format;
- emphasis among already permitted checks or categories;
- ordering among optional workflow steps;
- a user-specific default where the canonical interface already permits a
  choice;
- a domain convention where multiple canonical choices remain valid.

Substantive skill changes include:

- trigger or routing semantics;
- safety, confirmation, evidence, or refusal rules;
- interface creation, deletion, versioning, or architecture;
- dependencies, cross-skill calls, bindings, or machine behavior;
- allowed IO, network access, filesystem ownership, or side effects;
- deterministic validator, parser, schema, or machine-interface behavior;
- changes intended to apply to every user of the canonical skill.

For a preference-only request:

1. Identify the active canonical LLM interface when unambiguous.
2. Invoke `skill-maker.llm.update-personal-preferences` with that interface ID.
3. If multiple interfaces plausibly match, ask the user to identify the target;
   never duplicate the preference across interfaces by default.

If classification is uncertain or the request combines both kinds of change,
state the boundary and handle the canonical and preference portions separately.

## 6. Preference updater contract

The updater operates on one canonical LLM interface ID at a time. It must:

1. Accept or infer a canonical `<skill>.llm.<interface-name>` target.
2. Resolve the target through the live canonical blueprint graph.
3. Reject unknown interfaces, machine interfaces, raw paths, traversal, and
   bindings that escape the owning skill.
4. Derive the preference path from the interface ID.
5. Verify that the file, behavior source, edge, direct read, and final include
   conform before editing.
6. Read the current preference content.
7. Translate the user's request into concise active behavioral instructions.
8. Preserve unrelated preferences and avoid duplicating equivalent rules.
9. Refuse credentials, private conversation data, or changes outside the
   preference authority boundary.
10. Apply explicit additive or corrective changes directly.
11. Show the proposed result and obtain confirmation before removing,
    resetting, or destructively rewriting preferences.
12. Write only the derived preference file.
13. Before writing, verify the checkout-local filter configuration, the
    `.gitattributes` match, and the target's empty canonical index blob; install
    the local driver through the declared machine interface when needed.
14. Refuse to write personal content when canonical Git protection cannot be
    verified or installed.
15. Report the exact interface and the resulting preference change.
16. On failure, preserve the previous content and do not claim success.

The updater must redirect substantive requests to ordinary `skill-maker`
maintenance. It must never edit `SKILL.md`, LLM interface bindings, blueprints,
machine files, schemas, or validators as part of a preference-only operation.

## 7. Skill-maker scaffolding lifecycle

Whenever `skill-maker` creates a new skill, create the default interface and
the following artifacts as one logical unit:

- `SKILL.md` with its final default-preference include;
- the default LLM-interface declaration in the canonical graph;
- `personal-preferences/default.md`;
- `personal-preferences/.default.md.blueprint.yaml`;
- the default interface's matching behavior-source edge;
- the default interface's matching immediate read declaration.

Whenever `skill-maker` creates a named LLM interface, create:

- its instruction binding;
- its typed LLM-interface sidecar and root locator;
- `personal-preferences/<interface-name>.md`;
- `personal-preferences/.<interface-name>.md.blueprint.yaml`;
- the matching behavior-source edge and immediate read;
- the computed final include in the instruction binding.

Lifecycle rules:

- moving only an instruction binding recomputes the include but does not move
  the centralized preference file;
- renaming an interface renames its preference file and sidecar, changes the
  source ID and edges, recomputes the include, and preserves existing preference
  content;
- removing an interface removes its matching preference artifacts after
  checking whether the file contains user content and obtaining confirmation
  before destructive deletion;
- creating one new interface in a legacy skill scaffolds only that new
  interface; migration of the skill's pre-existing interfaces is not part of
  the operation;
- after any lifecycle operation, run the focused validator and blueprint sync
  check and report unrelated legacy failures separately.

For a newly scaffolded preference file, create and stage the empty file before
writing any local personal content. If content was added prematurely, require
the filter to be installed and verify that staging produces the empty blob
before proceeding.

## 8. Validator implementation

### Files

- Create `skills/skill-maker/validators/personal_preferences.py`.
- Create `tests/validate_personal_preferences.py`.

The validator exports exactly:

```python
def validate(repo_root: Path) -> list[str]:
    ...
```

It is auto-discovered by `validators/runner.py`; do not add manual registration.

### Discovery

Use the existing canonical graph loader from
`officina.common.blueprint_graph`. Do not parse generated `SKILL.md` contract
blocks as graph authority. For each graph:

1. collect every `llm-interface` node, including the inline default interface;
2. derive the local name from its canonical ID;
3. obtain its validated instruction-file binding;
4. derive its expected preference file, sidecar, source ID, and final include;
5. validate the complete invariant;
6. separately enumerate the skill's `personal-preferences/` directory to find
   orphan Markdown files and sidecars.

### Required checks

For every LLM interface, enforce:

1. The instruction binding exists as a regular file inside the skill root.
2. Exactly one expected Markdown preference file exists at
   `personal-preferences/<interface-name>.md`.
3. The matching hidden behavior-source sidecar exists.
4. The sidecar has the expected canonical source ID.
5. Its binding resolves to the expected regular preference file.
6. Its content/format declaration identifies Markdown local configuration.
7. The LLM interface has exactly one behavior-source edge to its own preference
   source.
8. It has no edge to another interface's preference source.
9. Its `direct_io.reads` contains exactly the required immediate preference
   read declaration.
10. The binding ends with exactly the expected canonical relative include.
11. Nothing follows the include except the final newline.
12. The include resolves lexically to the expected target.

For each skill directory, enforce:

- no orphan preference Markdown files;
- no orphan preference behavior-source sidecars;
- no duplicate source IDs or duplicate preference bindings;
- no two LLM interfaces consuming the same preference source;
- no preference file or preference source corresponding only to a machine
  interface;
- no case-only collisions among preference filenames.

Diagnostics must identify the canonical interface and repository-relative
artifact path, serialize paths with `/`, and be deterministic across platforms.
Accumulate independent errors rather than stopping after the first malformed
interface when safe to do so.

### No legacy exemption

Do not add:

- an opt-in marker;
- a baseline manifest;
- a directory-presence exemption;
- a date/version cutoff;
- a list of grandfathered skills.

Absence of the new structure is a validation error for every LLM interface.

## 9. Git filter and canonical-empty lifecycle

### Repository-shipped pieces

Create a tracked root `.gitattributes` entry:

```gitattributes
skills/*/personal-preferences/*.md filter=personal-preference
```

Every clone receives:

- the attribute pattern;
- all preference files that have been committed as empty blobs;
- the filter implementation and machine-interface blueprints;
- the structural and index validators;
- the setup/check/remove interface.

Git deliberately does not import executable filter commands from tracked
repository configuration into `.git/config`. Therefore every clone receives
the structure automatically but must activate the local driver once. The
filter-config interface performs that activation idempotently. This distinction
must be stated in the guideline and diagnostics; do not claim that cloning
alone activates executable Git configuration.

### Filter behavior

Implement the three machine interfaces in a private `skill-maker` runtime file
and bind them through typed sidecars.

The clean interface:

- reads arbitrary bytes from stdin;
- writes zero bytes to stdout;
- returns success when the input stream is consumed;
- writes diagnostics only to stderr;
- performs no filesystem or Git mutation.

The smudge interface:

- copies stdin bytes to stdout unchanged;
- returns success;
- writes diagnostics only to stderr;
- performs no filesystem or Git mutation.

Canonical blobs are empty, so checkout normally supplies an empty stream to the
smudge interface. Identity smudging avoids inventing content during checkout.
The filter is not a backup mechanism: reset, deletion, checkout replacement, or
other destructive working-tree operations can still lose local preferences.

### Checkout-local configuration

`skill-maker.machine.personal-preference-filter-config` supports:

```text
--check
--install
--remove
```

`--install` sets only the local repository keys for the named driver:

```text
filter.personal-preference.clean
filter.personal-preference.smudge
filter.personal-preference.required
```

The command values invoke the exported dispatcher machine interfaces with
`--caller-skill skill-maker`. Set `required` to true after both commands are
configured. Installation must be idempotent and must not rewrite unrelated Git
configuration.

`--check` verifies, without mutation:

- the repository root can be resolved;
- `.gitattributes` gives a representative preference path the expected filter;
- the local clean, smudge, and required values exactly match the supported
  configuration;
- tracked preference paths have stage-zero empty blobs;
- no unmerged preference index entries exist.

`--remove` deletes only the three driver keys above and requires an explicit
maintenance request. It does not edit `.gitattributes`, preference files, or
the index.

Use argument-list subprocess calls with explicit UTF-8/error handling for Git
text output. Do not use shell strings, shell redirection, or platform-specific
commands.

### Staging semantics

The lifecycle for a new preference artifact is:

1. Create the Markdown file as an empty regular file with its sidecar and graph
   declarations.
2. Ensure the tracked `.gitattributes` rule covers the path.
3. Install and verify the local filter driver.
4. Add the preference path. Git runs the clean interface and stages an empty
   blob.
5. Verify the stage-zero index blob is empty.
6. Commit the empty canonical preference file and structural declarations.
7. Only after the empty blob is canonical may the updater add local content.

A new untracked preference file remains visible as untracked until it is added.
After it is tracked with an empty blob, nonempty working-tree content cleans to
the same empty blob and should not appear as an ordinary staged content change.
The updater and filter-config interface must explain failures rather than
claiming content is protected when the driver is missing.

### Index-aware validation

Extend `skills/skill-maker/validators/personal_preferences.py`, or add a second
single-purpose skill-system validator if separation is clearer, to inspect Git
index entries when the repository has a usable Git index. Enforce:

- every tracked preference file has exactly one stage-zero entry;
- its indexed blob is zero bytes;
- no preference file has unmerged stages;
- `.gitattributes` contains the canonical pattern;
- the attribute resolves to `filter: personal-preference` for representative
  and discovered paths.

Do not require local filter configuration in the ordinary structural validator:
that would make an otherwise intact fresh clone invalid before setup. The
filter-config `--check` mode diagnoses local activation. Pre-commit index
validation remains the final safeguard against committing personal content
when setup is absent or broken.

## 10. Test-driven implementation sequence

### Task 1: Add failing validator path-model tests

**Files:**

- Create: `tests/validate_personal_preferences.py`

Add isolated `tmp_path` fixtures for:

- default binding: `SKILL.md` -> `@./personal-preferences/default.md`;
- named binding: `llm_interfaces/review.md` ->
  `@../personal-preferences/review.md`;
- nested binding: `workflows/reports/summarize.md` ->
  `@../../personal-preferences/summarize.md`;
- canonical `/` serialization independent of host path separators;
- rejection of absolute, drive-letter, backslash, redundant, and escaping
  paths.

Run the new test file and verify it fails because the validator does not exist.

### Task 2: Implement the minimal path and graph validator

**Files:**

- Create: `skills/skill-maker/validators/personal_preferences.py`
- Modify: `tests/validate_personal_preferences.py`

Implement only enough to load fixture graphs, derive targets/includes, and
validate the happy path. Run the focused tests until the initial cases pass.

### Task 3: Add correspondence and orphan failure cases

**Files:**

- Modify: `tests/validate_personal_preferences.py`
- Modify: `skills/skill-maker/validators/personal_preferences.py`

Add failing tests, one behavior at a time, for:

- missing or misnamed preference file;
- missing, malformed, or wrongly bound behavior-source sidecar;
- missing, duplicate, shared, or incorrect source edge;
- missing or incorrect immediate read declaration;
- orphan Markdown files and sidecars;
- a preference artifact attached to a machine interface;
- case-only collisions;
- multiple independent errors reported deterministically.

Implement the minimum code for each failure class, rerunning the focused suite
after each group.

### Task 4: Add forced-load failure cases

**Files:**

- Modify: `tests/validate_personal_preferences.py`
- Modify: `skills/skill-maker/validators/personal_preferences.py`

Add tests for:

- missing include;
- include not at the end;
- trailing blank or content after the include;
- wrong interface's preference target;
- a path that resolves correctly but is not canonically spelled;
- nested bindings;
- final newline handling.

Implement exact suffix and lexical-resolution checks. Ensure diagnostics show
the expected canonical include.

### Task 5: Add the normative shared guideline

**Files:**

- Modify: `references/skill-standards/skill-guidelines.md`

Add the convention described in Sections 2-4. Keep it concise enough to be
usable during ordinary skill authoring, while linking each mechanical claim to
the new validator. Do not add migration, Git-filter, audit, or ownership rules.

Review the resulting section against the validator tests so prose and
enforcement use identical paths, IDs, and final-include semantics.

### Task 6: Add `skill-maker` preference interfaces and scaffolding rules

**Files:**

- Modify: `skills/skill-maker/SKILL.md` hand-authored body
- Modify: the canonical `skill-maker` blueprint graph through its concrete
  schemas
- Create: `skills/skill-maker/llm_interfaces/update-personal-preferences.md`
- Create: its hidden LLM-interface sidecar
- Create: `skills/skill-maker/personal-preferences/default.md`
- Create: `skills/skill-maker/personal-preferences/update-personal-preferences.md`
- Create: both hidden behavior-source sidecars

Author the updater workflow from Section 6. Add the default routing and
scaffolding instructions from Sections 5 and 7. Declare reads/writes without
filesystem ownership. Force-load both `skill-maker` preference files.

Refresh generated contract/interface blocks through the dispatcher-owned sync
interface. Run sync in check mode and inspect the `skill-maker`-only diff.

### Task 7: Add focused skill-maker contract tests

**Files:**

- Create: `skills/skill-maker/tests/test_personal_preference_contract.py`

Test stable structural requirements rather than fragile prose fragments:

- the updater interface resolves through the canonical graph;
- its binding and own preference source are one-to-one;
- the updater declares preference reads and writes but no filesystem ownership;
- the default interface declares its own matching preference source;
- both bindings end with their derived includes;
- the generic validator accepts a fully conforming temporary skill;
- a substantive-change fixture is not represented as preference content in
  any generated/scaffolded artifact.

Where LLM routing judgment cannot be executed deterministically, document the
manual review case instead of pretending a string-presence assertion proves
correct routing.

### Task 8: Add Git-filter interfaces and integration tests

**Files:**

- Create: a private runtime implementation under `skills/skill-maker/_rtx/`
- Create: typed machine-interface sidecars for clean, smudge, and filter-config
- Modify: the canonical `skills/skill-maker` graph
- Create: `skills/skill-maker/tests/test_personal_preference_filter.py`
- Create or modify: root `.gitattributes`
- Modify: `tests/validate_personal_preferences.py`
- Modify: `skills/skill-maker/validators/personal_preferences.py`

Write temporary-repository integration tests before implementing each behavior.
Cover:

- clean consumes nonempty bytes and emits exactly zero bytes;
- smudge returns input bytes exactly;
- neither filter writes diagnostics to stdout;
- install creates the exact three local config values and is idempotent;
- check is read-only and distinguishes missing attributes, missing config,
  wrong commands, nonempty index blobs, and unmerged stages;
- remove deletes only the named driver keys;
- adding a new matching nonempty working-tree file stages an empty blob;
- editing a tracked matching file leaves the index blob empty;
- a nonmatching Markdown file is not filtered;
- structural validation permits nonempty working-tree preference content;
- index validation rejects a nonempty staged canonical blob when the filter was
  never installed;
- paths containing spaces are handled through argv, not shell quoting;
- all tests avoid `/tmp` assumptions, Unix-only executables, and shell syntax.

Implement the machine interfaces, blueprint declarations, attribute rule, and
index checks incrementally until these tests pass.

### Task 9: Connect updater and scaffolding to filter setup

**Files:**

- Modify: `skills/skill-maker/SKILL.md`
- Modify: `skills/skill-maker/llm_interfaces/update-personal-preferences.md`
- Modify: the canonical `skill-maker` blueprint graph
- Modify: `skills/skill-maker/tests/test_personal_preference_contract.py`

Declare the filter-config machine interface in the updater's
`uses_interfaces`. Require `--check` before a write and `--install` when setup
is missing. Require the empty-index-blob check before local personalization.
Keep scaffolding order explicit: empty artifact, filter setup, staging and blob
verification, then local content.

### Task 10: Verify the narrow implementation and characterize expected debt

Run:

```bash
pytest -q tests/validate_personal_preferences.py
pytest -q skills/skill-maker/tests/test_personal_preference_contract.py
pytest -q skills/skill-maker/tests/test_personal_preference_filter.py
dispatcher --caller-skill skill-maker skill-maker.machine.sync-blueprints --check
dispatcher --caller-skill skill-maker skill-maker.machine.personal-preference-filter-config --check
python3 validators/runner.py
```

Expected results:

- all three focused test files pass;
- the filter integration tests pass;
- filter setup check confirms the attribute, local commands, and empty indexed
  blobs after installation;
- blueprint sync check passes for authored/generated artifacts involved in the
  feature;
- the full validator runner fails only for legacy LLM interfaces missing
  preference artifacts, plus any pre-existing unrelated failures;
- the new validator accepts `skill-maker` and all conforming test fixtures;
- no existing skill is migrated as part of this implementation.

Record the legacy failures by canonical interface ID for the future migration
plan, but do not add that inventory as an exemption to the validator.

## 11. Implementation safeguards

- Begin by checking that the repository is on a named branch.
- Preserve all unrelated dirty-worktree changes.
- Use the concrete typed blueprint schemas; do not copy the template manifest.
- Do not hand-edit generated blueprint contract or interface blocks.
- Use `apply_patch` for authored file edits.
- Do not run skill implementation scripts directly; use the exported
  dispatcher interface.
- Do not run `skill-audit` merely because preference behavior sources change.
- Do not refresh every skill blueprint or generated documentation.
- Never place secrets, credentials, private messages, or irreplaceable data in
  a preference file; the filter is not a security or backup boundary.
- Never trust the working-tree file's emptiness as proof of canonical Git
  emptiness; inspect the index blob.
- Never mutate `.git/config` from validator or blueprint check mode.
- Inspect the file list after each task and stop if unrelated skills begin to
  change.
- Before any implementation commit, show the scoped diff and obtain the user's
  confirmation as required by the skill guideline.

## 12. Acceptance criteria

- The shared guideline clearly defines the per-LLM-interface preference
  convention and authority boundary.
- `skill-maker` creates complete preference scaffolding for every new skill and
  every new LLM interface.
- `skill-maker` preserves the correspondence across interface moves, renames,
  and removals.
- `skill-maker.llm.default` routes preference-only skill adjustments to
  `skill-maker.llm.update-personal-preferences`.
- The updater targets one canonical LLM interface, derives its file, preserves
  unrelated preferences, and refuses substantive changes.
- The updater declares repository-wide preference reads/writes without
  claiming filesystem ownership.
- Every conforming LLM interface has exactly one matching preference file,
  behavior-source sidecar, edge, direct read, and final include.
- Machine interfaces have no preference artifacts.
- The validator is strict for all skills and contains no legacy exception.
- Canonical preference files are tracked as empty blobs while local working-tree
  contents may be nonempty.
- One tracked `.gitattributes` pattern covers the complete preference namespace
  and ships to every clone.
- The clean and smudge interfaces are byte-safe and keep stdout reserved for
  filter content.
- The filter-config interface installs, checks, and removes only the named
  checkout-local driver configuration.
- A fresh clone contains the canonical structure and can validate structural
  correspondence before local filter activation.
- The updater verifies or installs filter protection before writing personal
  content.
- Index-aware validation rejects nonempty canonical preference blobs even when
  local filter setup is absent or broken.
- Focused tests pass on Linux, macOS, and Windows using logical POSIX repository
  paths.
- No existing skill is migrated by this implementation.
- Audit redesign and repository migration remain explicitly separate future
  work.
