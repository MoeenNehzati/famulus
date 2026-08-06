# User Documentation Validator Fixtures Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute `repo/user_docs_cover_blueprints` as four pytest items that share one validated skill catalog instead of constructing the catalog five times.

**Architecture:** Extend the validator collector to opt into ordinary pytest module collection when a validator defines `test_*` functions, while preserving the existing single-`validate()` fallback. Refactor documentation rendering to accept a prepared catalog, then make the user-doc validator provide that catalog through one module-scoped fixture and preserve its direct-call wrapper.

**Tech Stack:** Python 3.11+, pytest fixtures and collection hooks, PyYAML, existing `docs_tooling` APIs, root `repo_checks.py` entry point.

## Global Constraints

- Preserve all existing validator conformance rules and diagnostic text.
- Preserve staged-mirror isolation; fixtures must receive the captured repository view.
- Existing single-`validate()` validator modules require no changes.
- Multiple pytest items aggregate findings under one canonical validator ID.
- `validate(repo_root)` remains available for direct callers.
- Measure the selected validator through `repo_checks.py`; baseline wall time is 9.78 seconds.

---

### Task 1: Collect fixture-backed validator items

**Files:**
- Modify: `src/officina/repository_checks.py:71-179,451-478,575-640`
- Test: `tests/test_repository_validator_checks.py:98-120`

**Interfaces:**
- Consumes: `_ValidatorModule`, `ValidatorPytestPlugin.repo_root`, and the existing finding-list protocol.
- Produces: module-local `test_*` collection with ordinary pytest fixture semantics; legacy `validate` fallback; aggregated `dict[str, list[str]]` findings.

- [ ] **Step 1: Write a failing collector test**

Add a temporary validator defining one module-scoped fixture and two test items. The fixture records one preparation, while both items return distinct findings:

```python
def test_run_all_reuses_module_fixture_and_aggregates_validator_items(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    validators = _initialize_runner_repository(repo)
    evidence = tmp_path / "fixture-calls"
    (validators / "multi_item.py").write_text(
        "from pathlib import Path\n"
        "import pytest\n"
        f"EVIDENCE = Path({str(evidence)!r})\n"
        "@pytest.fixture(scope='module')\n"
        "def prepared(repo_root):\n"
        "    EVIDENCE.write_text(EVIDENCE.read_text() + 'x' if EVIDENCE.exists() else 'x')\n"
        "    return 'prepared' if repo_root.is_dir() else 'missing'\n"
        "def test_first(prepared): return [f'first:{prepared}']\n"
        "def test_second(prepared): return [f'second:{prepared}']\n"
        "def validate(repo_root): return ['legacy fallback ran']\n",
        encoding="utf-8",
    )
    _require_git_ok(GitTestRepository(repo).git("add", "."))

    assert _RUNNER.run_all(repo, validator_ids=["repo/multi_item"]) == {
        "repo/multi_item": ["first:prepared", "second:prepared"]
    }
    assert evidence.read_text(encoding="utf-8") == "x"
```

The production mutation this catches is falling back to `validate()`, failing
to register module fixtures, executing module setup more than once, or
overwriting one item's findings with another's.

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
PYTHONPATH=src pytest -q tests/test_repository_validator_checks.py::test_run_all_reuses_module_fixture_and_aggregates_validator_items
```

Expected: FAIL because `_ValidatorModule.collect()` currently creates only the
single `validate()` item.

- [ ] **Step 3: Implement normal module collection with a legacy fallback**

In `_ValidatorModule.collect()`, inspect the already loaded module for callable
names beginning with `test_`. When none exist, retain the current singleton
`validate()` construction. Otherwise call `super().collect()`, retain only
`pytest.Function` items, and attach the canonical metadata to every item:

```python
test_names = tuple(
    name
    for name, value in vars(self.obj).items()
    if name.startswith("test_") and callable(value)
)
if not test_names:
    return [self._legacy_item()]

items = [item for item in super().collect() if isinstance(item, pytest.Function)]
for item in items:
    item.add_marker("validator")
    item._validator_id = self.validator_id
    item._validator_entry_name = item.originalname or item.name
return items
```

Extract the existing singleton construction into `_legacy_item()` so its name,
callable, marker, and entry metadata remain unchanged. Change `repo_root` to
`@pytest.fixture(scope="session")`. In `pytest_pyfunc_call`, aggregate rather
than replace findings:

```python
if errors:
    self.results.setdefault(validator_id, []).extend(errors)
    pytest.fail("\n".join(errors), pytrace=False)
```

- [ ] **Step 4: Run focused collector tests and verify GREEN**

Run:

```bash
PYTHONPATH=src pytest -q tests/test_repository_validator_checks.py
```

Expected: all tests pass, including the legacy fixture test and the new
multi-item test.

- [ ] **Step 5: Commit the independently passing collector change**

After explicit commit authorization:

```bash
git add src/officina/repository_checks.py tests/test_repository_validator_checks.py
git commit -m "feat: collect fixture-backed validator items"
```

---

### Task 2: Reuse one catalog throughout document rendering

**Files:**
- Modify: `docs_tooling/render.py:9-57`
- Test: `tests/test_docs_catalog.py`

**Interfaces:**
- Consumes: `docs_tooling.catalog.SkillInfo`, `load_catalog(repo_root)`, and `skills_by_domain(catalog)`.
- Produces: `render_coverage_block(repo_root, domain, *, catalog=None) -> str` and `render_doc_with_updated_blocks(repo_root, rel_path, *, catalog=None) -> str`.

- [ ] **Step 1: Write a failing rendering reuse test**

Use the existing `_write_skill` helper to prepare one real catalog. Replace
`docs_tooling.render.load_catalog` with a function that raises, then render a
document using the prepared catalog:

```python
def test_render_document_uses_supplied_catalog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_skill(
        tmp_path,
        "proof-audit",
        domain="research",
        topics=["mathematical-reasoning"],
        visibility="featured",
    )
    catalog = load_catalog(tmp_path)
    doc_path = tmp_path / "docs" / "user" / "research.md"
    doc_path.parent.mkdir(parents=True)
    doc_path.write_text(
        "<!-- BEGIN AUTO-GENERATED DOCS: research -->\n"
        "stale\n"
        "<!-- END AUTO-GENERATED DOCS: research -->\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "docs_tooling.render.load_catalog",
        lambda _repo_root: pytest.fail("catalog was reconstructed"),
    )

    rendered = render_doc_with_updated_blocks(
        tmp_path,
        Path("docs/user/research.md"),
        catalog=catalog,
    )

    assert "Generated from live blueprints" in rendered
```

Add `import pytest` and import `render_doc_with_updated_blocks` beside the
existing render import. The production mutation this catches is any nested call
to `load_catalog()` after a caller supplies prepared catalog data.

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
PYTHONPATH=src pytest -q tests/test_docs_catalog.py::test_render_document_uses_supplied_catalog
```

Expected: FAIL because `render_doc_with_updated_blocks` does not accept the
`catalog` keyword.

- [ ] **Step 3: Add optional prepared-catalog parameters**

Import `SkillInfo`, then implement one load at the top-level boundary:

```python
def render_coverage_block(
    repo_root: Path,
    domain: str,
    *,
    catalog: list[SkillInfo] | None = None,
) -> str:
    grouped = skills_by_domain(
        load_catalog(repo_root) if catalog is None else catalog
    )
    skills = grouped.get(domain, [])
    lines = [
        begin_marker(domain),
        "> Generated from live blueprints. Do not edit this block by hand.",
        "",
    ]
    if skills:
        for skill in skills:
            lines.append(f"- `{skill.name}` — {skill.summary}")
    else:
        lines.append("- No skills currently map to this domain.")
    lines.append(end_marker(domain))
    return "\n".join(lines)


def render_doc_with_updated_blocks(
    repo_root: Path,
    rel_path: Path,
    *,
    catalog: list[SkillInfo] | None = None,
) -> str:
    prepared = load_catalog(repo_root) if catalog is None else catalog
    path = repo_root / rel_path
    text = path.read_text(encoding="utf-8")
    for block in COVERAGE_BLOCKS:
        if block.doc_path != rel_path:
            continue
        text = _replace_block(
            text,
            block.marker_id,
            render_coverage_block(repo_root, block.domain, catalog=prepared),
            rel_path,
        )
    return text
```

Do not add module-global caching.

- [ ] **Step 4: Run documentation rendering tests and verify GREEN**

Run:

```bash
PYTHONPATH=src pytest -q tests/test_docs_catalog.py tests/validate_documentation_validators.py
```

Expected: all tests pass with unchanged rendered text.

- [ ] **Step 5: Commit the independently passing rendering boundary**

After explicit commit authorization:

```bash
git add docs_tooling/render.py tests/test_docs_catalog.py
git commit -m "perf: reuse prepared documentation catalogs"
```

---

### Task 3: Split the user-doc validator over one module fixture

**Files:**
- Modify: `validators/user_docs_cover_blueprints.py`
- Modify: `tests/validate_documentation_validators.py`
- Test: `tests/test_repository_validator_checks.py`

**Interfaces:**
- Consumes: fixture-aware collection from Task 1 and `render_doc_with_updated_blocks(repo_root, rel_path, catalog=catalog)` from Task 2.
- Produces: `skill_catalog(repo_root) -> list[SkillInfo]`, `test_domain_coverage(repo_root, skill_catalog) -> list[str]`, parametrized `test_user_document(repo_root, skill_catalog, rel_path) -> list[str]`, and compatible `validate(repo_root) -> list[str]`.

- [ ] **Step 1: Write failing direct-path catalog-count coverage**

Add a test that creates the existing clean documentation repository, wraps the
validator module's real loader, and asserts one load while preserving success:

```python
def test_user_docs_validator_constructs_catalog_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = _make_repo(tmp_path)
    calls = 0
    real_load_catalog = user_docs_validator.load_catalog

    def counted_load_catalog(root: Path):
        nonlocal calls
        calls += 1
        return real_load_catalog(root)

    monkeypatch.setattr(user_docs_validator, "load_catalog", counted_load_catalog)
    monkeypatch.setattr(docs_render, "load_catalog", counted_load_catalog)

    assert user_docs_validator.validate(repo_root) == []
    assert calls == 1
```

Change the test module import from a function alias to
`from validators import user_docs_cover_blueprints as user_docs_validator` so
the test can patch the validator boundary, and add
`from docs_tooling import render as docs_render` to expose the nested renderer
boundary. The production mutation this catches is reconstructing the catalog in
the compatibility path or failing to pass it through to nested renders.

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
PYTHONPATH=src pytest -q tests/validate_documentation_validators.py::test_user_docs_validator_constructs_catalog_once
```

Expected: FAIL with `calls == 5`, proving the current repetition.

- [ ] **Step 3: Extract pure checks and add the fixture-backed items**

Implement these boundaries in the validator:

```python
@pytest.fixture(scope="module")
def skill_catalog(repo_root: Path) -> list[SkillInfo]:
    return load_catalog(repo_root)


def _validate_domain_coverage(
    repo_root: Path,
    catalog: list[SkillInfo],
) -> list[str]:
    if not catalog and not (repo_root / "docs").exists():
        return []
    covered_domains = {
        block.domain for block in COVERAGE_BLOCKS if block.doc_path in USER_DOCS
    }
    contributor_domains = {
        block.domain for block in COVERAGE_BLOCKS if block.doc_path not in USER_DOCS
    }
    live_domains = {
        skill.domain for skill in catalog if skill.domain not in contributor_domains
    }
    missing_domains = sorted(live_domains - covered_domains)
    if not missing_domains:
        return []
    return [
        "docs/user: missing coverage mapping for domains "
        + ", ".join(missing_domains)
    ]


def _validate_user_document(
    repo_root: Path,
    catalog: list[SkillInfo],
    rel_path: Path,
) -> list[str]:
    if not catalog and not (repo_root / "docs").exists():
        return []
    path = repo_root / rel_path
    if not path.is_file():
        return [f"{rel_path}: missing"]
    try:
        rendered = render_doc_with_updated_blocks(
            repo_root,
            rel_path,
            catalog=catalog,
        )
    except ValueError as exc:
        return [str(exc)]
    actual = path.read_text(encoding="utf-8")
    if actual == rendered:
        return []
    return [
        f"{rel_path}: generated coverage blocks are stale; "
        "run python3 scripts/generate-doc-artifacts.py"
    ]


def test_domain_coverage(
    repo_root: Path,
    skill_catalog: list[SkillInfo],
) -> list[str]:
    return _validate_domain_coverage(repo_root, skill_catalog)


@pytest.mark.parametrize("rel_path", USER_DOCS, ids=lambda path: path.as_posix())
def test_user_document(
    repo_root: Path,
    skill_catalog: list[SkillInfo],
    rel_path: Path,
) -> list[str]:
    return _validate_user_document(repo_root, skill_catalog, rel_path)


def validate(repo_root: Path) -> list[str]:
    catalog = load_catalog(repo_root)
    errors = _validate_domain_coverage(repo_root, catalog)
    for rel_path in USER_DOCS:
        errors.extend(_validate_user_document(repo_root, catalog, rel_path))
    return errors
```

Preserve the existing missing-file, missing-domain, stale-block, and rendering
error strings verbatim.

- [ ] **Step 4: Run focused behavior and collector integration tests**

Run:

```bash
PYTHONPATH=src pytest -q tests/validate_documentation_validators.py tests/test_repository_validator_checks.py
```

Expected: all tests pass. The temporary multi-item validator proves fixture
reuse and aggregation; the documentation tests prove direct compatibility and
unchanged conformance behavior.

- [ ] **Step 5: Stage only owned implementation paths and benchmark the real staged validator**

Run:

```bash
git add docs_tooling/render.py src/officina/repository_checks.py validators/user_docs_cover_blueprints.py tests/test_docs_catalog.py tests/test_repository_validator_checks.py tests/validate_documentation_validators.py docs/superpowers/specs/2026-08-05-user-doc-validator-fixtures-design.md docs/superpowers/plans/2026-08-05-user-doc-validator-fixtures.md
/usr/bin/time -f 'wall=%e cpu_user=%U cpu_system=%S' python3 repo_checks.py --suite validators --validator repo/user_docs_cover_blueprints
```

Expected: validator succeeds, reports four pytest items, constructs one catalog,
and improves materially from the 9.78-second baseline.

- [ ] **Step 6: Run regression verification**

Run:

```bash
PYTHONPATH=src pytest -q tests/test_docs_catalog.py tests/validate_documentation_validators.py tests/test_repository_validator_checks.py tests/test_repo_checks_entrypoint.py
python3 repo_checks.py --suite validators
git diff --cached --check
```

Expected: all focused tests and all validators pass, with no whitespace errors.

- [ ] **Step 7: Commit the completed validator refactor**

After explicit commit authorization:

```bash
git add docs_tooling/render.py src/officina/repository_checks.py validators/user_docs_cover_blueprints.py tests/test_docs_catalog.py tests/test_repository_validator_checks.py tests/validate_documentation_validators.py docs/superpowers/specs/2026-08-05-user-doc-validator-fixtures-design.md docs/superpowers/plans/2026-08-05-user-doc-validator-fixtures.md
git commit -m "perf: reuse user documentation validator catalog"
```
