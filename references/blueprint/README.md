# Blueprint Reference

This directory is the narrow reference index for the blueprint contract. If you want the broader maintainer overview, start with [docs/contributors/README.md](../../docs/contributors/README.md).

## Files

- [guide.md](guide.md) — narrative guide to the blueprint system, patterns, and validation model
- [schema.json](schema.json) — formal schema for `blueprint.yaml`
- [template.yaml](template.yaml) — annotated starting point for a new blueprint

## Typical Flow

1. Read [guide.md](guide.md).
2. Use [template.yaml](template.yaml) when authoring or revising a blueprint.
3. Validate and sync generated artifacts with [skills/skill-maker/scripts/sync_skill_blueprints.py](../../skills/skill-maker/scripts/sync_skill_blueprints.py):

```bash
python3 skills/skill-maker/scripts/sync_skill_blueprints.py
```

## Related Docs

- [docs/contributors/README.md](../../docs/contributors/README.md) — contributor entrypoint
- [docs/scaffolding/README.md](../../docs/scaffolding/README.md) — long-form scaffolding explainer
