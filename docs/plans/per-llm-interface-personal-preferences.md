# Per-LLM-Interface Personal Preferences Design and Implementation Plan

Status: design draft as of 2026-07-14.

## Purpose

Give every LLM interface a user-local preference source without requiring an
author to predict whether that interface will ever need personalization.
Preferences are scoped per interface so instructions relevant to one workflow
do not enter the context of another workflow in the same skill.

This design generalizes the model tested by the former personal-preference
pilot in `email-triage`. That pilot used one preference file shared by its
operational LLM interfaces and has since been removed. The general design uses
a one-to-one mapping between LLM interfaces and preference files and moves
preference management into `skill-maker`.

## Goals

- Give every LLM interface exactly one personal-preference file.
- Force-load that file at the end of the corresponding instruction binding.
- Keep preferences for one interface out of every other interface's context.
- Make the mapping mechanical enough for generation and validation.
- Keep all preference files for a skill in one discoverable directory.
- Keep canonical repository copies tracked and empty while allowing local
  customization without ordinary Git status or commits including it.
- Use one generic `skill-maker` LLM interface to review and update preferences.
- Declare `skill-maker`'s existing repository-wide skill-file reads and writes
  explicitly in its default LLM interface's `direct_io`.
- Preserve canonical safety, ownership, side-effect, and interface constraints.
- Make generation, validation, and tests portable across Linux, macOS, and
  Windows.

## Non-goals

- Personal preferences do not apply to machine interfaces.
- Preferences are not a mechanism for changing interface architecture,
  dependencies, filesystem ownership, side-effect authority, or machine
  behavior.
- The Git clean filter is not treated as a privacy, backup, or security
  boundary.
- This design does not introduce a single preference file shared by all LLM
  interfaces in a skill.
- This design does not introduce global assistant preferences. A preference
  that genuinely applies across skills belongs in a separate global mechanism.
- This design does not make personal preference content portable across clones
  or machines. That requires a separate synchronization design.
- The feature does not automatically audit or certify user-authored preference
  content.

## Core Model

A preference belongs conceptually to an LLM interface and is represented
structurally as a behavior source consumed by that interface.

```text
personal-preferences/<interface-name>.md
                    |
                    | behavior source
                    v
       <skill>.llm.<interface-name>
```

`SKILL.md` is the instruction binding for the `default` LLM interface. It is
not a separate preference owner. Named instruction files bind named LLM
interfaces. Machine interfaces remain deterministic and receive no personal
preference source.

Every LLM interface participates. There is no per-interface opt-in flag. This
all-or-nothing rule avoids asking authors or validators to decide in advance
whether future users could benefit from personalization.

## Filesystem Layout

Each skill has one central `personal-preferences/` directory directly under the
skill root. Preference filenames use the local LLM interface name exactly.

```text
skills/email-triage/
|-- SKILL.md
|-- llm_interfaces/
|   |-- triage.md
|   `-- update-personal-preferences.md
`-- personal-preferences/
    |-- default.md
    |-- triage.md
    `-- update-personal-preferences.md
```

The canonical path rule is:

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

The interface name comes from the blueprint interface ID, not from the binding
filename. This remains unambiguous if an instruction binding is moved or named
differently.

Preference filenames must follow the existing LLM-interface identifier rules.
They must not introduce path separators, `.` segments, or `..` segments. Every
preference path must resolve within the owning skill's
`personal-preferences/` directory.

### Why the directory is centralized

An alternative is a `personal-preferences/` directory beside every instruction
binding. That makes every textual include start with `./`, but scatters local
state across the skill and forces preference files to move whenever bindings
move. A central directory provides:

- one discoverable user-local state boundary;
- stable preference paths when instruction bindings move;
- simpler Git index setup and diagnostics;
- simpler ownership, audit, backup, and reset operations;
- fewer directories.

The validator can compute relative paths portably, so a fixed textual `./`
prefix is not valuable enough to outweigh those costs.

## Forced Loading

Every LLM instruction binding ends with an `@` include resolving to its own
preference file. It must be the final nonblank line of the file.

For `SKILL.md`:

```markdown
@./personal-preferences/default.md
```

For `llm_interfaces/triage.md`:

```markdown
@../personal-preferences/triage.md
```

If a binding is nested more deeply, its include contains the required number
of parent components. Authors do not choose or hardcode that relative path.
Generation and validation derive it from:

1. the skill root;
2. the LLM interface name;
3. the interface's declared instruction binding.

The final include has no headings, prose, or instructions after it. An empty
preference file means that only canonical interface instructions apply.

The canonical interface instructions immediately before the include must state
the authority boundary: preferences may refine discretionary behavior but may
not change safety rules, declared interfaces, ownership, side-effect limits,
required confirmations, or other canonical invariants. This is an instruction
policy, not a security boundary against a manually malicious local file.

## Portable Path Derivation

Blueprint and Markdown paths are repository-relative logical paths and always
use `/`, independently of the host operating system. The validator must not use
host-native path serialization to author or compare these strings.

For each LLM interface, define:

```text
binding = <declared instruction binding path>
target  = personal-preferences/<interface-name>.md
include = relative POSIX path from parent(binding) to target
```

The implementation should:

- use `posixpath.relpath()` or an equivalent lexical POSIX-path function for
  the serialized include;
- use `pathlib.Path` only when accessing files on the current host;
- use `.as_posix()` whenever a repository path enters Markdown, YAML, a
  diagnostic, or an expected test value;
- reject absolute bindings, drive-letter paths, backslashes in serialized
  blueprint paths, and traversal outside the skill root;
- compare normalized repository-relative targets rather than Unix absolute
  paths;
- avoid assumptions about case-sensitive filesystems.

Canonical textual form is:

- prefix the relative path with `./` when it does not begin with `../`;
- otherwise use the computed `../` path unchanged;
- do not permit redundant components such as
  `../llm_interfaces/../personal-preferences/triage.md`.

The primary semantic check resolves the final include lexically from the
binding's parent and verifies that it equals the expected preference target.
The canonical-form check then prevents multiple spellings of the same path.

## Blueprint Representation

Every preference file is an existing, tracked behavior-source binding. It has
one behavior-source sidecar in the same directory:

```text
personal-preferences/default.md
personal-preferences/.default.md.blueprint.yaml
```

The proposed behavior-source ID is:

```text
<skill>.source.personal-preferences.<interface-name>
```

Conceptual example:

```yaml
schema_version: 2
blueprint_type: behavior-source
id: email-triage.source.personal-preferences.triage
version: 1
description: Stores user-local preferences for email-triage.llm.triage.
binding:
  kind: file
  path: personal-preferences/triage.md
content: config
format: markdown
uses_behavior_sources: []
```

The corresponding LLM-interface sidecar declares only its matching preference
source:

```yaml
behavior_sources:
  - source: email-triage.source.personal-preferences.triage
    version: 1
    blueprint:
      base: skill-root
      path: personal-preferences/.triage.md.blueprint.yaml
    reason: Apply user-local preferences only to email-triage.llm.triage.
```

The preference read must also appear in the interface's direct IO as a
user-private local configuration read. No interface declares another
interface's preference source.

The default LLM interface follows the same rule. Its binding is `SKILL.md`, its
preference file is `personal-preferences/default.md`, and its interface sidecar
declares the default preference behavior source.

## Preference Scope and Precedence

Personal preferences may refine behavior that the canonical interface leaves
discretionary. Examples include:

- classification emphasis within already permitted categories;
- wording, tone, concision, and report organization;
- user-approved defaults when the interface explicitly permits defaults;
- which optional checks or presentation details to emphasize;
- domain conventions when more than one canonical choice is valid.

Preferences may not:

- disable safety or confirmation requirements;
- authorize undeclared filesystem, network, account, or external side effects;
- bypass dispatcher or interface boundaries;
- alter deterministic validator outcomes or machine-interface semantics;
- replace required evidence with unsupported claims;
- store credentials, tokens, private message bodies, or conversation history;
- change the architecture of the skill.

When a preference conflicts with canonical behavior, the canonical rule wins
and the interface reports the conflict when it materially affects the request.
A structural or architectural request must be handled as a skill change, not
saved as a preference.

## Generic Preference-Management Interface

`skill-maker` gains a named LLM interface:

```text
skill-maker.llm.update-personal-preferences
```

Individual skills do not each need their own preference-management interface.
The generic interface targets one explicit LLM interface at a time:

```text
<skill>.llm.<interface-name>
```

It must:

1. Resolve the target through the live blueprint graph.
2. Reject machine interfaces, unknown interfaces, traversal, symlinks that
   escape the skill, and arbitrary caller-supplied paths.
3. Derive the preference file from the target interface ID rather than accept
   a raw file path.
4. Read the existing preference file before proposing or applying a change.
5. Convert the user's request into concise active behavioral instructions.
6. Preserve unrelated preferences.
7. Refuse content that belongs in canonical architecture or violates the scope
   and precedence rules above.
8. Show the resulting content and obtain confirmation before reset, removal,
   or another destructive rewrite. Explicit additive or corrective requests
   may be applied directly.
9. Write only the derived preference file, using atomic replacement when the
   editing surface supports it.
10. Report the exact interface and preference change. On failure, preserve the
    previous content and never claim success.

If the user asks for personalization while a specific LLM interface is clearly
active, that interface is the default target. If the request could refer to
multiple interfaces, the updater asks for the interface rather than copying the
preference broadly.

Preference management for
`skill-maker.llm.update-personal-preferences` itself follows the same universal
rule: it has its own empty preference source. Its personal preferences may
refine presentation but cannot weaken its target resolution, ownership,
confirmation, or write constraints.

### General `skill-maker` IO

`skill-maker` already creates and edits skill artifacts throughout
`$repo/skills/**` when the user asks it to maintain a skill. Its default LLM
interface must declare those repository-wide skill reads and writes explicitly
in `direct_io`, using a repository-root glob and `content: skill`.

This declaration describes the immediate filesystem activity of the general
skill-authoring workflow. It gives `skill-maker` no ownership of another
skill, its interfaces, its blueprint nodes, or its canonical artifacts.
`owns_filesystem` remains reserved for paths that have one continuing writer
authority during normal skill operation. User-authorized skill maintenance may
modify declared artifacts without transferring ownership of the skill or its
artifacts to the general authoring interface.
The existing blueprint fields identify that distinction: the root is a
`meta-skill` and `generator`, while the broad filesystem entries use
`content: skill`. Validators treat those entries as maintenance of the skill
definitions themselves, not as competing operational ownership claims.

The named preference updater declares its own narrower preference-file reads
and writes in `direct_io`. Those entries use repository-root paths because the
interface writes preference files across multiple skill roots.

### Filesystem ownership

The generic updater is the sole writer for the repository-wide preference
namespace:

```text
$repo/skills/*/personal-preferences/*.md
```

Its blueprint declares that boundary as a repository-root ownership regex and
declares the corresponding repository-root `direct_io` read and write family.
Here `owns_filesystem` means writer authority over the preference state files
only. Neither that declaration nor the location of those files transfers
ownership of the containing skills or any other artifacts to `skill-maker`.
The generated ownership declaration lists the repository's LLM interface IDs
as allowed readers. The preference validator then applies the narrower
one-to-one rule: each reader may declare and read only its own derived
preference path. The combination avoids granting an interface permission to
consume another interface's preferences merely because both paths match the
updater's ownership pattern.

The updater declares the dedicated preference-filter setup interface in
`uses_interfaces`. It does not invoke Git directly or embed Git command syntax
in its LLM instructions. The ownership and IO validators must compare
repository-root regex/glob families semantically rather than only comparing
their literal serialized strings. The updater must not bypass ownership checks
with undeclared direct writes.

## Generation and Migration

When `skill-maker` creates an LLM interface, it creates as one atomic logical
unit:

- the instruction binding;
- the LLM-interface blueprint node or sidecar;
- `personal-preferences/<interface-name>.md` as an empty regular file;
- its behavior-source sidecar;
- the interface-to-source behavior edge;
- the final computed `@` include;
- the direct-IO read declaration.

Removing or renaming an LLM interface performs the corresponding preference
file and sidecar migration. A rename must preserve local preference content and
must warn if both the old and new targets exist. Moving only the instruction
binding changes the computed include but does not move the centralized
preference file.

The initial repository migration applies the rule to every existing LLM
interface. The earlier `email-triage` pilot has already been removed from the
current repository, so this migration must not recreate its shared
`references/personal-preferences.md` source or its per-skill updater. If an
older checkout still contains nonempty pilot preferences, migrating that
checkout requires a separate explicit content-allocation step; the current
repository migration must not guess where shared instructions belong.

## Git Lifecycle

Canonical preference files are tracked and empty. They must exist in the
repository because behavior-source bindings refer to real regular files.
Repository `.gitattributes` assigns one clean-filter driver to every preference
path:

```gitattributes
skills/*/personal-preferences/*.md filter=personal-preference
```

### Fresh-clone invariant

Every ordinary clone receives the same repository structure without running a
setup command:

- every canonical empty preference file;
- every behavior-source sidecar and interface edge;
- every final instruction include;
- the repository `.gitattributes` pattern;
- the filter implementation and structural/index validators.

Repository validation fails if any required artifact is missing, extra,
misnamed, incorrectly connected, or canonically nonempty. This structural
contract is cloneable and enforceable without checkout-local Git
configuration.

Only activation of the clean/smudge driver is checkout-local because Git does
not import repository-controlled commands into `.git/config` during clone. The
repository bootstrap/installer invokes the idempotent filter `--install` mode.
The generic preference updater also invokes `--check` and then `--install` when
needed before its first write. A user therefore does not need to identify or
configure preference paths individually.

The filter's clean operation consumes working-tree content and emits zero
bytes. Therefore Git compares and stages an empty blob while the working-tree
file retains its personal content. The smudge operation is an identity
operation; canonical empty blobs consequently check out as empty files.

The required lifecycle is ordered:

1. Create each preference file as an empty regular file together with its
   behavior-source and interface declarations, plus the tracked
   `.gitattributes` rule.
2. Install and verify the checkout-local filter-driver configuration.
3. Stage the new paths. The clean filter stages each preference file as the
   empty Git blob even if local content was added prematurely.
4. Verify the staged blobs are empty, then commit the canonical preference
   blobs and all declarations before writing personal content.

This permits adding and committing new empty preference files normally. A new
untracked preference file remains visible as untracked until it is added; the
filter controls its staged content, not whether the path exists. Once the path
is tracked with an empty blob, local nonempty content cleans to the same empty
blob and therefore does not appear as an ordinary content modification.

The tracked `.gitattributes` pattern is cloned, but the filter command in
`.git/config` is checkout-local. `skill-maker` exposes a dedicated machine
interface with these modes:

- `--install`: configure the required clean and identity-smudge commands for
  this checkout;
- `--check`: verify the tracked attribute, local driver configuration, and
  empty index blobs without mutation;
- `--remove`: remove only this checkout's filter-driver configuration for an
  explicit maintenance workflow.

The clean and smudge commands are dispatcher-owned machine interfaces. The
clean interface accepts bytes on stdin and emits no stdout bytes. The smudge
interface copies stdin bytes to stdout unchanged. They must not emit status or
diagnostic text on stdout because stdout is file content in Git's filter
protocol.

The local driver is configured as required when installed. A fresh clone still
needs `--install`; repository validation remains the final safeguard and
rejects any nonempty staged preference blob even when the local filter is
missing or misconfigured. Normal validators and blueprint check mode never
write `.git/config` or mutate the index.

The generic preference updater checks the filter setup and the target's empty
index blob before writing. If protection is missing, it invokes the setup
interface or reports the required repair instead of claiming that personal
content is excluded from commits.

## Audit and Drift Behavior

A preference file is a genuine behavior source. Under the current audit model,
changing it changes the local behavior-source hash and makes the prior audit
stale. The clean filter affects Git's comparison and staging view; it must not
hide working-tree preference content from behavior hashing or local drift
checks.

The updater reports the affected interface and the resulting stale local audit
state. It does not certify its own change.

Separating canonical and personal audit hashes could improve reporting later,
but it is not required for this feature. That extension should be designed
separately rather than silently changing existing audit semantics here.

## Validator Contract

The conformance validator discovers LLM interfaces from the validated blueprint
graph. For every LLM interface it enforces:

1. The instruction binding exists and is a regular file within the skill.
2. Exactly one expected preference file exists at
   `personal-preferences/<interface-name>.md`.
3. Its behavior-source sidecar exists, has the expected ID and binding, and
   resolves to that regular file.
4. The LLM interface declares exactly that preference source in
   `behavior_sources`.
5. Its direct IO declares the corresponding user-private local configuration
   read.
6. The instruction binding's final nonblank line is an `@` include.
7. Resolving that include lexically from the binding's parent yields exactly
   the expected preference file.
8. The include uses the computed canonical POSIX spelling.
9. Nothing follows the include except the file's final newline.
10. The preference directory contains no orphan `.md` files or sidecars.

For machine interfaces it enforces that no per-machine preference file or
personal-preference behavior edge is generated.

The blueprint relationship validator also enforces that:

- `skill-maker.llm.default` declares its general `$repo/skills/**` reads and
  writes in `direct_io` without claiming repository-wide filesystem ownership;
- no general `skill-maker` declaration is interpreted as ownership of another
  skill, interface, blueprint node, or canonical artifact;
- broad `content: skill` IO by a `meta-skill`/`generator` is classified as
  user-authorized maintenance and is not treated as a competing operational
  ownership claim;
- `skill-maker.llm.update-personal-preferences` declares the narrower
  repository-root preference read/write family and owns that family;
- path-family comparison accounts for `exact`, `glob`, and `regex` declarations
  rather than treating different serialized pattern forms as unrelated for
  operational IO and ownership.

Structural validation permits a nonempty local preference file. Otherwise a
legitimate personalized checkout would always fail. Canonical emptiness is a
staging/index invariant: commit-time validation runs against the index-backed
repository view and rejects nonempty canonical preference blobs. Tests that run
against the ordinary working tree must not assume preference files are empty.

The structural validator does not install or modify the local filter. The
dedicated setup interface checks local configuration, while index-aware
validation independently rejects nonempty canonical blobs.

## Cross-Platform Testing

All tests must pass on Linux, macOS, and Windows. Path tests operate on logical
repository paths, not host-specific absolute paths.

The path matrix must include:

| Binding | Expected include |
|---|---|
| `SKILL.md` | `@./personal-preferences/default.md` |
| `llm_interfaces/triage.md` | `@../personal-preferences/triage.md` |
| `workflows/reports/summarize.md` | `@../../personal-preferences/summarize.md` |

Focused validator tests must cover:

- missing, extra, and misnamed preference files;
- missing or incorrect behavior-source sidecars;
- wrong interface-to-source edges;
- a preference source shared by two interfaces;
- missing, nonfinal, malformed, and noncanonical includes;
- an include resolving to another interface's preference file;
- nested bindings and bindings moved between directories;
- forward slashes in diagnostics and generated Markdown on every host;
- rejection of backslashes, absolute paths, drive-letter paths, traversal, and
  case-collision hazards;
- local nonempty preferences passing structural validation;
- nonempty staged canonical preference content failing index validation;
- machine interfaces receiving no preference artifacts.

Generic updater tests must cover:

- target resolution by full LLM interface ID;
- rejection of machine, unknown, and path-like targets;
- preservation of unrelated preference instructions;
- confirmation for destructive changes;
- atomic failure preserving prior content;
- refusal of credentials and architectural changes;
- exact reporting of the affected interface;
- behavior when the local Git filter is missing or misconfigured.

Git-filter integration tests should create temporary repositories through
argument-list subprocess calls, not shell scripts. Tests must avoid Unix-only
commands, `/tmp`, shell redirection, executable-bit assumptions, and parsing
localized prose when a stable Git status code or porcelain output is available.
They must prove that adding a new matching file stages an empty blob, editing a
tracked matching file leaves the index blob empty, a nonmatching file remains
unchanged, missing required filter commands fail safely after installation, and
index-aware validation catches nonempty blobs when no filter was installed.

## Failure Handling

- Missing preference artifacts are conformance errors, not an empty-preference
  fallback.
- An unreadable preference file stops the affected LLM interface before its
  substantive workflow and reports the exact repository-relative path.
- A malformed or wrong final include fails validation.
- A preference write failure preserves the prior file and is reported as a
  failure.
- An ambiguous update request asks which LLM interface to target.
- A request outside preference scope is redirected to ordinary skill editing
  rather than stored as personal behavior.
- A missing or broken local Git filter is diagnosed explicitly; no component
  claims that preference content is excluded from commits when it is not.
- Blueprint or dispatcher rejection is accepted and reported. The updater must
  not work around declared ownership or interface boundaries.

## Implementation Work Units

- [ ] Add the shared POSIX preference-path model and platform-neutral tests.
- [ ] Add structural and index-aware preference validation, including semantic
  exact/glob/regex path-family checks.
- [ ] Convert `skill-maker` to a typed graph, declare its general
  `content: skill` direct IO, and add the generic preference updater.
- [ ] Add clean, identity-smudge, and filter setup/check/remove machine
  interfaces with temporary-repository integration tests.
- [ ] Integrate idempotent filter installation into repository bootstrap and
  the generic updater's pre-write path; a fresh clone must require no
  per-preference configuration.
- [ ] Add plan-first typed-blueprint migration and preference materialization
  interfaces; neither may mutate Git configuration or index state.
- [ ] Convert remaining legacy inline blueprints to typed graphs before adding
  preference behavior-source sidecars.
- [ ] Materialize one preference file, sidecar, behavior edge, direct read, and
  final include for every LLM interface.
- [ ] Run focused tests, full repository validation, blueprint sync checks, and
  the cross-platform Python test entrypoint.
- [ ] Install and verify the local filter, then stop for explicit approval to
  commit the verified canonical empty blobs before personalization.

## Rollout Sequence

1. Declare and validate `skill-maker`'s general `content: skill` direct IO and
   the maintenance-versus-operational ownership rule.
2. Add the shared path-mapping helper and its platform-neutral unit tests.
3. Add structural and index-aware preference validators with failing fixtures.
4. Extend `skill-maker` generation and interface-migration behavior.
5. Add `skill-maker.llm.update-personal-preferences` and focused behavior tests.
6. Add the clean/smudge and preference-filter setup machine interfaces with
   temporary-repository integration tests.
7. Integrate idempotent filter installation into repository bootstrap and the
   generic updater's first-write preflight.
8. Convert remaining legacy blueprints to typed graphs, then migrate existing
   skills without recreating the removed shared
   `email-triage` pilot.
9. Refresh generated blueprint artifacts through the dispatcher-owned sync
   interface.
10. Run focused tests, the full validator suite, blueprint sync check, and the
   repository's cross-platform test entrypoint.
11. Install and verify the required filter driver in the current checkout,
    then verify the staged preference blobs are empty.
12. Commit canonical empty files, `.gitattributes`, and related artifacts; each
    other checkout must install the filter before adding personal content.

The implementation must preserve unrelated dirty worktree changes and stage
only files belonging to this feature when the user later authorizes a commit.

## Acceptance Criteria

- Every LLM interface has exactly one matching preference file and behavior
  source.
- Every LLM instruction binding force-loads only its matching preference file
  as its final nonblank line.
- Includes are derived from live bindings and serialize identically on Linux,
  macOS, and Windows.
- Machine interfaces have no personal-preference artifacts.
- Preference management targets an explicit LLM interface through
  `skill-maker` and cannot write arbitrary paths.
- `skill-maker.llm.default` explicitly declares its general repository-wide
  skill reads and writes without owning any other skill or its artifacts.
- Blueprint validation distinguishes those user-authorized skill-maintenance
  writes from operational ownership using the existing root role, kind, and
  `content: skill` declaration.
- Canonical preference files are tracked and empty; local content may be
  nonempty without failing structural validation.
- The `.gitattributes` pattern covers every canonical preference path, and the
  clean filter allows new matching files to be added while staging empty blobs.
- Every fresh clone contains the complete canonical preference structure and
  can verify it without local filter configuration.
- Repository bootstrap and the generic updater install the one checkout-local
  filter driver idempotently; no preference path is configured individually.
- The empty canonical files are committed before personal content is written.
- Local Git protection is applied and diagnosed separately from repository
  validation.
- Personal preferences cannot legitimately change canonical safety,
  ownership, side-effect, dispatcher, or interface constraints.
- The removed `email-triage` shared pilot source and per-skill updater are not
  recreated by the repository migration.
- Focused, repository-wide, and cross-platform checks pass without relying on
  Unix-only paths or commands.
