# Interactive LLM testing dossier

This directory is the durable, repository-local record for the setup-interface-manager interactive experiment and the reusable pipeline distilled from it.

- [Source prompt](source-prompt.md) — original experiment request, with wording and paragraph structure preserved.
- [Experiment report](experiment-report.md) — scenario results, failure classifications, lessons, and repeat recipe.
- [Redacted evidence](experiment-evidence.md) — reproducible command surface, representative structured evidence, and fixture identity.
- [Campaign index](campaign-index.json) — redacted P00–P09/F00–F09 status and evidence inventory.
- [Implementation plan](implementation-plan.md) — proposed `interactive-llm-testing` skill architecture and task sequence.

## Evidence boundary

The raw campaign is preserved outside Git at `<repository-root>/.famulus/interactive-llm-testing/campaigns/setup-interface-manager-2026-09-01/`. Its 44,783-file SHA-256 manifest and preservation record are adjacent to that directory. It is not repository material: it contains private prompts, raw model events, authentication-adjacent state, temporary ledgers, and isolated installations. The report, evidence appendix, and campaign index are the durable redacted derivatives and are sufficient to understand or implement the plan.
