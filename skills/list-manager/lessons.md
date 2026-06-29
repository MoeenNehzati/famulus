# List Manager — Project Lessons

## Color rendering in conversation output

**Context:** beautify.py output shown in conversation via `--diff` flag.

**Lesson:** In `diff` code blocks, only `+` lines (green) and `-` lines (red) render with color in the assistant's conversation renderer (verified on this user's setup). `@@...@@` hunk headers do **not** render as cyan — they stay white. ANSI escape codes are stripped entirely in the bash tool output panel. HTML `<span style="color:...">` tags are stripped by the conversation renderer.

**Use/Avoid:** The only reliable color signal available for conversation output is `+` = green. Use `@@` headers for visual separation only, not for color. Do not attempt ANSI or HTML color in conversation output.

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
