---
name: my-writing-skills
description: Use when creating or editing a personal skill in ~/.claude/skills/
---

## Personal Conventions

**1. Skill categories** — declare `Category: <name>` near the top of `SKILL.md`. Valid values: `~/.claude/skills/references/skill-categories.md`. Omit only if no existing category fits.

**2. `my-X` naming and structure** — a personal override of upstream skill `X` is named `my-X`. Every `my-X` skill must follow this layout:
- Personal overrides and additions at the top (what's different or added).
- Then a **REQUIRED — NON-NEGOTIABLE** instruction to invoke the original `X` skill at the bottom. The original skill's rules apply in full; the personal section adds on top.

**3. `permissions.json`** — every skill ships one alongside `SKILL.md`:
```json
{
  "bash": ["Bash(/path/to/script:*)"],
  "network": ["WebSearch", "WebFetch(https://example.com/*)"]
}
```
Empty array `[]` for unused categories. Entries map directly to `.claude/settings.json`'s `permissions.allow`.

**4. Output-focused writing** — specify what to invoke and how to interpret output. Implementation internals belong in tool/script docs, not `SKILL.md`.

**5. Terse writing** — every line earns its place. No restatements, no motivation paragraphs. Long skills burn context on every invocation.

**6. Commit and push after every skill change** — when a skill is created or modified and the result is complete, **show the user the diff and ask for confirmation before committing**. Once confirmed, stage the changed files in `~/.claude`, commit, and push to `origin`. Skills are versioned in `github.com/S-Moeen/claude-config`; an unpushed change is not backed up and not portable.

**7. Skills are components in an evolving system — design accordingly.**

- **Reuse, don't reimplement.** Before writing new behavior, check whether an existing skill already covers it. If yes, invoke or extend that skill. Duplication means two places to update when behavior changes; reuse means one. Failing to reuse when a suitable skill exists is a defect. Example: `daily-plan` invokes `lists`, `g-calendar`, and `weather` rather than reimplementing any of them.
- **Depend on interfaces, not internals — invoke the skill, never its scripts.** Each skill's scripts are private to that skill. When your skill needs behavior another skill provides, invoke that skill and let it run its own scripts. Directly calling another skill's scripts bypasses its logic and couples to its internals. Reuse maximally, but only through skill invocation.
- **Make your own interface explicit.** State what inputs your skill expects and what outputs it produces, so future skills can depend on you cleanly. If your skill runs a script, document the invocation pattern and output format in `SKILL.md`.

**8. No code in SKILL.md — scripts only** — skill files must not contain executable code logic. Any logic (shell commands, Python, etc.) belongs in a dedicated file under `scripts/`. `SKILL.md` specifies only *when* to call a script, *how* to invoke it, and how to interpret its output. The script file itself carries everything else: what it does, how it works, and its full interface (arguments, flags, exit codes, output format). This minimizes permission prompts: scripts under `~/.claude/skills/*/scripts/` are pre-approved, whereas inline Bash in a skill body triggers approval on every run.

**9. State data lives under the skill's directory** — any persistent state a skill writes (logs, cache, data files, watermarks, etc.) must be stored under `~/.claude/skills/<skill-name>/`, not under system directories (`/tmp`, `/var`, `~/.config`, etc.) or anywhere outside the skills tree.

---

**REQUIRED — NON-NEGOTIABLE:** Invoke `superpowers:writing-skills` and read it fully before proceeding. All upstream rules apply; the conventions above are added on top.
