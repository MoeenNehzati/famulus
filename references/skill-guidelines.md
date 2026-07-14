# Skill Module Standards

**A skill is a software module.** The standards below define the module
boundary: identity, interfaces, allowed dependencies, runtime ownership, and
import discipline. They are structural requirements, not style preferences.

**1. Skill identity and contract come first** — every skill has a stable
dash-separated name and declares its dependency and interface contract before
workflow instructions.

The skill directory name and frontmatter `name:` must match exactly. Skill names
must be lower-case, dash-separated, and at least two words:

```text
list-manager
get-weather
email-client
```

Do not create one-word skill names such as `lists`, `weather`, or `email`.

Historical legacy skills may keep a hand-authored dependency block in
`SKILL.md`:

```markdown
Category: automation

Dependencies:
- list-manager
- g-calendar
- get-weather
```

Use `Dependencies: none` when the skill must not invoke other skills. List only
skill names, not paths, files, or implementation details.

In this repository, local skills use one canonical `blueprint.yaml` graph root
next to `SKILL.md`. Generate its initial values from `skill.schema.json`, then
create typed hidden sidecars from their concrete schemas. The committed
`references/blueprint/template.yaml` is an artifact-layout manifest, not a
copyable root blueprint. The canonical root plus the subordinate files
reachable through its locators are the source of truth for:

- `category`
- `role`
- `kind`
- `suggested_permissions`
- `skill_interface`
- interface locators and interface-local contracts

For blueprint skills, the top-of-file contract block in `SKILL.md` is generated
from the canonical graph. Authored root and subordinate blueprints are
canonical; generated blocks, pooled reviews, health records, and repo-level
manifests are not graph authority and must match the authored graph exactly.

Cross-skill use is declared on the interface that performs it, through
`uses_interfaces`. Do not add top-level skill dependency lists. If an LLM
surface delegates to another skill, point to that skill's LLM interface. If a
machine interface calls another machine interface, declare that exact
version-pinned machine interface edge.
In schema-version-2 LLM-interface and behavior-source bodies, use canonical
`skill.llm.name` or `skill.machine.name` IDs, never bare cross-skill names.
Every interface ID named in the body must be declared by that same node's
`uses_interfaces`.

**Blueprint authoring — REQUIRED: Use the concrete schema**

Generate each new root or subordinate blueprint from its concrete type schema.
Do not copy `references/blueprint/template.yaml` into a skill. The complete
type-specific authoring, creation, hash, and validator traceability rules live
in `references/blueprint/*.schema.json` and `schema-meta.json`; the manifest
only demonstrates deterministic filenames and generated outputs.

**Blueprint authoring notes**

- `category`: required single string from the typed enum in
  `references/blueprint/schema.json`.
- `role`: required single string from the typed enum in
  `references/blueprint/schema.json`; it names the primary user-facing domain
  for generated docs and graph clustering.
- `kind`: required single string from the typed enum in
  `references/blueprint/schema.json`; it names the primary shape of help the
  skill provides for generated docs and filters.
- `suggested_permissions`: advisory mapping with `bash` and `network` lists.
  Every entry must include a `reason`.
- `skill_interface`: three plain-language lists (`inputs`, `outputs`,
  `side_effects`) that describe the skill's high-level contract.
- `interfaces`: version-pinned locators for subordinate LLM and machine
  interface sidecars. Every root points to an `llm.default` sidecar, and that
  sidecar explicitly binds `SKILL.md`.
- `blueprint_type`: one of `skill`, `llm-interface`, `machine-interface`, or
  `behavior-source`. A typed file states its own type instead of wrapping its
  facts in another interface-name mapping.

Every subordinate blueprint binds exactly one existing regular file and is
hidden beside it. A single node bound to `foo.py` uses
`.foo.py.blueprint.yaml`; multiple nodes on the same file use qualified names
such as `.foo.py.read.blueprint.yaml`. Generated health uses the corresponding
`.health.json` suffix. The skill root alone keeps `blueprint.yaml` and
`.last_audit.json`. Directories never receive blueprints.

`skill-audit` generates node health bottom-up and may generate
`.pooled-blueprint-review.yaml` plus its health file for review. Pooled files
are never graph inputs. The schema family defines canonical health fields,
SHA-256 projections, HMAC-SHA-256 authentication, and which authored fields
participate in contract hashes. Health, pool, and key files are ignored local
state. Do not hand-author them.

**Canonical interface names**

Every blueprint interface has a canonical fully qualified name:

- machine interface: `skill.machine.name`
- llm interface: `skill.llm.name`

The local `<name>` is the final component of the subordinate blueprint's `id`.
It must be dash-separated and must **not** contain `.`.

The canonical invocation form for machine interfaces is:

```bash
dispatcher --caller-skill daily-plan list-manager.machine.read-list /tmp/todo.yaml
```

For Python skill code, every machine-interface class that may invoke another
skill must define a declared dispatch menu on the interface. Runtime code calls
only entries from that menu, never raw dispatcher APIs:

```python
from officina.runtime.python_machine_interface import DispatchCall, PythonMachineInterface


class Interface(PythonMachineInterface):
    dispatches = {
        "read-list": DispatchCall(
            caller_skill="daily-plan",
            target_skill="list-manager",
            interface="read-list",
        )
    }
```

Then execute by key:

```python
self.dispatch("read-list", args=["/tmp/todo.yaml"])
```

Do not import or call `officina.dispatcher` from skill runtime code. Do not
invoke the `dispatcher` CLI from Python skill code, and do not modify `sys.path`
to reach dispatcher internals.

This is mechanically checked by
`skills/skill-maker/validators/dispatcher_usage.py`, which rejects raw
dispatcher imports and CLI dispatch from skill runtime code, and
`skills/skill-maker/validators/dispatch_caller_skill.py`, which verifies every
`DispatchCall(caller_skill=...)` statically resolves to the owning skill name.

**Machine interfaces**

A `machine-interface` sidecar is the dispatcher-executable contract. It owns:

- `version` — the major version of this interface contract
- `description` — what the interface does
- `usage` — complete invocation argument template
- `patterns` — positional/flag/stdin constraints
- `allow_all_skills` / `allowed_callers` — access control
- `platform_support` — explicit Linux/macOS/Windows support booleans for this
  machine interface
- `binding` — one private Python entrypoint under `_rtx` or directly executed
  command file under `_cx`
- `dependencies` — factual runtime package and executable requirements
- `behavior_sources` — edges to typed file-backed behavior-source nodes
- `direct_io` — immediate semantic IO for generated docs, search, graphs, and
  safety summaries
- `owns_filesystem` — interface-owned filesystem paths and explicitly allowed
  reader interfaces

`binding` is **internal metadata**. Python interfaces use `kind:
python-entrypoint`, a `_rtx/*.py` path, and a symbol. Command interfaces use
`kind: command-file` and a tracked executable under `_cx`. The dispatcher
executes command files directly as argv; inline shell strings, direct Bash
declarations, and `bash -c` are forbidden. Public interface IDs remain
`skill.machine.name` for both binding kinds.
Binding paths must be relative, contain no parent traversal, and remain inside
the corresponding `_rtx` or `_cx` directory after symlink resolution.
Hand-authored LLM-facing text must never expose `_cx/...`; name the canonical
machine interface instead.

Every executable `machine-interface` sidecar must declare `dependencies`.
Use `dependencies: []` when the interface has no non-stdlib Python package or
external executable requirements. Otherwise list one object per requirement:

```yaml
platform_support:
  linux: true
  macos: true
  windows: false

dependencies:
  - kind: python-package
    name: PyYAML
    version: ">=6"
    platforms:
      linux: true
      macos: true
      windows: false
    reason: "Reads YAML input files."
  - kind: binary
    name: curl
    version: any
    platforms:
      linux: true
      macos: true
      windows: false
    reason: "Fetches remote JSON from the API."
```

`platform_support` is required on every machine interface. It is an explicit
three-boolean object, not a free-form OS list:

- `linux`
- `macos`
- `windows`

Set each boolean deliberately. Do not omit unsupported platforms. Use `macos`,
not `osx`.

Do not declare skill-level platform support. Skill-level summaries for docs,
graphs, installers, and validators must be derived from
reachable machine sidecar `platform_support`. A skill may have both portable and
platform-specific machine interfaces.

Every dependency must declare `kind`, `name`, `version`, `platforms`, and
`reason`. `platforms` uses the same required booleans as `platform_support` and
must not claim a platform that the owning interface does not support. Use
`version: any` only when there is no known lower bound, exact version, or useful
human-readable constraint.

Allowed dependency kinds are closed and non-overlapping:

- `python-package` — installable Python package requirement. `name` is the
  package/runtime requirement name, not necessarily the import name.
- `binary` — executable expected on `PATH`.
- `system-service` — service manager or daemon facility such as `systemd` or
  `launchd`.
- `system-library` — native/shared library requirement outside Python.
- `external-application` — installed GUI or full application such as Chrome or
  Audiveris.
- `runtime` — language/runtime requirement such as Python, Node, Java, or a
  shell runtime.
- `model-data` — local model, checkpoint, cache, or other required data bundle.

Where a dependency subfield has a bounded vocabulary, use the schema's finite
options rather than inventing adjacent names. For `kind: system-service`,
`name` must be one of:

- `systemd-user`
- `launchd`
- `task-scheduler`
- `cron`

Do not encode APIs, OAuth, credentials, or network access as dependency kinds.
Represent those through `direct_io.network`, auth metadata, and setup
interfaces. Runtime dependencies are factual environment requirements. They are
separate from top-level `suggested_permissions`, which remains developer
judgment about a good baseline approval set and must not be inferred from code.

The blueprint sync tool generates `references/blueprint/runtime_dependencies.json`
from these declarations. Installers and other non-YAML consumers should read
that JSON manifest instead of parsing blueprint YAML at runtime.

Every interface must declare `behavior_sources` directly.

Behavior sources are regular files that the interface reads as
instructions, rules, schemas, templates, examples, parser tables, checklists, or
other behavior-shaping material. They are not the user's subject input. A
Markdown interface should list the Markdown files it points to and loads. A
Python machine interface should list non-code files such as JSON schemas,
prompt templates, examples, policy files, and config files that affect behavior.
Python imports, `_rtx/` entrypoint files, and dispatcher targets are discovered
mechanically; do not duplicate them in behavior sources.

Use `behavior_sources: []` only after checking that the interface has no
non-code behavior-shaping files. Each edge points to a typed behavior-source
sidecar, carries the consumer-local `reason`, and pins a version. The target
node owns its binding, content, format, description, and any edges to other
behavior sources. All binding paths are relative to the skill root. Directory
bindings are forbidden; bind a concrete dispatcher, manifest, index, or README
file when a collection needs graph identity.

Every machine and LLM interface must declare `version`. Bump it only when that
interface's exported contract changes in a breaking way. A skill's version is
the version of its `llm.default` interface; there is no separate top-level skill
version field.

Every machine and LLM interface must also declare `direct_io` with `reads`,
`writes`, and `network` lists. `direct_io` is immediate-only: describe only the
IO performed by that interface itself, and never copy IO from interfaces listed
in `uses_interfaces`. Generated documentation and graphs derive transitive IO by
recursively following `uses_interfaces`.

`direct_io.content` must stay coarse and user-meaningful. It names the object
for docs, search, and graphs, not internal fields. Use values such as `email`,
`calendar-event`, `proof`, `report`, or `credential`; do not introduce
field-level values such as `email-subject`, `email-body`, `event-title`, or
`document-id`.

Use `direct_io.format` for one known format and `direct_io.formats` for a
finite family of possible formats. Never set both on the same entry. When a path
describes a family of files, set `path_match: glob` and use the documented
minimal glob syntax: `*` within a segment, `**` as a complete segment, and a
final extension family such as `*.{md,pdf}`. Prefer this over broad values such
as `mixed`. Do not use nonstandard forms such as `*.[md|pdf]`. Use
`path_match: regex` only when a glob cannot express the family; regex paths
must compile and should declare explicit `format` or `formats` values.

Every machine and LLM interface must declare `owns_filesystem`. Use `[]` when
the interface owns no filesystem paths. If an interface owns a path, only that
interface may write matching `direct_io.writes` entries; only that interface and
the canonical interfaces named in `allowed_readers` may read matching
`direct_io.reads` entries. Ownership paths can be exact strings or regexes.
Two different interfaces must not own overlapping filesystem paths.

For `kind: python-entrypoint`, health dependency exploration combines
three surfaces:

- typed `behavior_sources` for non-code behavior-shaping files, including
  schemas, templates, examples, policy files, and parser tables;
- class-level `dispatches = {...}` entries made of `DispatchCall(...)` for
  cross-skill machine-interface dependencies, followed recursively through
  dispatcher resolution;
- `route_smoke()` for same-skill dynamic Python imports that normal execution
  performs lazily.

If a Python file can affect behavior and is not otherwise covered by declared
imports or `DispatchCall`, the interface's `route_smoke()` must import the code
path cheaply and without real side effects. Non-code behavior files must be
declared through typed behavior-source nodes. Health exploration does not execute
normal `run(...)` just to discover dependencies.

Pattern semantics are per interface, not per grouped command. Every
`machine-interface` sidecar is one canonical callable interface. Do not
reintroduce grouped parent interfaces with hidden subinterface ids.

**LLM interfaces**

A `llm-interface` sidecar is not callable through the dispatcher. It documents a
skill-owned prompt surface routed by higher-level skill logic. It owns:

- `version`
- `description`
- `binding` — where the interface definition lives
- `behavior_sources` — additional non-code files that shape prompt behavior
- `direct_io`
- `owns_filesystem`
- `allow_all_skills` / `allowed_callers` — access control for other skills
- optional routing or documentation metadata

LLM bindings use `kind: instruction-file` and one regular local file. The
mandatory `skill.llm.default` sidecar binds `SKILL.md`; another LLM interface
may bind a file such as `llm_interfaces/summarize.md`. URI and directory
bindings are forbidden. All binding paths are relative to the skill root.

Local LLM interface Markdown beyond `SKILL.md` lives under
`skills/<skill-name>/llm_interfaces/`. It is the Markdown counterpart to
`_rtx/`: use `llm_interfaces/` for local LLM-facing instruction surfaces, and
use `references/` for supporting behavior sources such as checklists, examples,
parser notes, templates, or policy material loaded by those interfaces.
For decomposition guidance, see `references/llm-interface-design.md`.

Use `binding`, not `invocation`, because LLM interfaces are descriptive routing
surfaces rather than dispatcher-executed programs. `invocation` is forbidden under
`llm-interface` sidecars.

Every skill root must point to a default sidecar equivalent to:

```yaml
schema_version: 2
blueprint_type: llm-interface
id: example-skill.llm.default
version: 1
description: Primary LLM-facing skill instructions.
binding:
  kind: instruction-file
  path: SKILL.md
```

**Dispatcher role**

The installed `dispatcher` command and the shared dispatcher runtime are the
only sanctioned local cross-skill machine boundaries for blueprint-migrated
skills. Python skill runtime code must reach that boundary through declared
`DispatchCall` entries and `PythonMachineInterface.dispatch()`. The
dispatcher runtime's job is to:

1. Parse the target canonical name `skill.machine.name`
2. Resolve the callee `blueprint.yaml`
3. Follow the root edge to the target `machine-interface` sidecar
4. Verify `allow_all_skills` / `allowed_callers`
5. Match caller argv against declared `patterns`
6. Resolve the private file `binding`
7. Execute the interface without depending on the caller's working directory

The pattern-based approach enables compile-time validation: git hooks verify
that only allowed skills can export restricted interfaces, catching misuse
before deployment rather than only at runtime.

Use `--dry-run` to inspect the resolved invocation without executing it:

```bash
dispatcher --dry-run --caller-skill daily-plan \
  list-manager.machine.read-list /tmp/todo.yaml
```

Every `DispatchCall(...)` declaration must include `caller_skill` set to the
owning skill's exact name; that value must be a string literal or a module-level
string constant that resolves statically.

**Private runtime files**

Skill implementation files live under the private runtime-execution package
`skills/<skill-name>/_rtx/`. `_rtx` is an implementation namespace, not public
documentation vocabulary. Public skill docs must describe interfaces, not
runtime files.

Every non-exempt file or package directory under `_rtx/` must use a private
multi-part stem. The rule cascades through nested packages: every directory
component below `_rtx/` and every runtime filename stem must match the same
private naming convention.

```text
_rtx/_Calendar_Gateway.py
_rtx/_mail_transport.py
_rtx/_install_launcher/_windows_launcher.py
```

Every private directory name and every runtime filename stem must match:

```regex
^_[A-Za-z0-9]+(?:_[A-Za-z0-9]+)+$
```

That means each private runtime path component starts with `_` and has at
least two underscore-separated segments after it. Case is allowed, but
case-only path collisions are forbidden among siblings. `__init__.py` is the
only exempt package marker; package directories themselves are not exempt. The
allowed runtime file suffix list currently contains `.py`; add to that list
deliberately only if this policy is relaxed later.

Skill-facing Markdown (`SKILL.md` and skill-local Markdown outside tests and
assets) must not mention:

- `_rtx`
- runtime filenames ending in an allowed runtime suffix such as `.py`
- normalized forms of private runtime stems, such as `_Calendar_Gateway`,
  `Calendar_Gateway`, `calendar gateway`, or `calendar-gateway`

Blueprints bind public interface names to private runtime modules. Tests,
validators, and migration/design docs may mention runtime file details when
they are defining or checking the convention.

This is mechanically checked by `validators/skill_runtime_files.py` and
`validators/skill_runtime_doc_references.py`, plus
`skills/skill-maker/validators/skill_body_execution.py` for executable-file
references used in execution contexts in hand-authored `SKILL.md` bodies, with
behavior tests in
`tests/validate_skill_runtime_files.py` and
`tests/validate_skill_runtime_doc_references.py`, and
`tests/validate_skill_body_execution.py`.

**Import discipline**

Skill Python files may import only:

- relative modules from their own skill-local `_rtx/` package
- first-party shared packages under `src/officina/`
- stdlib and approved third-party packages

They must not import:

- another skill's Python modules directly
- repo-maintainer packages outside `src/officina/`
- another skill's runtime directory through `sys.path`, path loading, or
  dynamic module tricks

This is the intended model:

- local reuse inside one skill: relative imports
- generic shared infrastructure: `officina.*`
- cross-skill behavior: declared `DispatchCall` entries plus
  `PythonMachineInterface.dispatch()`

The dispatch discipline is mechanically checked by
`skills/skill-maker/validators/dispatcher_usage.py` and
`skills/skill-maker/validators/dispatch_caller_skill.py`.

Because machine interfaces run with the skill root on `PYTHONPATH`, modules
under `_rtx/` may use relative imports to share same-skill helpers. Nested
runtime packages are allowed only when their directory names and file stems
follow the cascading private `_rtx/` naming rule above.

**TOML IO boundary**

Production Python code must not construct, read, write, or parse TOML files
directly. TOML filenames are a controlled boundary because host-specific paths
and TOML string escaping interact badly when callers hand-roll config text.

Use the shared TOML IO boundary:

```python
from officina.common import toml_io

with toml_io.open(base_dir, "settings.toml", "r") as f:
    settings_text = f.read()

with toml_io.open(base_dir, f"{name}.settings.toml", "w") as f:
    f.write(settings_text)
```

Outside `src/officina/common/toml_io.py`, a `.toml` filename may appear only as
the direct filename argument to `toml_io.open(...)`. Do not build TOML filenames
through variables, concatenation, `Path(...)`, `/` path joins, `open(...)`,
`.read_text(...)`, `.write_text(...)`, `tomllib`, or ad hoc regex/string
rewrites. If a caller needs a reusable TOML filename or discovery rule, add a
named helper under `src/officina/common/` and keep filename construction there.
When the helper is specific to a host or platform, give the helper file a
matching platform name and include `toml` in the filename, such as
`codex_toml.py`, so both the TOML boundary and platform-neutral boundary stay
explicit.

`toml_io.open(...)` owns UTF-8 text mode, filename validation, and parse
validation after writes. This rule is enforced by
`validators/toml_io_boundary.py`, with behavior tests in
`tests/validate_toml_io_boundary.py`.

**Subprocess text boundaries**

Production Python code that asks `subprocess` for text must set both
`encoding` and `errors` explicitly on that call. Binary subprocess use is fine
when callers intentionally handle bytes themselves.

Use UTF-8 strict for project-owned/user-facing text:

```python
subprocess.run(
    cmd,
    capture_output=True,
    text=True,
    encoding="utf-8",
    errors="strict",
    check=False,
)
```

For byte streams whose contract is not ordinary text, keep binary mode and
decode explicitly at the boundary with the correct error policy. For git
path-list output, use UTF-8 with `surrogateescape` so unusual filenames remain
round-trippable.

The shared dispatcher owns this boundary for machine-interface calls: text-mode
dispatcher invocations use UTF-8 strict in the parent process, and Python module
runtimes get `PYTHONIOENCODING=utf-8:strict` in the child environment. This is
enforced by `validators/subprocess_text_encoding.py`, with behavior tests in
`tests/test_officina_dispatcher.py` and
`tests/validate_subprocess_text_encoding.py`.

**Injection lifecycle**

`../../skills/skill-maker/_rtx/_blueprint_syncer.py` injects and refreshes the
generated artifacts for blueprint skills:

- the generated contract block placed immediately after the YAML frontmatter in
  `SKILL.md`
- the generated owner-facing interface sections placed immediately after the
  contract block
- repo-level manifests such as
  `references/blueprint/runtime_dependencies.json`

That generated content is not user-authored. Do not edit it by hand. These
checks are enforced on every commit by `validators/runner.py` (called from
`.githooks/pre-commit`) via the skill-maker validators.

**2. Skill taxonomy** — declare `category`, `role`, and `kind` in
`blueprint.yaml`. Each must be one of the typed enum values in
`references/blueprint/schema.json`. `category` remains compatibility metadata
during the migration; `role` and `kind` are the user-facing documentation and
graph taxonomy.

For `research-assistant` skills applied to `.tex` files: check whether a
top-of-document profile comment exists before proceeding; if not, use
`make-tex-docstring` first.

**3. `my-X` naming and structure** — a personal override of upstream skill `X`
is named `my-X`. Every `my-X` skill must follow this layout:

- Personal overrides and additions at the top.
- Then a **REQUIRED — NON-NEGOTIABLE** instruction to invoke the original `X`
  skill at the bottom.

**4. `suggested_permissions`** — permission suggestions live in
`blueprint.yaml`, not in per-skill sidecar files. `suggested_permissions` is
advisory, not a grant. It should explain what is safe and useful to pre-approve
for smoother execution. Do not cascade another skill's suggested permissions
here; declare the actual interface edge in `uses_interfaces` and let permission
tooling derive transitive grants from the interface graph.

**5. Frontmatter `description:` is a trigger declaration, not a summary** —
write it as "Use when..." followed by the triggering conditions and symptoms
that signal this skill applies. Never summarize the workflow, steps, or outputs
in the description.

**6. Output-focused, terse writing** — specify what to invoke and how to
interpret output. Implementation internals belong in tool/script docs, not
`SKILL.md`. Every line earns its place.

**7. The canonical blueprint graph owns all interface definitions** — the root
owns skill-level facts and points to neighbors; every subordinate blueprint
owns its own facts and points to its direct neighbors. A node never repeats a
neighbor's intrinsic information. The generated interface blocks in `SKILL.md`
are derived views of that graph. The skill body must not restate, re-explain,
or re-invoke any interface. Specifically:

- a machine sidecar's `description` describes what the machine interface
  does.
- its `usage` provides the complete invocation argument
  template.
- `patterns[*].notes` gives mode-specific detail where multiple calling modes
  exist.
- an LLM sidecar documents its bound instruction file and
  description, but never a dispatcher invocation.
- The skill body references interface names only — it never shows
  `dispatcher --caller-skill` invocations or runtime file paths.

The generated blocks must be sufficient for a first-attempt correct invocation.

**Interfaces that require runtime state should say so explicitly in the skill
body.** When an operation depends on information only available at call time,
the skill body must instruct the model to read that state first.

**8. Commit and push after every skill change** — when a skill is created or
modified and the result is complete, show the user the diff and ask for
confirmation before committing. Once confirmed, stage the changed files,
commit, and push to `origin`.

**9. Skills are components in an evolving system — design accordingly.**

- **Reuse, don't reimplement.** Before writing new behavior, check whether an
  existing skill already covers it. If yes, invoke or extend that skill.
- **Depend on interfaces, not internals.** There are only two valid
  cross-skill boundaries:
  - invoke the dependency skill as a skill
  - call the dependency skill's exported machine interface through `dispatcher`
    or a declared `DispatchCall` used by `PythonMachineInterface.dispatch()`
- **Do not introduce new cross-skill Python imports.** If behavior should be
  shared across skills, expose it through a skill invocation, exported machine
  interface, or a first-party shared package under `src/officina/`.
- **Do not reach into another skill's runtime directory from local runtime code.**
- **Keep SKILL.md references local.** Paths in `SKILL.md` must be relative. A
  skill may refer to files under its own directory, to shared `../references/`
  material, and to shared repo tools under `../../tools/`. It must not mention
  parent-path addresses such as `../other-skill/...`, `../../skills/...`, or
  any absolute filesystem path to another skill.
- **Make your own interface explicit.** State what inputs your skill expects
  and what outputs it produces. For blueprint skills:
  - `skill_interface` describes the skill-level contract
  - `machine-interface` sidecars describe dispatcher-callable interfaces
  - `llm-interface` sidecars describe LLM-facing interfaces
  - `direct_io` describes immediate semantic IO for each interface
  - `owns_filesystem` declares interface-owned filesystem paths and permitted
    readers
  - `version` on each interface is the major version of that public contract

**10. No code in SKILL.md — runtime files only, with one exception** — skill files
must not contain executable code logic. Any logic belongs in a dedicated file
under `_rtx/`, except when the skill's purpose is to provide an interface to
a specific external tool and that tool is declared in frontmatter `tools:`.
Hand-authored `SKILL.md` bodies must also avoid executable-file names and paths
in execution contexts, such as `run tmp.py`, `python helper.py`, `use
install.sh`, or `launch tool.exe`. If normal operation requires execution, put
the mechanics behind a blueprint machine interface and refer to the interface
name and outcome in prose. Generated blueprint interface blocks may contain
invocation details because they are owned by `blueprint.yaml`.
Opaque `_cx/...` paths are forbidden anywhere in the hand-authored body, even
outside an explicit execution sentence.

**11. State data lives under the skill's directory** — any persistent state a
skill writes must be stored under the skill's own directory, not under system
directories or elsewhere outside the skills tree.

**12. Sensitive configs live under `~/.config/<skill-name>/`** — passwords,
API keys, OAuth tokens, and credentials must go there, never under the skill
directory.

**13. Prefer widely available, cross-platform tools at every layer** —
language, runtime, and any external tools invoked. Skills must work out of the
box across Linux, macOS, and Windows on machines other than your own.

Shared code under `src/officina/common/` must be host-neutral by default. Put
repo-wide policy there only when more than one skill can plausibly need the
same boundary, and keep the surface thin: validation, naming, error
normalization, and test seams are appropriate; product-specific behavior is
not. Prefer a mature cross-platform adapter over per-host in-house
implementations when the adapter delegates to the host facility. For example,
`officina.common.secret_store` owns the repo contract for small local secrets
and delegates storage to Python `keyring`; skills should call that wrapper
instead of importing `keyring` directly or shelling out to host-specific
credential commands.

Date and time formatting at IO boundaries must avoid host-specific formatting
extensions. In Python, do not use GNU/POSIX-only or Windows-only `strftime`
padding modifiers such as `%-m`, `%-d`, `%#m`, or `%#d`. Put shared date/time
storage and display formats in the first-party helpers under
`officina.common.dates` instead of retyping ad hoc formatting logic in each
skill. This is mechanically checked by `validators/portable_dates.py`.

**14. Shared skill content must stay neutral about which specific AI-assistant
host it runs under, and must mention operating systems only in explicit
platform-support metadata or platform-named implementation files.** Enforced by
`validators/platform_neutral.py`.

- `SKILL.md` and any generically named runtime file must not name a specific
  host or operating system.
- authored blueprint files may name operating systems only in structured
  `platform_support` and dependency `platforms` metadata. Do not put
  platform-specific prose or implementation guidance in generic blueprint
  fields.
- Blueprint schema documentation and blueprint validation/sync tooling may name
  the allowed platform keys because they define and enforce that metadata.
- If a skill or shared package genuinely needs platform-specific logic, put
  that logic in a file whose own filename names the platform, such as
  `claude`, `codex`, `windows`, `osx`, or `linux`.
- A small cross-platform adapter may temporarily dispatch to platform-specific
  commands while a backend split is pending, but new platform-specific command
  bodies should live in platform-named files.
- `__init__.py` remains the conventional aggregation seam for
  platform-specific modules. It may import platform-named files and re-export
  a host-neutral API for the rest of the codebase.

---

## Validator and test conventions

Conformance checks run on every commit via `validators/runner.py` (called from
`.githooks/pre-commit`). The runner auto-discovers two packages:

- **`validators/`** — repo-wide checks
- **`skills/skill-maker/validators/`** — skill-system checks

### Adding a new validator

1. Create `validators/<name>.py` or
   `skills/skill-maker/validators/<name>.py`.
2. Export exactly one function: `validate(repo_root: Path) -> list[str]`.
3. Optionally add a `main()` so it can be run standalone.
4. Add a `tests/validate_<name>.py` with at least a pass case and a fail case.
   Use `pytest` conventions (plain functions, `tmp_path` fixture for
   isolation). Load the validator via `importlib.util.spec_from_file_location`
   rather than importing it as a package.

The runner picks up the new file automatically — no registration needed.

### Test file conventions

- Validator tests live in `tests/validate_<name>.py`.
- Behavior tests live in `skills/<skill-name>/tests/`.
- Use `importlib.util.spec_from_file_location` to load validators in tests —
  avoids package naming collisions and works regardless of working directory.
