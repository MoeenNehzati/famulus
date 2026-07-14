# List-manager category cache Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a cloud-backed category-path cache so list-manager can discover valid categories without rendering full lists on every entry creation.

**Architecture:** A dedicated `cloud-list-categories` machine interface owns an untracked per-list YAML cache under `skills/list-manager/tmp/`. It reads the cache until a 20-use countdown expires, then downloads the cloud list through the existing declared cloud-files boundary, extracts category paths, and atomically replaces the cache. The LLM workflow uses this interface for category discovery and refreshes once after a stale-category create failure before asking the user to choose again.

**Tech Stack:** Python stdlib, PyYAML, existing `officina.runtime` machine-interface runner and cloud-files dispatcher boundary, pytest.

## Global Constraints

- Cache only category paths and countdown metadata; never cache entries, descriptions, or ids.
- Cache files live at `skills/list-manager/tmp/categories.<name>.yaml`, are untracked, and are disposable.
- A normal lookup decrements `remaining_uses`; zero triggers refresh; `--refresh` resets it to 20.
- Never infer a replacement category or silently retry a failed write.
- Preserve unrelated typed-blueprint work already present in the repository.

---

### Task 1: Category-cache machine interface

**Files:**
- Create: `skills/list-manager/_rtx/_category_cache.py`
- Modify: `skills/list-manager/blueprint.yaml`
- Modify: `.gitignore`
- Test: `skills/list-manager/tests/test_category_cache.py`

**Interfaces:**
- Consumes: `cloud-files.machine.lists-read@1` through a declared `DispatchCall`.
- Produces: `list-manager.machine.cloud-list-categories@1`, accepting `<name> --cloud [--refresh]` and printing category paths as YAML.

- [ ] **Step 1: Write failing cache tests**

```python
def test_first_lookup_downloads_paths_and_leaves_nineteen_uses(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(category_cache, "download_list", fake_download(calls, TODO_YAML))
    assert category_cache.load_paths("todo", cache_dir=tmp_path) == ["Dev", "Dev/Tasks"]
    assert calls == ["todo"]
    assert yaml.safe_load((tmp_path / "categories.todo.yaml").read_text())["remaining_uses"] == 19

def test_cached_lookup_decrements_without_downloading(tmp_path, monkeypatch):
    category_cache.write_cache(tmp_path / "categories.todo.yaml", "todo", ["Dev"], 2)
    monkeypatch.setattr(category_cache, "download_list", pytest.fail)
    assert category_cache.load_paths("todo", cache_dir=tmp_path) == ["Dev"]
    assert yaml.safe_load((tmp_path / "categories.todo.yaml").read_text())["remaining_uses"] == 1

def test_zero_countdown_and_refresh_flag_download_and_reset(tmp_path, monkeypatch):
    category_cache.write_cache(tmp_path / "categories.todo.yaml", "todo", ["Old"], 0)
    calls = []
    monkeypatch.setattr(category_cache, "download_list", fake_download(calls, TODO_YAML))
    assert category_cache.load_paths("todo", cache_dir=tmp_path) == ["Dev", "Dev/Tasks"]
    assert calls == ["todo"]
    assert yaml.safe_load((tmp_path / "categories.todo.yaml").read_text())["remaining_uses"] == 19
```

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `pytest skills/list-manager/tests/test_category_cache.py -q`

Expected: FAIL because `_category_cache` does not exist.

- [ ] **Step 3: Implement the smallest cache owner**

```python
DEFAULT_REMAINING_USES = 20

def load_paths(name: str, *, refresh: bool = False, cache_dir: Path = CACHE_DIR) -> list[str]:
    path = cache_dir / f"categories.{name}.yaml"
    cached = read_cache(path)
    if refresh or cached is None or cached["remaining_uses"] <= 0:
        paths = fetch_category_paths(name)
        write_cache(path, name, paths, DEFAULT_REMAINING_USES - 1)
        return paths
    write_cache(path, name, cached["paths"], cached["remaining_uses"] - 1)
    return cached["paths"]
```

Implement `fetch_category_paths` by downloading `lists/<name>.yaml` through the existing list-manager cloud transport boundary, loading YAML, and recursively extracting category names into slash-separated paths. Define a `PythonArgvMachineInterface` that accepts `name`, requires `--cloud`, accepts optional `--refresh`, and emits `{name, paths, remaining_uses}` as YAML. Make writes atomic with a temporary file in the cache directory followed by `replace`.

Add `cloud-list-categories` to the list-manager blueprint with the exact caller, argument, cloud-files dependency, cache ownership, and direct IO declarations. Add `skills/list-manager/tmp/` to `.gitignore`.

- [ ] **Step 4: Run focused tests to verify the implementation**

Run: `pytest skills/list-manager/tests/test_category_cache.py skills/list-manager/tests/test_python_machine_interfaces.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the focused code change**

Do not commit unless the user explicitly approves the staged diff. If approved, stage only the task files and use: `git commit -m "feat(list-manager): cache cloud category paths"`.

### Task 2: LLM category-discovery and stale-category policy

**Files:**
- Modify: `skills/list-manager/SKILL.md`
- Modify: `skills/list-manager/blueprint.yaml`
- Test: `skills/list-manager/tests/test_category_cache.py`

**Interfaces:**
- Consumes: `list-manager.machine.cloud-list-categories@1`.
- Produces: an LLM workflow that uses cached paths to ask for a category and refreshes once after a missing-category create error.

- [ ] **Step 1: Write failing documentation-contract tests**

```python
def test_skill_requires_category_lookup_instead_of_full_list_read():
    body = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    assert "cloud-list-categories" in body
    assert "refresh" in body.lower()
    assert "Do not infer a replacement category" in body
```

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `pytest skills/list-manager/tests/test_category_cache.py::test_skill_requires_category_lookup_instead_of_full_list_read -q`

Expected: FAIL because the workflow still instructs a full rendered-list read for unknown categories.

- [ ] **Step 3: Update the hand-authored workflow and generated contract**

Replace the unknown-category rule with: obtain category paths through `cloud-list-categories`; if more than one path fits, offer short concrete choices; do not guess. Add the stale-category rule: refresh the category cache once and ask the user to match a current path; do not retry a mutation with a substituted path. Keep the existing required-field and ambiguous-value rules intact.

Refresh generated blueprint content with `skill-maker.machine.sync-blueprints`, rather than editing generated blocks by hand.

- [ ] **Step 4: Run workflow and blueprint checks**

Run: `pytest skills/list-manager/tests/test_category_cache.py -q`

Run: `dispatcher --caller-skill skill-maker skill-maker.machine.sync-blueprints --check`

Expected: both commands exit 0.

- [ ] **Step 5: Commit the workflow change**

Do not commit unless the user explicitly approves the staged diff. If approved, stage only the task files and use: `git commit -m "docs(list-manager): guide category cache recovery"`.

### Task 3: End-to-end verification

**Files:**
- Modify only if verification exposes a defect in Task 1 or Task 2.

**Interfaces:**
- Consumes: the completed `cloud-list-categories` interface and list-manager LLM workflow.
- Produces: evidence that the interface is dispatcher-resolvable, cache semantics work, and existing list-manager behavior remains intact.

- [ ] **Step 1: Verify dispatcher resolution without cloud side effects**

Run: `dispatcher --dry-run --caller-skill list-manager list-manager.machine.cloud-list-categories todo --cloud`

Expected: successful resolution to the new list-manager interface.

- [ ] **Step 2: Run the complete list-manager test suite**

Run: `pytest skills/list-manager/tests -q`

Expected: PASS.

- [ ] **Step 3: Run repository validation relevant to skill contracts**

Run: `python3 validators/runner.py`

Expected: exit 0, unless pre-existing unrelated typed-blueprint work causes a documented failure; in that case, report the exact failure without changing unrelated files.

- [ ] **Step 4: Review the final diff and cache exclusion**

Run: `git diff -- .gitignore skills/list-manager docs/superpowers`

Expected: only the category-cache implementation, contract, tests, ignore rule, and this design/plan documentation appear; no file under `skills/list-manager/tmp/` appears as tracked.
