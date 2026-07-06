# Skill Module Standards

**A skill is a software module.** The standards below are its module spec — the same engineering discipline that applies to any well-designed module applies here: declared interfaces, enforced abstraction boundaries, single responsibility, explicit state contracts, and dependency hygiene. These are not stylistic preferences; they are structural requirements.

**1. Skill identity and contract come first** — every skill has a stable dash-separated name and declares its dependency and interface contract before workflow instructions.

The skill directory name and frontmatter `name:` must match exactly. Skill names must be lower-case, dash-separated, and at least two words:

```text
list-manager
get-weather
email-client
```

Do not create one-word skill names such as `lists`, `weather`, or `email`. The dash-separated rule keeps skill IDs distinct from ordinary prose and makes mechanical dependency checks reliable.

Historical legacy skills may keep a hand-authored dependency block in `SKILL.md`:

```markdown
Category: automation

Dependencies:
- list-manager
- g-calendar
- get-weather
```

Use `Dependencies: none` when the skill must not invoke other skills. List only skill names, not paths, scripts, or implementation details.

In this repository, local skills use a canonical `blueprint.yaml` next to `SKILL.md`. Initialize a new blueprint by copying `references/skill-blueprint-template.yaml` into the skill directory as your base, then customize it in place. The blueprint is hand-authored and comment-rich. It is the source of truth for:

- `category`
- `interface_version`
- `depends_on`
- `suggested_permissions`
- `skill_interface`
- `script_interfaces`

For blueprint skills, `depends_on_skills` and the top-of-file contract block in `SKILL.md` are generated compatibility artifacts. The blueprint is canonical; the generated files must match it exactly.
Every local skill must therefore have a sibling `blueprint.yaml`, exactly one generated contract block in `SKILL.md`, and generated artifacts that stay in sync with the blueprint.

Every exact skill-name mention in the body of `SKILL.md` must also match the dependency set. Do not mention a skill as an invoked collaborator unless it is in both the `Dependencies:` block and `depends_on_skills`.

Dependencies authorize skill invocation and, for blueprint-migrated dependencies, dispatcher calls through the installed `dispatcher` command (CLI) or `script_dispatcher.dispatch()` (Python) to that skill's exported script interface. A dependency never authorizes direct access to another skill's files or raw script paths.

**Blueprint authoring — REQUIRED: Initialize by copying the template**

**Always initialize a new blueprint by copying `references/skill-blueprint-template.yaml` to `skills/<skill-name>/blueprint.yaml`.** Do not create a blueprint from scratch; start with the template as your base. The template includes:

- Complete documentation on all blueprint fields and concepts
- Detailed explanation of the pattern-based invocation system
- Examples of single-pattern and multi-pattern interfaces
- Access control patterns (`exported`, `allowed_callers`)
- Constraint validation via regex (`positional_patterns`, `flag_patterns`)
- Best practices for blueprint design
- Regex syntax guide with common examples

**Keep the comments.** The template's extensive comments are part of the specification; they explain intent and guide future edits. Deleting or shortening them reduces clarity and defeats the purpose of the template.

**Blueprint authoring notes**

- `category`: required single string from the typed enum in `references/blueprint/schema.json`. The taxonomy is a tree; names encode hierarchy via postfix (`workflow-general-assistant` ⊂ `general-assistant`). A skill may sit at any node — leaf or intermediate.
- `interface_version`: required positive integer. Bump only when exported contract changes in a breaking way.
- `depends_on`: mapping from skill name to dependency contract. Use `major_version` for blueprint dependencies and `{}` only for legacy non-blueprint dependencies. Include `exports` list to name which interfaces this skill is allowed to use.
- `suggested_permissions`: mapping with `bash` and `network` lists. Every entry must include a `reason` explaining why it's safe to pre-approve.
- `skill_interface`: three plain-language lists (`inputs`, `outputs`, `side_effects`) that describe the skill's high-level contract.
- `script_interfaces`: **Pattern-based invocation contracts** (see template for details). Each interface group has a required unique owner-facing `id`, a shared `command`, an optional `default` subinterface, and optional named `subinterfaces` for narrower external views. Interface-id uniqueness within a skill is enforced by `skills/my-writing-skills/validators/interface_ids.py`. Patterns support:
  - Positional argument constraints (`min_positionals`, `max_positionals`, `positional_patterns` with regex)
  - Flag constraints (`required_flags`, `allowed_flags`, `forbidden_flags`, `flag_patterns` with regex)
  - Access control (`allow_all_skills`, `allowed_callers`)
  - Multi-pattern interfaces for different calling styles (file vs stdin, etc.)
  - `notes` on every pattern — **required, not optional**. Notes are injected verbatim into the generated SKILL.md interface block. A dependent skill calling through the dispatcher reads only SKILL.md, not `blueprint.yaml` or the underlying script. Write notes so a caller can invoke the interface correctly from SKILL.md alone.

**Dispatcher role**

The installed `dispatcher` command and the shared `script_dispatcher` Python package are the only sanctioned local cross-skill script boundaries for blueprint-migrated skills. Their job is to:

1. Load the callee's `blueprint.yaml`
2. Resolve the requested `script_interface` id to either the owner-facing default surface or a named subinterface
3. Allow the owning skill to use its full owner-facing/default interface
4. Allow external callers to use only externally callable ids and patterns
5. Verify that the caller declared the dependency with matching major version
6. Verify that the interface is listed in `depends_on.exports` (if caller specified)
7. Verify that the caller is in `allowed_callers` (if interface is restricted)
8. Match the actual invocation against available patterns:
   - Check positional count constraints (min/max)
   - Check positional argument values against regex patterns
   - Check required flags are present, forbidden flags are absent
   - Check flag values against regex patterns
9. Resolve the declared working directory and execute the command

The pattern-based approach enables **compile-time validation**: git hooks verify that only allowed skills can export restricted interfaces, catching misuse before deployment rather than at runtime.

Use `--dry-run` to inspect the resolved command without executing it:
```bash
dispatcher --dry-run --caller-skill daily-plan \
  list-manager read-list /tmp/todo.yaml
```

For Python skill code, the canonical form is:
```python
from script_dispatcher import dispatch
```

Do not invoke the `dispatcher` CLI from Python skill code, and do not modify `sys.path` to reach `script_dispatcher`. That canonical Python-side rule is enforced by `skills/my-writing-skills/validators/dispatcher_usage.py`.

**Installer-bootstrap exception.** The `dispatcher` command itself is a launcher generated by `install-assistant-tools` that runs `script_dispatcher` from the repo (`$AI`); first-party code is never pip-installed. The installer therefore legitimately (a) references the launcher by name when generating or removing it, and (b) bootstraps `script_dispatcher` imports via `sys.path` before any launcher exists. For this reason `install-assistant-tools` is exempted from the dispatcher-usage validator as a whole (its `_EXCLUDED_SKILLS` set), matching the existing skill-level exemption pattern in `platform_neutral.py`. No other skill is exempt.

Every Python `dispatch(...)` call must also include `caller_skill` set to the owning skill's exact name; that value must be a string literal or a module-level string constant that resolves statically. This is enforced by `skills/my-writing-skills/validators/dispatch_caller_skill.py`.

**Injection lifecycle**

`../../skills/my-writing-skills/scripts/sync_skill_blueprints.py` injects and refreshes the generated compatibility artifacts for blueprint skills:

- `depends_on_skills`
- `permissions.json`
- the generated contract block placed immediately after the YAML frontmatter in `SKILL.md`
- the generated owner-facing dispatcher interface block placed immediately after the contract block

That generated block is not user-authored. Do not edit it by hand. These checks are enforced on every commit by `validators/runner.py` (called from `.githooks/pre-commit`) via:

- `skills/my-writing-skills/validators/blueprints.py` — blueprint presence, injection layout, and artifact sync
- `skills/my-writing-skills/validators/boundaries.py` — local script boundary enforcement
- `skills/my-writing-skills/validators/dispatch_caller_skill.py` — Python dispatch caller identity must match the owning skill
- `skills/my-writing-skills/validators/dispatcher_usage.py` — canonical Python-side dispatcher usage
- `skills/my-writing-skills/validators/skill_md_dispatch.py` — generated owner-facing `SKILL.md` interface block must expose dispatcher commands, not raw scripts
- `skills/my-writing-skills/validators/blueprint_relationships.py` — cross-blueprint dependency constraints
- `skills/my-writing-skills/validators/interface_ids.py` — per-skill uniqueness and layout checks for interface ids

Regression tests for the blueprint dispatcher and sync script live in `skills/my-writing-skills/tests/test_blueprint_tools.py`.

**2. Skill categories** — declare `category` in `blueprint.yaml`. Must be one of the typed enum values in `references/blueprint/schema.json`. The taxonomy is a tree encoded by postfix; see the `category` section in `references/blueprint/guide.md` for the full tree. Do not invent new category names; update the schema enum and the `_CATEGORY_NODES` set in `skills/my-writing-skills/validators/blueprints.py` first.

For `research-assistant` skills applied to `.tex` files: check whether a top-of-document profile comment exists before proceeding; if not, use `make-tex-docstring` first.

**3. `my-X` naming and structure** — a personal override of upstream skill `X` is named `my-X`. Every `my-X` skill must follow this layout:
- Personal overrides and additions at the top (what's different or added).
- Then a **REQUIRED — NON-NEGOTIABLE** instruction to invoke the original `X` skill at the bottom. The original skill's rules apply in full; the personal section adds on top.

**4. `permissions.json` and `suggested_permissions`** — every skill ships a `permissions.json` alongside `SKILL.md`:
```json
{
  "bash": ["Bash(scripts/example.sh:*)"],
  "network": ["WebSearch", "WebFetch(https://example.com/*)"]
}
```
Empty array `[]` for unused categories. Entries map to the active agent's permission allow-list when that agent supports one. Do not cascade another skill's permissions here; list that skill in `depends_on_skills` instead and let permission tooling derive transitive grants from declared dependencies.

For blueprint-migrated skills, `permissions.json` is generated from `suggested_permissions` in `blueprint.yaml`. `suggested_permissions` is advisory, not a grant. It should explain what is safe and useful to pre-approve for smoother execution. Use structured entries plus a short `reason`, for example:

```yaml
suggested_permissions:
  bash:
    - command: ["python3", "scripts/lists.py"]
      args_prefix: ["read"]
      reason: "Stable read path behind the exported read-list interface."
  network:
    - kind: web_search
      reason: "Needed when live verification is required."
```

The generated `permissions.json` remains the compatibility artifact for current tooling.

**5. Frontmatter `description:` is a trigger declaration, not a summary** — write it as "Use when..." followed by the triggering conditions and symptoms that signal this skill applies. Never summarize the skill's workflow, steps, or outputs in the description.

If the description summarizes the workflow, agents read it instead of the skill body and follow the shorter summary — the full SKILL.md becomes documentation they skip. The description should only answer "should I load this skill right now?", not "what does this skill do?"

```yaml
# Bad — summarizes workflow; agent may follow this instead of reading the skill
description: Use when planning your day — fetches calendar and todo, computes free time, ranks tasks.

# Good — triggering conditions only
description: Use when the user asks to plan their day, check their schedule, or review today's actions.
```

**6. Output-focused writing** — specify what to invoke and how to interpret output. Implementation internals belong in tool/script docs, not `SKILL.md`.

**6. Terse writing** — every line earns its place. No restatements, no motivation paragraphs. Long skills burn context on every invocation.

**6. blueprint.yaml owns all interface definitions** — the generated interface block in `SKILL.md` (between `<!-- BEGIN/END BLUEPRINT INTERFACES -->`) is the single source of truth for interface names, invocation forms, and descriptions. The skill body must not restate, re-explain, or re-invoke any interface. Specifically:

- The `description` field on each `script_interfaces` entry describes what the interface does.
- The `usage` field provides the complete invocation argument template (positionals, required flags, optional flags). The sync script renders this into a ready-to-run `dispatcher` invocation in the generated block.
- The `notes` field on each pattern gives mode-specific detail where multiple calling modes exist.
- The skill body references interface names only — it never shows `dispatcher --caller-skill` invocations or `scripts/` paths. Behavioral rules (what to do with output, ordering, invariants) belong in the body; invocation mechanics belong in the blueprint.

This separation is enforced by `skills/my-writing-skills/validators/skill_md_dispatch.py`, which fails the build if `dispatcher --caller-skill` invocations or `scripts/` paths appear in the hand-authored skill body. Prose references to the word "dispatcher" are allowed.

**The generated block must be sufficient for a first-attempt correct invocation.** A model reading SKILL.md must be able to construct and execute a valid call without consulting blueprint.yaml, running `--help`, or trial-and-error. Test against this standard when authoring interfaces:

- If `description` is missing, the model cannot reason about which interface to use.
- If `usage` is missing or shows `...`, the model cannot construct the call correctly.
- If `notes` omit mode-specific constraints, the model will guess wrong flags or ordering.

**Interfaces that require runtime state should say so explicitly in the skill body.** When an operation depends on information only available at call time (e.g., category structure in a list, available ids, existing entries), the skill body must instruct the model to read that state first — before attempting the operation. Discovering required state through failure is a skill design defect. Example: "if the target category is not already in context, run `cloud-read-beautify` first."

**7. Commit and push after every skill change** — when a skill is created or modified and the result is complete, **show the user the diff and ask for confirmation before committing**. Once confirmed, stage the changed files, commit, and push to `origin`. Skills are versioned in the shared skills repository; an unpushed change is not backed up and not portable.

**8. Skills are components in an evolving system — design accordingly.**

- **Reuse, don't reimplement.** Before writing new behavior, check whether an existing skill already covers it. If yes, invoke or extend that skill. Duplication means two places to update when behavior changes; reuse means one. Failing to reuse when a suitable skill exists is a defect. Example: `daily-plan` invokes `list-manager`, `g-calendar`, and `get-weather` rather than reimplementing any of them.
- **Depend on interfaces, not internals.** There are only two valid cross-skill boundaries:
  - invoke the dependency skill as a skill
  - call the dependency skill's exported script interface through `dispatcher` or `script_dispatcher.dispatch()`

  Directly naming another skill's script path is forbidden. The owning skill's `blueprint.yaml` defines the full internal script interface under `script_interfaces`, including the owner-facing default id and any narrower named subinterfaces.
- **Do not introduce new cross-skill Python imports.** If behavior should be shared across skills, expose it through a skill invocation or exported script interface. If truly shared library code is needed, move it to an explicit shared library area outside individual skill script directories.
- **Do not reach into another skill's script directory from local scripts.** For blueprint-migrated skills, local `.py` and `.sh` files must not call, source, or add another skill's `scripts/` directory to `sys.path`. Use a skill invocation, `dispatcher`, or `script_dispatcher.dispatch()` instead.
- **Keep SKILL.md references local.** Paths in `SKILL.md` must be relative. A skill may refer to files under its own directory, to shared `../references/` material, and to shared repo tools under `../../tools/`. It must not mention parent-path addresses such as `../other-skill/...`, `../../skills/...`, or any absolute filesystem path to another skill. System-level paths are allowed only for durable user configuration or executable interfaces that are intentionally outside the skills tree, such as `~/.config/<skill-name>/` and installed commands under `bin`.
- **Make your own interface explicit.** State what inputs your skill expects and what outputs it produces, so future skills can depend on you cleanly. For blueprint skills:
  - `skill_interface` describes the skill-level contract
  - `script_interfaces` describes the owning skill's full script surface
  - the owner-facing default subinterface shares the parent interface id
  - named `subinterfaces` let you restrict other skills without constraining the owner-facing default surface
  - `interface_version` is the major version of that public contract

**9. No code in SKILL.md — scripts only, with one exception** — skill files must not contain executable code logic. Any logic (shell commands, Python, etc.) belongs in a dedicated file under `scripts/`. `SKILL.md` specifies only *when* to call a script, *how* to invoke it, and how to interpret its output. The script file itself carries everything else: what it does, how it works, and its full interface (arguments, flags, exit codes, output format). This minimizes permission prompts: scripts under each skill's `scripts/` directory can be pre-approved, whereas inline Bash in a skill body triggers approval on every run.

**Exception — declared tools:** when a skill's purpose is to provide an interface to a specific external tool, that tool may be declared in the frontmatter and its commands may appear directly in `SKILL.md`. Declare it with a `tools:` field:

```yaml
---
name: pdf-to-markdown
description: Convert PDF to Markdown using the maker CLI
tools:
  - maker
---
```

What **may** appear inline for a declared tool: installation instructions, flags and options, invocation patterns, output format and interpretation. What **may not** appear inline even with a declaration: orchestration logic, data processing, multi-step control flow built on top of the tool — those still belong in `scripts/`. The corresponding permission entry still goes in `permissions.json` (e.g. `"Bash(maker:*)"`). A tool not listed under `tools:` is not covered by this exception, regardless of whether it appears in the description.

**10. State data lives under the skill's directory** — any persistent state a skill writes (logs, cache, data files, watermarks, etc.) must be stored under the skill's own directory, not under system directories (`/tmp`, `/var`, `~/.config`, etc.) or anywhere outside the skills tree.

**11. Sensitive configs live under `~/.config/<skill-name>/`** — passwords, API keys, OAuth tokens, and any credentials must go in `~/.config/<skill-name>/` (mode 600), never under the skill directory. `~/.config/` is outside the skills git repo and is never committed. Distinguish between the original credential file (e.g., `client.json` — source of truth, kept permanently) and any derived/transformed file (e.g., `credentials.json` — generated by a setup script, may be overwritten). Document both files and their roles in the skill's setup section.

**12. Prefer widely available, cross-platform tools at every layer — language, runtime, and any external tools invoked.** Skills must work out of the box across operating systems (Linux, macOS, Windows) and on machines other than your own. Ask: *would this run without installing anything extra on a typical Linux, macOS, or Windows machine belonging to someone else?*

---

## Validator and test conventions

Conformance checks run on every commit via `validators/runner.py` (called from `.githooks/pre-commit`). The runner auto-discovers two packages:

- **`validators/`** — repo-wide checks (e.g. platform neutrality)
- **`skills/my-writing-skills/validators/`** — skill-system checks (names, metadata, blueprints, boundaries, dependencies, blueprint relationships)

### Adding a new validator

1. Create `validators/<name>.py` or `skills/my-writing-skills/validators/<name>.py`.
2. Export exactly one function: `validate(repo_root: Path) -> list[str]` — return an empty list on success, or a list of human-readable error strings.
3. Optionally add a `main()` so it can be run standalone: `python3 path/to/<name>.py`.
4. Add a `tests/validate_<name>.py` with at least a pass case and a fail case. Use `pytest` conventions (plain functions, `tmp_path` fixture for isolation). Load the validator via `importlib.util.spec_from_file_location` rather than importing it as a package.

The runner picks up the new file automatically — no registration needed.

### Test file conventions

- Validator tests live in `tests/validate_<name>.py` (unit tests for conformance logic, use `tmp_path`).
- Behavior tests live in `skills/<skill-name>/tests/` (integration tests for the skill's own scripts).
- Use `importlib.util.spec_from_file_location` to load validators in tests — avoids package naming collisions and works regardless of working directory.
