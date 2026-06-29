# List Manager — Project Lessons

## Color rendering in conversation output (exhaustive empirical map)

**Context:** beatify.py `--diff` output shown in `diff` code blocks in conversation. Tested exhaustively June 2026.

**Confirmed colors (3 total):**
- **Green**: `+` prefix, OR any line whose content contains `=` characters (even with space prefix — space is hidden, `=` triggers green). Also `---`, `+++`, merge conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`), git meta lines (`diff --git`, `index`, `old mode`, `new mode`), `\`, `!`, `~` — these all render green, not their "standard" diff color.
- **Red**: single `-` prefix only. The `-` character is always visible (no hidden equivalent for red).
- **White**: space prefix (` `) with no `=` in content; `!!`; everything else not listed above.

**Confirmed non-functional:**
- `@@...@@` hunk headers — do NOT render cyan in this renderer, even with proper git format (`@@ -0,0 +1 @@ Name`). Render white.
- `{n,m}` line-highlight annotation — works with `python` etc. but NOT with `diff` (annotation shows as literal text, not applied).
- `diff-python`, `diff-yaml` and other combo language tags — do not blend syntax highlighting with diff colors; render gray/white only.
- ANSI escape codes (including 24-bit true-color `\033[38;2;R;G;Bm`) — the ESC character is stripped but the remainder (`[38;2;255;0;0m`) renders as visible garbage text. Not silently ignored — actively corrupts output.
- HTML `<span style="color:...">` — stripped by conversation renderer.
- Other syntax highlighters (Python, CSS, YAML, TOML, INI) — all render white/unstyled in the conversation renderer.
- `---` (three dashes) — renders **green**, not red. Treated as a diff file header (`--- a/file`), not a deletion marker. Do not use `---` expecting red separators.

**Key asymmetry:** Green can be achieved without a visible marker (space prefix + `=` content). Red cannot — `-` is always shown. There is no mechanism for cyan, gray, bold, or italic in diff blocks.

**Use/Avoid:** Use ` === Name ===` (space + `=`) for green headers with no visible marker. Use `-=== Name ===` for red headers (accepts the visible `-`). Do not attempt ANSI, HTML, `@@`, or language combos. Do not attempt to get a 4th color — none exists in this renderer.

---

## beautify.py diff renderer design decisions

**Context:** `--diff` renderer redesigned June 2026 for in-conversation color display.

**Decisions:**
- State symbols (`☐ ▷ ✓ ✗`) removed from diff output — color alone conveys state.
- `inprogress` and `accepted` → `+` (green); `rejected` → `-` (red); all others → ` ` (white).
- Category headers: `+{pad}=== Name ===` (green, space hidden by renderer, `+` shown).
- Subcategory headers: `-{pad}=== Name ===` (red, `-` shown — unavoidable).
- `todo` and `potential-actions` schemas auto-use diff renderer; other schemas fall back to rich.
- Plain ANSI renderer (`--no-color`) removed — ANSI is stripped in the conversation renderer anyway.

**Use/Avoid:** Do not re-add state symbols to diff output. Do not reintroduce `--no-color`. When adding new schemas that need color, add them to `DIFF_SCHEMAS` in beautify.py.

---

## Edit UI highlighting vs. code block rendering

**Context:** User asked whether the syntax highlighting visible in file-edit proposals can be reused for beautify output.

**Lesson:** The multi-color syntax highlighting in file edit proposals is the assistant's native edit UI renderer, triggered only when the Edit/Write tools are used on actual files. It is not a markdown feature and cannot be reproduced in conversation text or code blocks.

**Use/Avoid:** Do not suggest repurposing the edit UI for display purposes. The two rendering paths are completely separate.

---

## JSONSchema `additionalProperties: false` with `if/then/else`

**Context:** `domain_category` in `todo.json` uses `if/then/else` to apply different subcategory constraints for Personal vs. other domains.

**Lesson:** `additionalProperties: false` only sees properties declared in the **sibling `properties` object** — not properties defined inside `then`/`else` branches. Any property that must be allowed needs a stub entry (e.g., `"categories": {}`) in the base `properties` block, even if its full schema is only defined conditionally.

**Use/Avoid:** Always add a stub `"categories": {}` (or any conditionally-typed field) to the base `properties` alongside `additionalProperties: false`. Missing this causes validation to reject valid documents.

---

## Migration: relative deadline parsing

**Context:** `migrate_md.py` parses natural-language deadlines like "this week" or "by next monday".

**Lesson:** "Relative" deadlines must be relative to the **entry's creation date**, not the migration date. Pass `relative_to=entry["created"]` to `dateparser`'s `RELATIVE_BASE` setting. Using `datetime.today()` as the base silently produces wrong deadlines for historical entries.

**Use/Avoid:** Always pass `relative_to=created` when calling `parse_deadline` for entries with a known creation date.

---

## Migrated files status

**Context:** Migration run in June 2026 session.

**Lesson:** `/tmp/todo_new.yaml`, `/tmp/potential-actions_new.yaml`, `/tmp/shopping_new.yaml` were migrated from the original `.md` files but **not yet uploaded to cloud**. Old `.md` files should be deleted after upload is confirmed.

**Use/Avoid:** Before assuming cloud lists are current, verify upload was completed.
