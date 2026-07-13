# Update Personal Email-Triage Preferences

This interface is the sole writer of `references/personal-preferences.md`.
It manages user-level triage preferences; it does not triage email.

1. Read the current preference file before proposing a change.
   If the user asks only to review preferences, take the read-only branch:
   report the current preferences without writing the file.
2. Translate the user's request into concise behavioral instructions. Do not
   store conversation history, passwords, credentials, or unrelated personal
   data.
3. Preserve unrelated preferences.
4. For a reset, removal, or other destructive rewrite, show the proposed
   result and obtain confirmation before writing. An explicit additive or
   corrective request may be applied directly.
5. Write only `references/personal-preferences.md`. When the editing surface
   supports atomic application, use it so a failed write preserves the prior
   content. On any write failure, report the failure and never claim that the
   preference was saved.
6. After a successful write, report the exact preference change, state
   explicitly that the bound-file hash changed, and report that the local skill
   audit is stale until the customized skill is separately reviewed and
   certified again.

An empty file means that only canonical triage behavior applies. Never add
headings, examples, or placeholder prose unless the user requests them,
because every stored line becomes active behavior.
