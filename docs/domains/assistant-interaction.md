# Assistant Interaction

This domain controls how an assistant session proceeds: choosing broad or
rigorous reasoning, preserving continuity between sessions, and scheduling a
session to resume after a timeout or usage reset.

Example prompts:

- `Use loose mode to explore several approaches.`
- `Use tight mode and verify every claim.`
- `Prepare a handoff before I stop.`
- `Wake this session after my usage limit resets.`

Use `prepare-handoff` when a session produced decisions, failed paths, or
repository changes that a later session should inherit directly.

<!-- BEGIN AUTO-GENERATED DOCS: assistant-interaction -->
> Generated from live blueprints. Do not edit this block by hand.

- `llm-wakeup` — Schedule or manage an automatic assistant-session wakeup after a usage reset or timeout
- `loose-mode` — Broad, fast exploration mode with breadth over certainty
- `prepare-handoff` — Prepare a clean handoff with workflow and documentation updates
- `tight-mode` — Rigorous, verified output mode with certainty over speed
<!-- END AUTO-GENERATED DOCS: assistant-interaction -->
