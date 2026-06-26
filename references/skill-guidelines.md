# Skill Module Standards

**A skill is a software module.** The standards below are its module spec — the same engineering discipline that applies to any well-designed module applies here: declared interfaces, enforced abstraction boundaries, single responsibility, explicit state contracts, and dependency hygiene. These are not stylistic preferences; they are structural requirements.

**1. Skill identity and dependencies come first** — every skill has a stable dash-separated name and declares skill-level dependencies before workflow instructions.

The skill directory name and frontmatter `name:` must match exactly. Skill names must be lower-case, dash-separated, and at least two words:

```text
list-manager
get-weather
email-client
```

Do not create one-word skill names such as `lists`, `weather`, or `email`. The dash-separated rule keeps skill IDs distinct from ordinary prose and makes mechanical dependency checks reliable.

In `SKILL.md`, put a `Dependencies:` block near the beginning, immediately after the `Category:` line:

```markdown
Category: automation

Dependencies:
- list-manager
- g-calendar
- get-weather
```

Use `Dependencies: none` when the skill must not invoke other skills. List only skill names, not paths, scripts, or implementation details.

Each skill also ships a sidecar `depends_on_skills` next to `SKILL.md`, with the same dependency names, one per line. Use an empty file for no dependencies. Scripts and permission generators read `depends_on_skills`; humans read the `SKILL.md` block. These two declarations must match.

Every exact skill-name mention in the body of `SKILL.md` must also match the dependency set. Do not mention a skill as an invoked collaborator unless it is in both the `Dependencies:` block and `depends_on_skills`.

Dependencies authorize skill invocation only. A dependency does not authorize direct access to another skill's files or scripts.

**2. Skill categories** — declare `Category: <name>` near the top of `SKILL.md`. Valid values: top-level `references/skill-categories.md`. Omit only if no existing category fits.

**3. `my-X` naming and structure** — a personal override of upstream skill `X` is named `my-X`. Every `my-X` skill must follow this layout:
- Personal overrides and additions at the top (what's different or added).
- Then a **REQUIRED — NON-NEGOTIABLE** instruction to invoke the original `X` skill at the bottom. The original skill's rules apply in full; the personal section adds on top.

**4. `permissions.json`** — every skill ships one alongside `SKILL.md`:
```json
{
  "bash": ["Bash(scripts/example.sh:*)"],
  "network": ["WebSearch", "WebFetch(https://example.com/*)"]
}
```
Empty array `[]` for unused categories. Entries map to the active agent's permission allow-list when that agent supports one. Do not cascade another skill's permissions here; list that skill in `depends_on_skills` instead and let permission tooling derive transitive grants from declared dependencies.

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
- **Depend on interfaces, not internals — invoke the skill, never its scripts.** Each skill's scripts are private to that skill. When your skill needs behavior another skill provides, invoke that skill and let it run its own scripts. Directly calling another skill's scripts bypasses its logic and couples to its internals. Reuse maximally, but only through skill invocation. Do not name or reference paths to a dependency skill's scripts anywhere in `SKILL.md` — even as examples or clarifications. Naming them invites direct calls.
- **Keep SKILL.md references local.** Paths in `SKILL.md` must be relative. A skill may refer to files under its own directory and to shared `../references/` material only. It must not mention parent-path addresses such as `../other-skill/...`, `../../skills/...`, or any absolute filesystem path to another skill. System-level paths are allowed only for durable user configuration or executable interfaces that are intentionally outside the skills tree, such as `~/.config/<skill-name>/` and installed commands under `bin`.
- **Make your own interface explicit.** State what inputs your skill expects and what outputs it produces, so future skills can depend on you cleanly. If your skill runs a script, document the invocation pattern and output format in `SKILL.md`.

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