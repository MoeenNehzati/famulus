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

Dependencies authorize skill invocation and, for blueprint-migrated dependencies, dispatcher calls through `../../scripts/invoke_skill_export.py` to that skill's exported script interface. A dependency never authorizes direct access to another skill's files or raw script paths.

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

- `category`: required string, or a list of strings for a genuinely multi-category skill. Values from `references/skill-categories.md`.
- `interface_version`: required positive integer. Bump only when exported contract changes in a breaking way.
- `depends_on`: mapping from skill name to dependency contract. Use `major_version` for blueprint dependencies and `{}` only for legacy non-blueprint dependencies. Include `exports` list to name which interfaces this skill is allowed to use.
- `suggested_permissions`: mapping with `bash` and `network` lists. Every entry must include a `reason` explaining why it's safe to pre-approve.
- `skill_interface`: three plain-language lists (`inputs`, `outputs`, `side_effects`) that describe the skill's high-level contract.
- `script_interfaces`: **Pattern-based invocation contracts** (see template for details). Each interface group has a required unique owner-facing `id`, a shared `command`, an optional `default` subinterface, and optional named `subinterfaces` for narrower external views. Interface-id uniqueness within a skill is enforced by `skills/my-writing-skills/validators/interface_ids.py`. Patterns support:
  - Positional argument constraints (`min_positionals`, `max_positionals`, `positional_patterns` with regex)
  - Flag constraints (`required_flags`, `allowed_flags`, `forbidden_flags`, `flag_patterns` with regex)
  - Access control (`allow_all_skills`, `allowed_callers`)
  - Multi-pattern interfaces for different calling styles (file vs stdin, etc.)

**Dispatcher role**

`../../scripts/invoke_skill_export.py` is the only sanctioned local cross-skill script boundary for blueprint-migrated skills. Its job is to:

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
python3 scripts/invoke_skill_export.py --dry-run --caller-skill daily-plan \
  list-manager read-list /tmp/todo.yaml
```

**Injection lifecycle**

`../../skills/my-writing-skills/scripts/sync_skill_blueprints.py` injects and refreshes the generated compatibility artifacts for blueprint skills:

- `depends_on_skills`
- `permissions.json`
- the generated contract block placed immediately after the YAML frontmatter in `SKILL.md`
- the generated owner-facing interface block placed immediately after the contract block when top/default interface descriptions are present

That generated block is not user-authored. Do not edit it by hand. These checks are enforced on every commit by `validators/runner.py` (called from `.githooks/pre-commit`) via:

- `skills/my-writing-skills/validators/blueprints.py` — blueprint presence, injection layout, and artifact sync
- `skills/my-writing-skills/validators/boundaries.py` — local script boundary enforcement
- `skills/my-writing-skills/validators/blueprint_relationships.py` — cross-blueprint dependency constraints
- `skills/my-writing-skills/validators/interface_ids.py` — per-skill uniqueness and layout checks for interface ids

Regression tests for the blueprint dispatcher and sync script live in `skills/my-writing-skills/tests/test_blueprint_tools.py`.

**2. Skill categories** — declare `Category: <name>` near the top of `SKILL.md`. Valid values: top-level `references/skill-categories.md`. Omit only if no existing category fits.

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

**7. Commit and push after every skill change** — when a skill is created or modified and the result is complete, **show the user the diff and ask for confirmation before committing**. Once confirmed, stage the changed files, commit, and push to `origin`. Skills are versioned in the shared skills repository; an unpushed change is not backed up and not portable.

**8. Skills are components in an evolving system — design accordingly.**

- **Reuse, don't reimplement.** Before writing new behavior, check whether an existing skill already covers it. If yes, invoke or extend that skill. Duplication means two places to update when behavior changes; reuse means one. Failing to reuse when a suitable skill exists is a defect. Example: `daily-plan` invokes `list-manager`, `g-calendar`, and `get-weather` rather than reimplementing any of them.
- **Depend on interfaces, not internals.** There are only two valid cross-skill boundaries:
  - invoke the dependency skill as a skill
  - call the dependency skill's exported script interface through `../../scripts/invoke_skill_export.py`

  Directly naming another skill's script path is forbidden. The owning skill's `blueprint.yaml` defines the full internal script interface under `script_interfaces`, including the owner-facing default id and any narrower named subinterfaces.
- **Do not introduce new cross-skill Python imports.** If behavior should be shared across skills, expose it through a skill invocation or exported script interface. If truly shared library code is needed, move it to an explicit shared library area outside individual skill script directories.
- **Do not reach into another skill's script directory from local scripts.** For blueprint-migrated skills, local `.py` and `.sh` files must not call, source, or add another skill's `scripts/` directory to `sys.path`. Use a skill invocation or `../../scripts/invoke_skill_export.py` instead.
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
