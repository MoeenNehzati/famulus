# Documentation System

Famulus documentation is intentionally split between hand-written narrative and generated inventory/coverage surfaces. This file explains the automation behind that split.

## What Is Hand-Written

The following stay hand-written:

- [README.md](../../README.md)
- [`docs/user/*.md`](../user/)
- [`docs/contributors/*.md`](./)
- explanatory reference docs under [`references/`](../../references/)

Those files carry the user-facing and contributor-facing explanations, walkthroughs, examples, and design rationale.

## What Is Generated

The generated documentation surfaces are:

- [docs/skills.md](../skills.md) — the complete skill inventory
- embedded coverage blocks inside the user and contributor docs

Those surfaces are derived from live `skills/*/blueprint.yaml` files plus the descriptions in each skill's [`SKILL.md`](../../skills/skill-maker/SKILL.md).

## Centralized Code Home

Documentation generation and documentation-validation support live in [docs_tooling/](../../docs_tooling/).

That module owns:

- loading the live skill catalog
- taxonomy and coverage contracts
- rendering the skill index
- rendering coverage blocks for hand-written docs
- shared support used by validators

Top-level scripts should stay thin wrappers around `docs_tooling/`.

The local repo browser and rendered-doc server also live on this side of the boundary:

- [scripts/serve-repo.py](../../scripts/serve-repo.py)
- [docs_tooling/repo_browser.py](../../docs_tooling/repo_browser.py)

## Generated Blocks

Coverage blocks are embedded between markers such as:

```text
<!-- BEGIN AUTO-GENERATED DOCS: workflow-general-assistant -->
...
<!-- END AUTO-GENERATED DOCS: workflow-general-assistant -->
```

Do not edit the contents inside those markers by hand. Edit the surrounding prose if you need a better explanation, and rerun the generator if the skill inventory changed.

## Regenerating Doc Artifacts

From the repo root:

```bash
python3 scripts/generate-doc-artifacts.py
```

This regenerates:

- [docs/skills.md](../skills.md)
- embedded coverage blocks in the user docs
- embedded coverage blocks in [docs/contributors/README.md](README.md)

## Local Browsing

To browse the repo and rendered Markdown pages locally through a web server:

```bash
./scripts/serve-repo.py --port 8765
```

This serves:

- a lightweight repo home page
- directory browsing under `/browse/...`
- rendered Markdown pages for docs and READMEs
- raw-file links for the underlying sources

## Validators

Documentation conformance is enforced by repo validators under [validators/](../../validators/), not by prose-shape pytest tests.

The key validators are:

- [validators/readme_user_contract.py](../../validators/readme_user_contract.py)
- [validators/user_docs_cover_blueprints.py](../../validators/user_docs_cover_blueprints.py)
- [validators/contributor_docs_contract.py](../../validators/contributor_docs_contract.py)
- [validators/generated_skill_docs.py](../../validators/generated_skill_docs.py)

Run them through:

```bash
python3 validators/runner.py
```

## Adding a New Doc Contract

When you add a new documentation contract:

1. Add or update the shared logic in `docs_tooling/`.
2. Add or update the hand-written doc that owns the prose.
3. Add marker blocks if the doc needs generated coverage content.
4. Add or update the validator module under [validators/](../../validators/).
5. Regenerate docs and run validators.

This keeps the rules in one place and avoids scattering doc-generation logic across unrelated scripts.

For the local pre-commit order, GitHub Actions behavior, and Python test-suite boundaries, see [TESTING.md](../../TESTING.md).
