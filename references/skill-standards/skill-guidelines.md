<!-- Generated from references/skill-standards/skill-guidelines.standard.yaml; do not edit. -->

# Skill Module Standards

Source-faithful repository standards for skill identity, interfaces, runtime boundaries, state, portability, instruction design, workflow, and validation.

## A skill is a software module.

**A skill is a software module.** The standards below define the module
boundary: identity, interfaces, allowed dependencies, runtime ownership, and
import discipline. They are structural requirements, not style preferences.

## Version 4 modules and behavioral sources

This family is the sole live blueprint and interface authoring authority.

Every new or edited blueprint uses `schema_version: 4` and exactly one live `node_type`: `node_type: module` or `node_type: behavioral_source`. A skill is an autodiscoverable module, expressed by `discovery: {mechanism: skill}`, and uses `SKILL.md` as its module gateway; `skill` is not a node type or schema family.

A module is declared by `blueprint.yaml`. Each contained behavioral source is declared under `blueprints/`, for example `blueprints/gateway.yaml`. Do not author hidden blueprint sidecars.

Every node declares one whole-file `gateway` with `path` and `language`, plus optional alternative `machines`. `content` contains case-sensitive regular expressions for the files the node owns; the gateway must be included. The project node-input policy selects certification hash inputs from that ownership; `content` is not a second hash policy. Gateway fragments, symbols, and pre-v4 gateway kinds do not belong in version 4 gateway declarations.

A module declares `authority`, `sources`, and `exports`. `sources` maps each contained source ID to its blueprint locator. `exports` maps a public `<module>.interface.<name>` ID to one `source_interface` and its module-level access policy. The module does not copy the source interface version, contract, or process binding.

A behavioral source declares `dependencies`, `uses_interfaces`, and `interfaces`. `dependencies` names direct behavioral-source dependencies with exact versions, locators, and reasons. `uses_interfaces` names exact versions of sibling private interfaces or module exports. `interfaces` defines source-owned contracts under `<module>.source.<source>.interface.<name>`; each interface owns its version, description, semantic contract, and optional process binding.

Cross-module interface use goes through the callee module's `exports` and is authorized by that export's `access`. A source may use an unexported source interface only inside its own module. Interfaces are source-owned contracts and module exports are their public relation; neither machine nor LLM is an interface node type or ID namespace.

The v4 module/source graph is authored authority. Generated `SKILL.md` contract blocks, interface views, runtime-dependency manifests, certification records, and other projections must be derived from that graph and must not become a parallel declaration source.

## 1. Skill identity and contract come first

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

## Blueprint authoring — REQUIRED: Use the version 4 schemas

**Blueprint authoring — REQUIRED: Use the version 4 schemas**

Generate module roots from `references/blueprint/module.schema.json`
and behavioral sources from
`references/blueprint/behavioral-source.schema.json`. Do not copy
`references/blueprint/template.yaml` into a module; it documents the
artifact layout rather than a complete declaration.

## Canonical interface names

**Canonical interface names**

Every blueprint interface has a canonical fully qualified name:

- module export: `<module>.interface.<name>`

- source-owned interface: `<module>.source.<source>.interface.<name>`

The local `<name>` is the final component of the interface ID.
It must be dash-separated and must **not** contain `.`.

The canonical invocation form for a process-bound module export is:

```bash
dispatcher --caller-skill daily-plan list-manager.interface.read-list /tmp/todo.yaml
```

Every Python behavioral-source gateway that may invoke another
module must define a declared dispatch menu. Runtime code calls
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

## Dispatcher role

**Dispatcher role**

The installed `dispatcher` command and the shared dispatcher runtime are the
only sanctioned local cross-module process boundary. Python behavioral-source
runtime code must reach that boundary through declared
`DispatchCall` entries and `PythonMachineInterface.dispatch()`. The
dispatcher runtime's job is to:

1. Parse the canonical target `<module>.interface.<name>`

2. Load the callee's version 4 module graph and resolve the export

3. Verify the export's module-level access policy

4. Parse caller arguments against the source-owned interface contract

5. Compile the interface's process binding against its source gateway

6. Build an invocation plan without depending on the caller's working directory

7. Return the dry-run plan or execute it

The graph and process-binding approach enables static validation: git hooks verify
that only authorized modules use restricted exports, catching misuse
before deployment rather than only at runtime.

Use `--dry-run` to inspect the resolved invocation without executing it:

```bash
dispatcher --dry-run --caller-skill daily-plan \
  list-manager.interface.read-list /tmp/todo.yaml
```

Every `DispatchCall(...)` declaration must include `caller_skill` set to the
owning skill's exact name; that value must be a string literal or a module-level
string constant that resolves statically.

## Private runtime files

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

Module exports bind public names to source-owned interfaces; process bindings
may route those interfaces to private runtime modules. Tests,
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

## Import discipline

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

Because Python behavioral-source gateways run with the module root on
`PYTHONPATH`, files
under `_rtx/` may use relative imports to share same-skill helpers. Nested
runtime packages are allowed only when their directory names and file stems
follow the cascading private `_rtx/` naming rule above.

## TOML IO boundary

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

## Subprocess text boundaries

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

The shared dispatcher owns this boundary for process-bound exports: text-mode
dispatcher invocations use UTF-8 strict in the parent process, and Python module
runtimes get `PYTHONIOENCODING=utf-8:strict` in the child environment. This is
enforced by `validators/subprocess_text_encoding.py`, with behavior tests in
`tests/test_officina_dispatcher.py` and
`tests/validate_subprocess_text_encoding.py`.

## Injection lifecycle

**Injection lifecycle**

`../../skills/skill-maker/_rtx/_blueprint_syncer.py` injects and refreshes the
generated artifacts for autodiscoverable modules:

- the generated contract block placed immediately after the YAML frontmatter in
  `SKILL.md`

- the generated owner-facing interface sections placed immediately after the
  contract block

- repo-level manifests such as
  `references/blueprint/runtime_dependencies.json`

That generated content is not user-authored. Do not edit it by hand. These
checks are enforced on every commit by `validators/runner.py` (called from
`.githooks/pre-commit`) via the skill-maker validators.

## 2. Skill taxonomy

**2. Skill taxonomy** — autodiscoverable modules declare `category`, `role`,
and `kind` in `blueprint.yaml` using the typed fields in
`references/blueprint/module.schema.json`. These fields provide user-facing
documentation and graph taxonomy.

For `research-assistant` skills applied to `.tex` files: check whether a
top-of-document profile comment exists before proceeding; if not, use
`make-tex-docstring` first.

## 3. `my-X` naming and structure

**3. `my-X` naming and structure** — a personal override of upstream skill `X`
is named `my-X`. Every `my-X` skill must follow this layout:

- Personal overrides and additions at the top.

- Then a **REQUIRED — NON-NEGOTIABLE** instruction to invoke the original `X`
  skill at the bottom.

## 4. `suggested_permissions`

**4. `suggested_permissions`** — permission suggestions live in the module's
`blueprint.yaml`, not in separate permission files. `suggested_permissions` is
advisory, not a grant. It should explain what is safe and useful to pre-approve
for smoother execution. Do not cascade another module's suggested permissions;
declare the exact interface edge in `uses_interfaces` and let permission tooling
derive transitive grants from the interface graph.

## 5. Frontmatter `description:` is a trigger declaration, not a summary

**5. Frontmatter `description:` is a trigger declaration, not a summary** —
write it as "Use when..." followed by the triggering conditions and symptoms
that signal this skill applies. Never summarize the workflow, steps, or outputs
in the description.

## 6. Output-focused, terse writing

**6. Output-focused, terse writing** — specify what to invoke and how to
interpret output. Implementation internals belong in tool/script docs, not
`SKILL.md`. Every line earns its place.

## 7. The canonical blueprint graph owns all interface definitions

**7. The canonical blueprint graph owns all interface definitions** — the module
blueprint owns module facts, source containment, exports, and access; every
behavioral-source blueprint owns its content, dependencies, and interface
contracts. A node never repeats a neighbor's intrinsic information. Generated
interface blocks in `SKILL.md` are derived views of that graph. The skill body
must not restate, re-explain, or re-invoke any interface. Specifically:

- a source interface's `description` describes what the interface does.

- its `usage` provides the complete invocation argument
  template.

- `patterns[*].notes` gives mode-specific detail where multiple calling modes
  exist.

- a source interface owns its contract and optional process binding; a module
  export names that interface and access policy without copying either.

- The skill body references interface names only — it never shows
  `dispatcher --caller-skill` invocations or runtime file paths.

The generated blocks must be sufficient for a first-attempt correct invocation.

**Interfaces that require runtime state should say so explicitly in the skill
body.** When an operation depends on information only available at call time,
the skill body must instruct the model to read that state first.

## 8. Commit and push after every skill change

**8. Commit and push after every skill change** — when a skill is created or
modified and the result is complete, show the user the diff and ask for
confirmation before committing. Once confirmed, stage the changed files,
commit, and push to `origin`.

## 9. Skills are components in an evolving system — design accordingly.

**9. Skills are components in an evolving system — design accordingly.**

- **Reuse, don't reimplement.** Before writing new behavior, check whether an
  existing skill already covers it. If yes, invoke or extend that skill.

- **Depend on interfaces, not internals.** There are only two valid
  cross-module boundaries:

  - invoke the dependency module through its autodiscovered gateway

  - call the dependency module's export through `dispatcher` or a declared
    `DispatchCall` used by `PythonMachineInterface.dispatch()`

- **Do not introduce new cross-module Python imports.** If behavior should be
  shared across modules, expose it through a module gateway, module export, or a
  first-party shared package under `src/officina/`.

- **Do not reach into another skill's runtime directory from local runtime code.**

- **Keep SKILL.md references local.** Paths in `SKILL.md` must be relative. A
  skill may refer to files under its own directory, to shared `../references/`
  material, and to shared repo tools under `../../tools/`. It must not mention
  parent-path addresses such as `../other-skill/...`, `../../skills/...`, or
  any absolute filesystem path to another skill.

- **Make your own interface explicit.** State what inputs your module expects
  and what outputs it produces:

  - the module blueprint declares sources, exports, and export access

  - each behavioral source declares its own interfaces and direct dependencies

  - `uses_interfaces` records exact sibling or exported interface dependencies

  - each interface contract describes its immediate semantic IO

  - `content` declares the files owned by each node

  - dependency and interface versions are exact

## 10. No code in SKILL.md — runtime files only, with one exception

**10. No code in SKILL.md — runtime files only, with one exception** — skill files
must not contain executable code logic. Any logic belongs in a dedicated file
under `_rtx/`, except when the skill's purpose is to provide an interface to
a specific external tool and that tool is declared in frontmatter `tools:`.
Hand-authored `SKILL.md` bodies must also avoid executable-file names and paths
in execution contexts, such as `run tmp.py`, `python helper.py`, `use
install.sh`, or `launch tool.exe`. If normal operation requires execution, put
the mechanics behind a process-bound behavioral-source interface exported by
the module and refer to the export name and outcome in prose. Generated
blueprint interface blocks may contain invocation details because they are
derived from the version 4 graph. Opaque `_cx/...` paths are forbidden anywhere
in the hand-authored body, even outside an explicit execution sentence.

## 11. State data lives under the skill's directory

**11. State data lives under the skill's directory** — any persistent state a
skill writes must be stored under the skill's own directory, not under system
directories or elsewhere outside the skills tree.

## 12. Sensitive configs live under `~/.config/<skill-name>/`

**12. Sensitive configs live under `~/.config/<skill-name>/`** — passwords,
API keys, OAuth tokens, and credentials must go there, never under the skill
directory.

## 13. Prefer widely available, cross-platform tools at every layer

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

## 14. Shared skill content must stay neutral about which specific AI-assistant host it runs under, and must mention operating systems only in explicit platform-support metadata or platform-named implementation files.

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

## Validator and test conventions

## Validator and test conventions

Mechanical repository checks run on every commit via `validators/runner.py` (called from
`.githooks/pre-commit`). The runner auto-discovers two packages:

- **`validators/`** — repo-wide checks

- **`skills/skill-maker/validators/`** — skill-system checks

## Adding a new validator

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

## Test file conventions

### Test file conventions

- Validator tests live in `tests/validate_<name>.py`.

- Behavior tests live in `skills/<skill-name>/tests/`.

- Use `importlib.util.spec_from_file_location` to load validators in tests —
  avoids package naming collisions and works regardless of working directory.
