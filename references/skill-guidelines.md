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

In this repository, local skills use a canonical `blueprint.yaml` next to
`SKILL.md`. Initialize a new blueprint by copying
`references/blueprint/template.yaml` into the skill directory as your base,
then customize it in place. The blueprint is hand-authored and comment-rich. It
is the source of truth for:

- `category`
- `interface_version`
- `depends_on`
- `suggested_permissions`
- `skill_interface`
- `interfaces`

For blueprint skills, `depends_on_skills` and the top-of-file contract block in
`SKILL.md` are generated compatibility artifacts. The blueprint is canonical;
the generated files must match it exactly.

Every exact skill-name mention in the body of `SKILL.md` must also match the
dependency set. Do not mention a skill as an invoked collaborator unless it is
in both the `Dependencies:` block and `depends_on_skills`.

Dependencies authorize skill invocation and, for blueprint-migrated
dependencies, interface calls through the installed `dispatcher` command (CLI)
or `officina.dispatcher.dispatch()` (Python). A dependency never authorizes
direct access to another skill's files or raw script paths.

**Blueprint authoring — REQUIRED: Initialize by copying the template**

**Always initialize a new blueprint by copying
`references/blueprint/template.yaml` to `skills/<skill-name>/blueprint.yaml`.**
Do not create a blueprint from scratch; start with the template as your base.
Keep the comments. The template's extensive comments are part of the
specification.

**Blueprint authoring notes**

- `category`: required single string from the typed enum in
  `references/blueprint/schema.json`.
- `interface_version`: required positive integer. Bump only when the exported
  contract changes in a breaking way.
- `depends_on`: mapping from skill name to dependency contract. Use
  `major_version` for blueprint dependencies and `{}` only for legacy
  non-blueprint dependencies. Include `exports` to name which interfaces this
  skill is allowed to use. Each export is the canonical fully qualified
  interface name: `dependency.machine.name` or `dependency.llm.name`.
- `suggested_permissions`: advisory mapping with `bash` and `network` lists.
  Every entry must include a `reason`.
- `skill_interface`: three plain-language lists (`inputs`, `outputs`,
  `side_effects`) that describe the skill's high-level contract.
- `interfaces`: mapping with two namespaces:
  - `interfaces.machine.<name>` defines a dispatcher-callable machine
    interface.
  - `interfaces.llm.<name>` defines an LLM-facing interface documented through
    a separate file, never executed through the dispatcher.

**Canonical interface names**

Every blueprint interface has a canonical fully qualified name:

- machine interface: `skill.machine.name`
- llm interface: `skill.llm.name`

The local `<name>` is the mapping key under `interfaces.machine` or
`interfaces.llm`. It must be dash-separated and must **not** contain `.`.

The canonical invocation form for machine interfaces is:

```bash
dispatcher --caller-skill daily-plan list-manager.machine.read-list /tmp/todo.yaml
```

For Python skill code, the canonical form is:

```python
from officina.dispatcher import dispatch
```

and:

```python
dispatch(
    caller_skill="daily-plan",
    target="list-manager.machine.read-list",
    args=["/tmp/todo.yaml"],
)
```

Do not invoke the `dispatcher` CLI from Python skill code, and do not modify
`sys.path` to reach `officina.dispatcher`.

**Machine interfaces**

`interfaces.machine.<name>` is the dispatcher-executable contract. It owns:

- `description` — what the interface does
- `usage` — complete invocation argument template
- `patterns` — positional/flag/stdin constraints
- `allow_all_skills` / `allowed_callers` — access control
- `runtime` — internal execution metadata
- `dependencies` — factual runtime package and executable requirements

`runtime` is **internal metadata**. It belongs in the blueprint because the
dispatcher must know how to execute the interface, but it must not leak into
user-facing generated docs. Typical runtime forms:

- `kind: python_module` with `module: _rtx._handoff_scan`
- `kind: command` with explicit argv for non-Python tools

Every executable `interfaces.machine.<name>` entry must declare `dependencies`.
Use `dependencies: []` when the interface has no non-stdlib Python package or
external executable requirements. Otherwise list one object per requirement:

```yaml
dependencies:
  - kind: python
    name: PyYAML
    reason: "Reads YAML input files."
  - kind: binary
    name: curl
    reason: "Fetches remote JSON from the API."
```

`kind: python` names an installable Python package. `kind: binary` names an
executable expected on `PATH`. `reason` is required and should explain why the
interface needs that dependency; it is used for docs and review. Runtime
dependencies are factual environment requirements. They are separate from
top-level `suggested_permissions`, which remains developer judgment about a
good baseline approval set and must not be inferred from code.

The blueprint sync tool generates `references/blueprint/runtime_dependencies.json`
from these declarations. Installers and other non-YAML consumers should read
that JSON manifest instead of parsing blueprint YAML at runtime.

Pattern semantics are per interface, not per grouped command. Every
`interfaces.machine.<name>` entry is one canonical callable interface. Do not
reintroduce grouped parent interfaces with hidden subinterface ids.

**LLM interfaces**

`interfaces.llm.<name>` is not callable through the dispatcher. It documents a
skill-owned prompt surface routed by higher-level skill logic. It owns:

- `description`
- `binding` — where the interface definition lives
- `allow_all_skills` / `allowed_callers` — access control for other skills
- optional routing or documentation metadata

Typical LLM bindings:

- `kind: markdown_file` with `path: interfaces/summarize.md`
- `kind: uri` with `uri: https://example.com/skills/summarize.md`

Use `binding`, not `runtime`, because LLM interfaces are descriptive routing
surfaces rather than dispatcher-executed programs. `runtime` is forbidden under
`interfaces.llm.*`.

For migration, `file: interfaces/name.md` may be accepted as a shorthand for:

```yaml
binding:
  kind: markdown_file
  path: interfaces/name.md
```

**Dispatcher role**

The installed `dispatcher` command and the shared `officina.dispatcher` Python
package are the only sanctioned local cross-skill machine boundaries for
blueprint-migrated skills. Their job is to:

1. Parse the target canonical name `skill.machine.name`
2. Resolve the callee `blueprint.yaml`
3. Load `interfaces.machine.<name>`
4. Verify the caller declared the dependency with matching major version
5. Verify the interface appears in `depends_on.exports` when required
6. Verify `allow_all_skills` / `allowed_callers`
7. Match the invocation against declared patterns
8. Resolve the internal `runtime`
9. Execute the runtime without depending on the caller's working directory

The pattern-based approach enables compile-time validation: git hooks verify
that only allowed skills can export restricted interfaces, catching misuse
before deployment rather than only at runtime.

Use `--dry-run` to inspect the resolved invocation without executing it:

```bash
dispatcher --dry-run --caller-skill daily-plan \
  list-manager.machine.read-list /tmp/todo.yaml
```

Every Python `dispatch(...)` call must include `caller_skill` set to the owning
skill's exact name; that value must be a string literal or a module-level
string constant that resolves statically.

**Private runtime files**

Skill implementation files live under the private runtime-execution package
`skills/<skill-name>/_rtx/`. `_rtx` is an implementation namespace, not public
documentation vocabulary. Public skill docs must describe interfaces, not
runtime files.

Every non-exempt file directly under `_rtx/` must use an allowed runtime suffix
and a private multi-part stem:

```text
_rtx/_Calendar_Gateway.py
_rtx/_mail_transport.sh
```

The stem must match:

```regex
^_[A-Za-z0-9]+(?:_[A-Za-z0-9]+)+$
```

That means the filename starts with `_` and has at least two underscore-separated
segments after it. Case is allowed, but case-only filename collisions are
forbidden. `__init__.py` is the only exempt package marker. The allowed runtime
suffix list currently contains `.py` and `.sh`; add to that list deliberately
when a new runtime file type is needed.

Skill-facing Markdown (`SKILL.md` and skill-local Markdown outside tests and
assets) must not mention:

- `_rtx`
- runtime filenames ending in an allowed runtime suffix such as `.py` or `.sh`
- normalized forms of private runtime stems, such as `_Calendar_Gateway`,
  `Calendar_Gateway`, `calendar gateway`, or `calendar-gateway`

Blueprints bind public interface names to private runtime modules. Tests,
validators, and migration/design docs may mention runtime file details when
they are defining or checking the convention.

This is mechanically checked by `validators/skill_runtime_files.py` and
`validators/skill_runtime_doc_references.py`, with behavior tests in
`tests/validate_skill_runtime_files.py` and
`tests/validate_skill_runtime_doc_references.py`.

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
- cross-skill behavior: `dispatch(...)`

Because machine interfaces run with the skill root on `PYTHONPATH`, direct
modules under `_rtx/` may use relative imports to share same-skill helpers.
Runtime files must remain direct children of `_rtx/`; add nested-package support
to the validator before introducing package directories there.

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
named helper to `toml_io` and keep filename construction there.

`toml_io.open(...)` owns UTF-8 text mode, filename validation, and parse
validation after writes. This rule is enforced by
`validators/toml_io_boundary.py`, with behavior tests in
`tests/validate_toml_io_boundary.py`.

**Injection lifecycle**

`../../skills/skill-maker/_rtx/_blueprint_syncer.py` injects and
refreshes the generated compatibility artifacts for blueprint skills:

- `depends_on_skills`
- `permissions.json`
- the generated contract block placed immediately after the YAML frontmatter in
  `SKILL.md`
- the generated owner-facing interface sections placed immediately after the
  contract block

That generated content is not user-authored. Do not edit it by hand. These
checks are enforced on every commit by `validators/runner.py` (called from
`.githooks/pre-commit`) via the skill-maker validators.

**2. Skill categories** — declare `category` in `blueprint.yaml`. Must be one
of the typed enum values in `references/blueprint/schema.json`.

For `research-assistant` skills applied to `.tex` files: check whether a
top-of-document profile comment exists before proceeding; if not, use
`make-tex-docstring` first.

**3. `my-X` naming and structure** — a personal override of upstream skill `X`
is named `my-X`. Every `my-X` skill must follow this layout:

- Personal overrides and additions at the top.
- Then a **REQUIRED — NON-NEGOTIABLE** instruction to invoke the original `X`
  skill at the bottom.

**4. `permissions.json` and `suggested_permissions`** — every skill ships a
`permissions.json` alongside `SKILL.md`:

```json
{
  "bash": ["Bash(_rtx/_example_tool.sh:*)"],
  "network": ["WebSearch", "WebFetch(https://example.com/*)"]
}
```

Empty array `[]` for unused categories. Entries map to the active agent's
permission allow-list when that agent supports one. Do not cascade another
skill's permissions here; list that skill in `depends_on_skills` instead and
let permission tooling derive transitive grants from declared dependencies.

For blueprint-migrated skills, `permissions.json` is generated from
`suggested_permissions` in `blueprint.yaml`. `suggested_permissions` is
advisory, not a grant. It should explain what is safe and useful to pre-approve
for smoother execution.

**5. Frontmatter `description:` is a trigger declaration, not a summary** —
write it as "Use when..." followed by the triggering conditions and symptoms
that signal this skill applies. Never summarize the workflow, steps, or outputs
in the description.

**6. Output-focused, terse writing** — specify what to invoke and how to
interpret output. Implementation internals belong in tool/script docs, not
`SKILL.md`. Every line earns its place.

**7. `blueprint.yaml` owns all interface definitions** — the generated
interface blocks in `SKILL.md` are the single source of truth for interface
names, invocation forms, and descriptions. The skill body must not restate,
re-explain, or re-invoke any interface. Specifically:

- `interfaces.machine.<name>.description` describes what the machine interface
  does.
- `interfaces.machine.<name>.usage` provides the complete invocation argument
  template.
- `patterns[*].notes` gives mode-specific detail where multiple calling modes
  exist.
- `interfaces.llm.<name>` documents the LLM-facing interface file and
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
    or `officina.dispatcher.dispatch()`
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
  - `interfaces.machine` describes dispatcher-callable interfaces
  - `interfaces.llm` describes LLM-facing interfaces
  - `interface_version` is the major version of that public contract

**10. No code in SKILL.md — runtime files only, with one exception** — skill files
must not contain executable code logic. Any logic belongs in a dedicated file
under `_rtx/`, except when the skill's purpose is to provide an interface to
a specific external tool and that tool is declared in frontmatter `tools:`.

**11. State data lives under the skill's directory** — any persistent state a
skill writes must be stored under the skill's own directory, not under system
directories or elsewhere outside the skills tree.

**12. Sensitive configs live under `~/.config/<skill-name>/`** — passwords,
API keys, OAuth tokens, and credentials must go there, never under the skill
directory.

**13. Prefer widely available, cross-platform tools at every layer** —
language, runtime, and any external tools invoked. Skills must work out of the
box across Linux, macOS, and Windows on machines other than your own.

Date and time formatting at IO boundaries must avoid host-specific formatting
extensions. In Python, do not use GNU/POSIX-only or Windows-only `strftime`
padding modifiers such as `%-m`, `%-d`, `%#m`, or `%#d`. Put shared date/time
storage and display formats in the first-party helpers under
`officina.common.dates` instead of retyping ad hoc formatting logic in each
skill. This is mechanically checked by `validators/portable_dates.py`.

**14. Shared skill content must stay neutral about which specific AI-assistant
host it runs under.** Enforced by `validators/platform_neutral.py`.

- `SKILL.md`, `blueprint.yaml`, and any generically named runtime file must not
  name a specific host.
- If a skill genuinely needs host-specific logic, put that logic in a file
  whose own filename names the host.
- `__init__.py` remains the conventional aggregation seam for host-specific
  modules.

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
