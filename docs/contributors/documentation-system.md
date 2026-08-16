# Documentation System

Famulus documentation is intentionally split between hand-written narrative and generated inventory/coverage surfaces. This file explains the automation behind that split.

## What Is Hand-Written

The following stay hand-written:

- [README.md](../../README.md)
- [`docs/domains/*.md`](../domains/)
- [`docs/contributors/*.md`](./)
- explanatory reference docs under [`references/`](../../references/)

Those files carry the user-facing and contributor-facing explanations, walkthroughs, examples, and design rationale.

## What Is Generated

The generated documentation surfaces are:

- [docs/skills.md](../skills.md) — the complete skill inventory
- embedded coverage blocks inside the domain and contributor docs

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

The bounded website assembler also lives on this side of the boundary:

- [scripts/docs-site.py](../../scripts/docs-site.py)
- [docs_tooling/site.py](../../docs_tooling/site.py)

MkDocs owns Markdown rendering, navigation, search, local serving, and static
site output. The repository-owned assembler decides which sources are public
and rewrites links to unpublished repository content.

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
- embedded coverage blocks in the domain docs
- embedded coverage blocks in [docs/contributors/README.md](README.md)

## Local Browsing

Install the documentation dependencies, then serve the same bounded site that
is published through GitHub Pages:

```bash
python3 -m pip install -r requirements-docs.txt
./scripts/docs-site.py serve
```

The site includes:

- every regular file under `docs/`, recursively, except `docs/plans/`
- standalone assets such as generated HTML demos under `docs/demo/`
- the generated interactive repository blueprint graph

`docs/plans/` is the explicit private documentation subtree. Links to files
there, and to repository source code outside `docs/`, open the corresponding
GitHub page.

To build without starting the local server:

```bash
./scripts/docs-site.py build
```

Both commands write only under the ignored `_build/docs-site/` tree, apart from
the existing generated-Markdown refresh performed by
`scripts/generate-doc-artifacts.py`.

## Validators

Documentation conformance is enforced by repo validators under [validators/](../../validators/), not by prose-shape pytest tests.

The key validators are:

- [validators/readme_user_contract.py](../../validators/readme_user_contract.py)
- [validators/domain_docs_cover_blueprints.py](../../validators/domain_docs_cover_blueprints.py)
- [validators/contributor_docs_contract.py](../../validators/contributor_docs_contract.py)
- [validators/generated_skill_docs.py](../../validators/generated_skill_docs.py)

Run them through:

```bash
python3 repo_checks.py --suite validators
```

## Adding a New Doc Contract

When you add a new documentation contract:

1. Add or update the shared logic in `docs_tooling/`.
2. Add or update the hand-written doc that owns the prose.
3. Add marker blocks if the doc needs generated coverage content.
4. Add or update the validator module under [validators/](../../validators/).
5. Regenerate docs and run validators.

This keeps the rules in one place and avoids scattering doc-generation logic across unrelated scripts.

For the local pre-commit order, GitHub Actions behavior, and Python test-suite
boundaries, see [docs/testing.md](../testing.md).
