# Remove Email-Triage Personal Preferences

## Goal

Remove the complete personal-preference subsystem from `email-triage` without
changing its canonical triage workflow, machine interfaces, scheduler, logs,
or watermark behavior. A later design may add a replacement independently.

## Approaches considered

1. Remove only the updater interface. This leaves an empty preference source
   and preference-loading behavior with no writer, so it preserves a confusing
   half-feature.
2. Disable the router branch but retain all files. This avoids a graph change
   but does not satisfy deletion and leaves dead interface artifacts.
3. Remove the entire preference subsystem. This is the selected approach: it
   removes the updater, source, router branch, and triage consumption together.

## Resulting interface design

`email-triage.llm.default` becomes a single-route entrypoint that always selects
`email-triage.llm.triage`. It no longer advertises preference-management
requests or asks callers to choose between modes.

`email-triage.llm.triage` retains the existing email classification, list
routing, logging, failure, and watermark workflow. Its personal-preferences
section, behavior-source edge, and preference-file read declaration are
removed.

Because both exported LLM contracts change incompatibly, increment
`email-triage.llm.default` and `email-triage.llm.triage` from version 1 to
version 2. No other skill currently declares either interface as a dependency.

## Files

Delete:

- `skills/email-triage/llm_interfaces/update-personal-preferences.md`
- `skills/email-triage/llm_interfaces/.update-personal-preferences.md.blueprint.yaml`
- `skills/email-triage/references/personal-preferences.md`
- `skills/email-triage/references/.personal-preferences.md.blueprint.yaml`

Update:

- `skills/email-triage/blueprint.yaml`
- `skills/email-triage/.SKILL.md.blueprint.yaml`
- `skills/email-triage/llm_interfaces/.triage.md.blueprint.yaml`
- `skills/email-triage/llm_interfaces/triage.md`
- `skills/email-triage/SKILL.md` through blueprint synchronization
- `skills/email-triage/tests/test_llm_routing.py`

Historical design and plan documents remain unchanged because they describe
the earlier implementation accurately.

## Tests and validation

Tests will first assert the new contract and fail against the current files:

- the default interface exposes only the triage route at version 2;
- preference-management files and graph identifiers are absent;
- the triage interface has no preference source or preference-file IO;
- canonical triage instructions remain present.

After implementation, run the complete `email-triage` test suite, blueprint
synchronization check, repository validators, and `git diff --check`. Any
unrelated pre-existing validator failures will be reported separately.
