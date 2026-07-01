# Hooks / Tests / Validators Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate the ad-hoc tangle of bash hooks, misnamed test-validators, and scattered tools by giving every artifact a well-typed home — validators in `validators/` or `skills/my-writing-skills/validators/`, behavior tests in `skills/*/tests/`, utilities in `scripts/` — and wiring it all through a single Python runner called from a simplified pre-commit hook.

**Architecture:** Validators expose `validate(repo_root: Path) -> list[str]` and live in two packages: `validators/` (global, platform-neutral checks) and `skills/my-writing-skills/validators/` (skill-system checks). A runner at `validators/runner.py` discovers both packages and runs them all. Eight bash check-scripts in `.githooks/skill/` and the `tools/` directory are deleted; their logic moves into the validator modules above. Behavior tests (blueprint tooling, install flows, recurring-tasks) move into the skill they test. The pre-commit hook shrinks to a detached-HEAD guard plus one runner call.

**Tech Stack:** Python 3, pytest, PyYAML, bash (pre-commit entry-point only)

---

## Current State

```
.githooks/
  pre-commit                              # detached-HEAD guard + calls all skill/check-* hooks
  git/check-not-detached                  # detached-HEAD guard (duplicated in pre-commit)
  skill/check-blueprint-tooling           # bash → runs tests/test_skill_blueprint_tools.py
  skill/check-blueprints                  # bash → runs tools/check_skill_blueprints.py
  skill/check-boundaries                  # bash → runs tools/check_skill_boundaries.py
  skill/check-dependencies                # bash (150-line awk-heavy validator)
  skill/check-install                     # bash → runs tests/test_*_install.py if CLI present
  skill/check-metadata                    # bash → runs tests/test_skill_metadata.py
  skill/check-names                       # bash (inline validator)
  skill/check-platform-neutral            # bash → runs tests/test_platform_neutral_content.py
tools/
  check_skill_blueprints.py               # validator with main()
  check_skill_boundaries.py              # validator with main()
  validate_blueprint_relationships.py     # validator with main()
  sync_skill_blueprints.py               # utility called by check_skill_blueprints
  invoke_skill_export.py                  # utility (cross-skill script dispatcher)
  migrate_all_skills_to_blueprints.py    # one-off scaffold utility
tests/
  test_platform_neutral_content.py        # validator disguised as test
  test_skill_metadata.py                  # validator disguised as test
  test_skill_blueprint_tools.py           # behavior test for blueprint tooling
  test_claude_install.py                  # install integration test
  test_codex_install.py                   # install integration test
  install_test_utils.py                   # helpers for install tests
skills/
  recurring-tasks/scripts/test_enable_disable.py   # test in wrong directory
  recurring-tasks/scripts/test_sync_units.py       # test in wrong directory
  my-writing-skills/                       # SKILL.md exists; validators/ does not yet
```

---

## File Map

### New directories to create
| Path | Purpose |
|---|---|
| `validators/` | Global repo validators (platform-neutral, discoverable by runner) |
| `skills/my-writing-skills/validators/` | Skill-system validators (blueprint, names, metadata, boundaries, dependencies, relationships) |
| `skills/my-writing-skills/scripts/` | Skill utilities moved from `tools/` |
| `skills/recurring-tasks/tests/` | Correct home for recurring-tasks behavior tests |

### Validators (new interface: `validate(repo_root: Path) -> list[str]`)
| From | To | Change |
|---|---|---|
| `tests/test_platform_neutral_content.py` | `validators/platform_neutral.py` | Extract `validate()`, keep `main()` |
| `tests/test_skill_metadata.py` | `skills/my-writing-skills/validators/skill_metadata.py` | Extract `validate()`, keep `main()` |
| `tools/check_skill_blueprints.py` | `skills/my-writing-skills/validators/blueprints.py` | Extract `validate()`, fix subprocess path |
| `tools/check_skill_boundaries.py` | `skills/my-writing-skills/validators/boundaries.py` | Extract `validate()` |
| `tools/validate_blueprint_relationships.py` | `skills/my-writing-skills/validators/blueprint_relationships.py` | Add `validate()` wrapper |
| `.githooks/skill/check-names` (bash) | `skills/my-writing-skills/validators/names.py` | Rewrite in Python |
| `.githooks/skill/check-dependencies` (bash) | `skills/my-writing-skills/validators/dependencies.py` | Rewrite in Python |

### Runner (new)
| Path | Purpose |
|---|---|
| `validators/__init__.py` | Package marker |
| `validators/runner.py` | Discovers both validator packages, runs all, exits 0/1 |
| `skills/my-writing-skills/validators/__init__.py` | Package marker |

### Behavior tests (move, no logic change)
| From | To |
|---|---|
| `tests/test_skill_blueprint_tools.py` | `skills/my-writing-skills/tests/test_blueprint_tools.py` |
| `tests/test_claude_install.py` | `skills/install-assistant-tools/tests/test_claude_install.py` |
| `tests/test_codex_install.py` | `skills/install-assistant-tools/tests/test_codex_install.py` |
| `tests/install_test_utils.py` | `skills/install-assistant-tools/tests/install_test_utils.py` |
| `skills/recurring-tasks/scripts/test_enable_disable.py` | `skills/recurring-tasks/tests/test_enable_disable.py` |
| `skills/recurring-tasks/scripts/test_sync_units.py` | `skills/recurring-tasks/tests/test_sync_units.py` |

### Utilities (move out of tools/)
| From | To |
|---|---|
| `tools/sync_skill_blueprints.py` | `skills/my-writing-skills/scripts/sync_skill_blueprints.py` |
| `tools/invoke_skill_export.py` | `scripts/invoke_skill_export.py` |
| `tools/migrate_all_skills_to_blueprints.py` | `scripts/migrate_all_skills_to_blueprints.py` |

### Deleted entirely
| Path | Reason |
|---|---|
| `.githooks/skill/check-platform-neutral` | Replaced by `validators/platform_neutral.py` |
| `.githooks/skill/check-metadata` | Replaced by `skills/my-writing-skills/validators/skill_metadata.py` |
| `.githooks/skill/check-blueprints` | Replaced by `skills/my-writing-skills/validators/blueprints.py` |
| `.githooks/skill/check-boundaries` | Replaced by `skills/my-writing-skills/validators/boundaries.py` |
| `.githooks/skill/check-blueprint-tooling` | Test moved; now discovered by pytest |
| `.githooks/skill/check-names` | Replaced by `skills/my-writing-skills/validators/names.py` |
| `.githooks/skill/check-dependencies` | Replaced by `skills/my-writing-skills/validators/dependencies.py` |
| `.githooks/skill/check-install` | Install tests now run by CI only, not pre-commit |
| `.githooks/git/check-not-detached` | Logic already inlined in `pre-commit` |
| `tests/test_platform_neutral_content.py` | Logic moved to validator |
| `tests/test_skill_metadata.py` | Logic moved to validator |
| `tools/` (entire directory) | All contents distributed above |

### Modified
| Path | Change |
|---|---|
| `.githooks/pre-commit` | Strip all skill/check-* calls; call runner only |

---

## Task 1: Create `validators/` package and `platform_neutral.py`

**Files:**
- Create: `validators/__init__.py`
- Create: `validators/platform_neutral.py`
- Create: `tests/validate_platform_neutral.py`
- Delete: `tests/test_platform_neutral_content.py`

- [ ] **Step 1: Write the failing validator tests**

Create `tests/validate_platform_neutral.py`:

```python
"""Tests for validators/platform_neutral.py."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from validators.platform_neutral import validate  # noqa: E402


def test_empty_repo_passes(tmp_path: Path) -> None:
    assert validate(tmp_path) == []


def test_clean_skill_passes(tmp_path: Path) -> None:
    d = tmp_path / "skills" / "my-skill"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: my-skill\n---\nHello world.\n")
    assert validate(tmp_path) == []


def test_platform_reference_in_skill_detected(tmp_path: Path) -> None:
    d = tmp_path / "skills" / "my-skill"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: my-skill\n---\nUse Claude for this.\n")
    errors = validate(tmp_path)
    assert len(errors) == 1
    assert "Claude" in errors[0]


def test_excluded_install_path_skipped(tmp_path: Path) -> None:
    d = tmp_path / "skills" / "install-assistant-tools"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("Install Claude Code here.\n")
    assert validate(tmp_path) == []


def test_tests_subdir_skipped(tmp_path: Path) -> None:
    d = tmp_path / "skills" / "my-skill" / "tests"
    d.mkdir(parents=True)
    (d / "test_something.py").write_text("# test for claude or codex\n")
    assert validate(tmp_path) == []


def test_references_dir_scanned(tmp_path: Path) -> None:
    refs = tmp_path / "references"
    refs.mkdir()
    (refs / "guide.md").write_text("Use Claude Code to run this.\n")
    errors = validate(tmp_path)
    assert any("Claude" in e for e in errors)


def test_multiple_violations_all_reported(tmp_path: Path) -> None:
    d = tmp_path / "skills" / "a-skill"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("Use Claude.\nAlso codex.\n")
    errors = validate(tmp_path)
    assert len(errors) == 2
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd ~/Documents/AI && python3 -m pytest tests/validate_platform_neutral.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError` — `validators.platform_neutral` doesn't exist yet.

- [ ] **Step 3: Create `validators/platform_neutral.py`**

```bash
touch ~/Documents/AI/validators/__init__.py
```

Create `validators/platform_neutral.py`:

```python
"""Validate that shared content contains no platform-specific references."""
from __future__ import annotations

import re
import sys
from pathlib import Path

FORBIDDEN = re.compile(r"(\.claude|\.codex|Claude|Codex|claude|codex)")

_CHECK_ROOTS = ["skills", "references", "agents", "CLAUDE.md"]
_EXCLUDED_PARTS = {"tests", ".git", ".claude-plugin", ".codex-plugin"}
_EXCLUDED_PATHS = {Path("skills/install-assistant-tools")}


def _iter_files(repo_root: Path):
    for root_name in _CHECK_ROOTS:
        root = repo_root / root_name
        if root.is_file():
            yield root
            continue
        if not root.is_dir():
            continue
        for child in root.rglob("*"):
            if not child.is_file():
                continue
            rel_parts = child.relative_to(repo_root).parts
            if any(part in _EXCLUDED_PARTS for part in rel_parts):
                continue
            rel_path = child.relative_to(repo_root)
            if any(rel_path == ep or ep in rel_path.parents for ep in _EXCLUDED_PATHS):
                continue
            yield child


def validate(repo_root: Path) -> list[str]:
    """Return error strings for every platform-specific reference found in shared content."""
    errors: list[str] = []
    for path in _iter_files(repo_root):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if FORBIDDEN.search(line):
                rel = path.relative_to(repo_root)
                errors.append(f"{rel}:{lineno}: {line.strip()}")
    return errors


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    errors = validate(repo_root)
    if errors:
        print("Platform-specific references found in shared content:")
        for error in errors:
            print(f"- {error}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
cd ~/Documents/AI && python3 -m pytest tests/validate_platform_neutral.py -v
```

Expected: all 7 tests PASS.

- [ ] **Step 5: Delete old file**

```bash
git -C ~/Documents/AI rm tests/test_platform_neutral_content.py
```

- [ ] **Step 6: Commit**

```bash
git -C ~/Documents/AI add validators/__init__.py validators/platform_neutral.py tests/validate_platform_neutral.py
git -C ~/Documents/AI commit -m "feat: extract platform_neutral validator; add tests"
```

---

## Task 2: Create `skills/my-writing-skills/validators/` and `skill_metadata.py`

**Files:**
- Create: `skills/my-writing-skills/validators/__init__.py`
- Create: `skills/my-writing-skills/validators/skill_metadata.py`
- Create: `tests/validate_skill_metadata.py`
- Delete: `tests/test_skill_metadata.py`

- [ ] **Step 1: Write the failing validator tests**

Create `tests/validate_skill_metadata.py`:

```python
"""Tests for skills/my-writing-skills/validators/skill_metadata.py."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_VALIDATOR = Path(__file__).resolve().parents[1] / "skills" / "my-writing-skills" / "validators" / "skill_metadata.py"
_spec = importlib.util.spec_from_file_location("skill_metadata", _VALIDATOR)
_mod = importlib.util.module_from_spec(_spec)

def setup_module(_):
    _spec.loader.exec_module(_mod)

validate = None

def setup_function(_):
    global validate
    validate = _mod.validate

MAX_LEN = 1024


def _make_skill(skills_dir: Path, name: str, content: str) -> None:
    d = skills_dir / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(content)


def test_no_skills_passes(tmp_path: Path) -> None:
    (tmp_path / "skills").mkdir()
    assert _mod.validate(tmp_path) == []


def test_valid_skill_passes(tmp_path: Path) -> None:
    _make_skill(
        tmp_path / "skills", "my-skill",
        "---\nname: my-skill\ndescription: A short description.\n---\nBody.\n",
    )
    assert _mod.validate(tmp_path) == []


def test_missing_frontmatter_flagged(tmp_path: Path) -> None:
    _make_skill(tmp_path / "skills", "my-skill", "No frontmatter here.\n")
    errors = _mod.validate(tmp_path)
    assert any("missing YAML frontmatter" in e for e in errors)


def test_missing_description_flagged(tmp_path: Path) -> None:
    _make_skill(tmp_path / "skills", "my-skill", "---\nname: my-skill\n---\nBody.\n")
    errors = _mod.validate(tmp_path)
    assert any("missing description" in e for e in errors)


def test_long_description_flagged(tmp_path: Path) -> None:
    long_desc = "x" * (MAX_LEN + 1)
    _make_skill(
        tmp_path / "skills", "my-skill",
        f"---\nname: my-skill\ndescription: {long_desc}\n---\nBody.\n",
    )
    errors = _mod.validate(tmp_path)
    assert any(f"{MAX_LEN + 1} characters" in e for e in errors)


def test_description_at_limit_passes(tmp_path: Path) -> None:
    exact_desc = "x" * MAX_LEN
    _make_skill(
        tmp_path / "skills", "my-skill",
        f"---\nname: my-skill\ndescription: {exact_desc}\n---\nBody.\n",
    )
    assert _mod.validate(tmp_path) == []


def test_multiple_skills_all_checked(tmp_path: Path) -> None:
    skills = tmp_path / "skills"
    _make_skill(skills, "good-skill", "---\nname: good-skill\ndescription: Fine.\n---\n")
    _make_skill(skills, "bad-skill", "No frontmatter.\n")
    errors = _mod.validate(tmp_path)
    assert len(errors) == 1
    assert "bad-skill" in errors[0]
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd ~/Documents/AI && python3 -m pytest tests/validate_skill_metadata.py -v 2>&1 | head -20
```

Expected: failure loading the module — file doesn't exist yet.

- [ ] **Step 3: Create `skills/my-writing-skills/validators/skill_metadata.py`**

```bash
touch ~/Documents/AI/skills/my-writing-skills/validators/__init__.py
```

Create `skills/my-writing-skills/validators/skill_metadata.py`:

```python
"""Validate that skills have well-formed YAML frontmatter accepted by all platforms."""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

MAX_CODEX_DESCRIPTION_LENGTH = 1024


def validate(repo_root: Path) -> list[str]:
    """Return error strings for every skill with invalid frontmatter."""
    errors: list[str] = []
    skills_dir = repo_root / "skills"
    if not skills_dir.is_dir():
        return errors

    for skill_path in sorted(skills_dir.glob("*/SKILL.md")):
        text = skill_path.read_text(encoding="utf-8")
        match = re.match(r"---\n(.*?)\n---", text, re.DOTALL)
        if not match:
            errors.append(f"{skill_path}: missing YAML frontmatter")
            continue

        metadata = yaml.safe_load(match.group(1)) or {}
        description = metadata.get("description")
        if not description:
            errors.append(f"{skill_path}: missing description")
            continue

        if len(description) > MAX_CODEX_DESCRIPTION_LENGTH:
            errors.append(
                f"{skill_path}: description is {len(description)} characters; "
                f"Codex maximum is {MAX_CODEX_DESCRIPTION_LENGTH}"
            )

    return errors


def main() -> int:
    repo_root = Path(__file__).resolve().parents[3]
    errors = validate(repo_root)
    if errors:
        print("Invalid skill metadata:")
        for error in errors:
            print(f"- {error}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
cd ~/Documents/AI && python3 -m pytest tests/validate_skill_metadata.py -v
```

Expected: all 7 tests PASS.

- [ ] **Step 5: Delete old file**

```bash
git -C ~/Documents/AI rm tests/test_skill_metadata.py
```

- [ ] **Step 6: Commit**

```bash
git -C ~/Documents/AI add skills/my-writing-skills/validators/__init__.py skills/my-writing-skills/validators/skill_metadata.py tests/validate_skill_metadata.py
git -C ~/Documents/AI commit -m "feat: extract skill_metadata validator into my-writing-skills; add tests"
```

---

## Task 3: Move `check_skill_boundaries.py` → `boundaries.py`

**Files:**
- Create: `skills/my-writing-skills/validators/boundaries.py`
- Delete: `tools/check_skill_boundaries.py`

The logic is unchanged. The refactor is: extract `main()` body into `validate(repo_root)`, update hardcoded `REPO_ROOT` to use the parameter.

- [ ] **Step 1: Write a failing smoke test**

Create `tests/validate_boundaries.py`:

```python
"""Smoke tests for skills/my-writing-skills/validators/boundaries.py."""
from __future__ import annotations

import importlib.util
from pathlib import Path

_VALIDATOR = Path(__file__).resolve().parents[1] / "skills" / "my-writing-skills" / "validators" / "boundaries.py"
_spec = importlib.util.spec_from_file_location("boundaries", _VALIDATOR)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def test_empty_skills_passes(tmp_path: Path) -> None:
    (tmp_path / "skills").mkdir()
    assert _mod.validate(tmp_path) == []


def test_direct_cross_skill_path_flagged(tmp_path: Path) -> None:
    skills = tmp_path / "skills"
    caller = skills / "caller-skill"
    target = skills / "target-skill"
    caller.mkdir(parents=True)
    target.mkdir(parents=True)
    (target / "blueprint.yaml").write_text("name: target-skill\n")
    (caller / "blueprint.yaml").write_text("name: caller-skill\n")
    script = caller / "scripts" / "run.py"
    script.parent.mkdir()
    script.write_text("import subprocess\nsubprocess.run(['python3', '../target-skill/scripts/helper.py'])\n")
    errors = _mod.validate(tmp_path)
    assert any("target-skill" in e for e in errors)


def test_same_skill_path_allowed(tmp_path: Path) -> None:
    skills = tmp_path / "skills"
    skill = skills / "my-skill"
    (skill / "scripts").mkdir(parents=True)
    (skill / "blueprint.yaml").write_text("name: my-skill\n")
    script = skill / "scripts" / "run.py"
    script.write_text("import subprocess\nsubprocess.run(['python3', './helper.py'])\n")
    assert _mod.validate(tmp_path) == []
```

- [ ] **Step 2: Run to confirm it fails**

```bash
cd ~/Documents/AI && python3 -m pytest tests/validate_boundaries.py -v 2>&1 | head -20
```

Expected: failure loading module — file doesn't exist yet.

- [ ] **Step 3: Create `skills/my-writing-skills/validators/boundaries.py`**

Copy the existing logic and refactor:

```python
"""Reject direct cross-skill script-path reach-through for blueprint skills."""
from __future__ import annotations

import re
import sys
from pathlib import Path

SCRIPT_SUFFIXES = {".py", ".sh"}


def _is_text_script(path: Path) -> bool:
    return path.is_file() and path.suffix in SCRIPT_SUFFIXES


def validate(repo_root: Path) -> list[str]:
    errors: list[str] = []
    skills_root = repo_root / "skills"
    if not skills_root.is_dir():
        return errors

    skill_names = sorted(path.name for path in skills_root.iterdir() if path.is_dir())
    blueprint_skills = sorted(skills_root.glob("*/blueprint.yaml"))

    for blueprint_path in blueprint_skills:
        skill_dir = blueprint_path.parent
        skill_name = skill_dir.name
        other_skills = [name for name in skill_names if name != skill_name]
        script_files = [path for path in skill_dir.rglob("*") if _is_text_script(path)]

        for path in script_files:
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except UnicodeDecodeError:
                continue

            for lineno, line in enumerate(lines, start=1):
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue

                for other_skill in other_skills:
                    direct_patterns = [
                        rf"(?:^|[^A-Za-z0-9_-])(?:\.\./)+{re.escape(other_skill)}/scripts/",
                        rf"(?:^|[^A-Za-z0-9_-])skills/{re.escape(other_skill)}/scripts/",
                        rf"/skills/{re.escape(other_skill)}/scripts/",
                    ]
                    if any(re.search(pattern, line) for pattern in direct_patterns):
                        rel = path.relative_to(repo_root)
                        errors.append(
                            f"{rel}:{lineno}: direct cross-skill script path to "
                            f"{other_skill} is forbidden"
                        )
                        break

                    if "skills" in line and "scripts" in line and other_skill in line:
                        if "sys.path.insert" in line:
                            rel = path.relative_to(repo_root)
                            errors.append(
                                f"{rel}:{lineno}: cross-skill sys.path insertion to "
                                f"{other_skill} is forbidden"
                            )
                            break

    if errors:
        print("error: invalid cross-skill boundary usage.", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
    return errors


def main() -> int:
    return 0 if not validate(Path(__file__).resolve().parents[3]) else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
cd ~/Documents/AI && python3 -m pytest tests/validate_boundaries.py -v
```

Expected: all 3 tests PASS.

- [ ] **Step 5: Delete old file**

```bash
git -C ~/Documents/AI rm tools/check_skill_boundaries.py
```

- [ ] **Step 6: Commit**

```bash
git -C ~/Documents/AI add skills/my-writing-skills/validators/boundaries.py tests/validate_boundaries.py
git -C ~/Documents/AI commit -m "feat: move check_skill_boundaries into my-writing-skills/validators"
```

---

## Task 4: Move `validate_blueprint_relationships.py` → `blueprint_relationships.py`

**Files:**
- Create: `skills/my-writing-skills/validators/blueprint_relationships.py`
- Delete: `tools/validate_blueprint_relationships.py`

The existing file already has `validate_relationships(blueprints) -> list[str]` and `load_blueprints()` using a global `SKILLS_ROOT`. The refactor: add a `validate(repo_root)` wrapper and thread `repo_root` into `load_blueprints`.

- [ ] **Step 1: Write a failing smoke test**

Create `tests/validate_blueprint_relationships.py`:

```python
"""Smoke tests for skills/my-writing-skills/validators/blueprint_relationships.py."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

_VALIDATOR = (
    Path(__file__).resolve().parents[1]
    / "skills" / "my-writing-skills" / "validators" / "blueprint_relationships.py"
)
_spec = importlib.util.spec_from_file_location("blueprint_relationships", _VALIDATOR)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def _write_blueprint(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data))


def test_no_blueprints_passes(tmp_path: Path) -> None:
    (tmp_path / "skills").mkdir()
    assert _mod.validate(tmp_path) == []


def test_self_dependency_flagged(tmp_path: Path) -> None:
    _write_blueprint(
        tmp_path / "skills" / "my-skill" / "blueprint.yaml",
        {"depends_on": {"my-skill": None}},
    )
    errors = _mod.validate(tmp_path)
    assert any("itself" in e for e in errors)


def test_valid_blueprint_passes(tmp_path: Path) -> None:
    _write_blueprint(
        tmp_path / "skills" / "my-skill" / "blueprint.yaml",
        {"depends_on": {}},
    )
    assert _mod.validate(tmp_path) == []
```

- [ ] **Step 2: Run to confirm it fails**

```bash
cd ~/Documents/AI && python3 -m pytest tests/validate_blueprint_relationships.py -v 2>&1 | head -20
```

Expected: failure loading module.

- [ ] **Step 3: Create `skills/my-writing-skills/validators/blueprint_relationships.py`**

Copy the existing file and add a `validate(repo_root)` entry-point that threads `repo_root` into `load_blueprints`:

```python
#!/usr/bin/env python3
"""Validate relationships between blueprints (inter-YAML constraints)."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml


class BlueprintError(Exception):
    """Raised when a blueprint relationship is invalid."""


def load_blueprints(skills_root: Path) -> dict[str, dict[str, Any]]:
    """Load all blueprint.yaml files under skills_root."""
    blueprints: dict[str, dict[str, Any]] = {}
    for path in sorted(skills_root.glob("*/blueprint.yaml")):
        skill_name = path.parent.name
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as e:
            raise BlueprintError(f"{path}: failed to parse YAML: {e}")
        if not isinstance(data, dict):
            raise BlueprintError(f"{path}: top level must be a mapping")
        blueprints[skill_name] = data
    return blueprints


def validate_relationships(blueprints: dict[str, dict[str, Any]], skills_root: Path) -> list[str]:
    """Validate inter-blueprint constraints."""
    errors: list[str] = []

    for skill_name, blueprint in blueprints.items():
        blueprint_path = skills_root / skill_name / "blueprint.yaml"
        depends_on = blueprint.get("depends_on") or {}

        if not isinstance(depends_on, dict):
            continue

        for dep_name, dep_spec in depends_on.items():
            if not isinstance(dep_name, str):
                continue

            if dep_name == skill_name:
                errors.append(f"{blueprint_path}: skill cannot depend on itself")
                continue

            if dep_spec is None:
                dep_spec = {}
            if not isinstance(dep_spec, dict):
                continue

            major_version = dep_spec.get("major_version")
            exports = dep_spec.get("exports") or []
            if not isinstance(exports, list):
                exports = []

            dep_blueprint = blueprints.get(dep_name)
            if dep_blueprint is not None:
                if major_version is None:
                    errors.append(
                        f"{blueprint_path}: depends_on.{dep_name} must declare "
                        f"major_version because {dep_name} has a blueprint"
                    )
                    continue

                dep_interface_version = dep_blueprint.get("interface_version")
                if major_version != dep_interface_version:
                    errors.append(
                        f"{blueprint_path}: depends_on.{dep_name}.major_version={major_version} "
                        f"does not match {dep_name} interface_version={dep_interface_version}"
                    )

            if dep_blueprint is not None and exports:
                dep_script_interfaces = dep_blueprint.get("script_interfaces") or {}
                if not isinstance(dep_script_interfaces, dict):
                    continue

                for export_name in exports:
                    if not isinstance(export_name, str):
                        continue

                    interface_spec = dep_script_interfaces.get(export_name)
                    if interface_spec is None:
                        errors.append(
                            f"{blueprint_path}: depends_on.{dep_name}.exports includes "
                            f"`{export_name}`, which is not defined in {dep_name}"
                        )
                        continue

                    allow_all_skills = interface_spec.get("allow_all_skills", False)
                    allowed_callers = interface_spec.get("allowed_callers") or []
                    if not isinstance(allowed_callers, list):
                        allowed_callers = []

                    if not allow_all_skills and not allowed_callers:
                        errors.append(
                            f"{blueprint_path}: depends_on.{dep_name}.exports includes "
                            f"`{export_name}`, which is internal-only in {dep_name}"
                        )
                    elif not allow_all_skills and skill_name not in allowed_callers:
                        errors.append(
                            f"{blueprint_path}: skill {skill_name} is not in allowed_callers "
                            f"for {dep_name}.{export_name}. Allowed: {allowed_callers}"
                        )

    return errors


def validate(repo_root: Path) -> list[str]:
    """Entry-point for runner: load blueprints and validate relationships."""
    skills_root = repo_root / "skills"
    if not skills_root.is_dir():
        return []
    try:
        blueprints = load_blueprints(skills_root)
    except BlueprintError as e:
        return [str(e)]
    return validate_relationships(blueprints, skills_root)


def main() -> int:
    errors = validate(Path(__file__).resolve().parents[3])
    if errors:
        print("error: invalid blueprint relationships.", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
cd ~/Documents/AI && python3 -m pytest tests/validate_blueprint_relationships.py -v
```

Expected: all 3 tests PASS.

- [ ] **Step 5: Delete old file**

```bash
git -C ~/Documents/AI rm tools/validate_blueprint_relationships.py
```

- [ ] **Step 6: Commit**

```bash
git -C ~/Documents/AI add skills/my-writing-skills/validators/blueprint_relationships.py tests/validate_blueprint_relationships.py
git -C ~/Documents/AI commit -m "feat: move validate_blueprint_relationships into my-writing-skills/validators"
```

---

## Task 5: Move `check_skill_blueprints.py` → `blueprints.py`

**Files:**
- Create: `skills/my-writing-skills/validators/blueprints.py`
- Move: `tools/sync_skill_blueprints.py` → `skills/my-writing-skills/scripts/sync_skill_blueprints.py`
- Delete: `tools/check_skill_blueprints.py`

Note: `check_skill_blueprints.py` calls `sync_skill_blueprints.py` via subprocess. After both move, the subprocess path must update accordingly.

- [ ] **Step 1: Move sync_skill_blueprints.py first**

```bash
mkdir -p ~/Documents/AI/skills/my-writing-skills/scripts
git -C ~/Documents/AI mv tools/sync_skill_blueprints.py skills/my-writing-skills/scripts/sync_skill_blueprints.py
```

- [ ] **Step 2: Verify sync script still works from new location**

```bash
python3 ~/Documents/AI/skills/my-writing-skills/scripts/sync_skill_blueprints.py --check 2>&1 | head -5
```

Expected: exits 0 (or shows sync errors if any exist in the repo — fix those separately).

- [ ] **Step 3: Write a failing smoke test**

Create `tests/validate_blueprints.py`:

```python
"""Smoke tests for skills/my-writing-skills/validators/blueprints.py."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

_VALIDATOR = (
    Path(__file__).resolve().parents[1]
    / "skills" / "my-writing-skills" / "validators" / "blueprints.py"
)
_spec = importlib.util.spec_from_file_location("blueprints", _VALIDATOR)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def test_no_skills_passes(tmp_path: Path) -> None:
    (tmp_path / "skills").mkdir()
    # No blueprint template means the template check is skipped; no skills means no errors.
    assert _mod.validate(tmp_path) == []


def test_skill_without_blueprint_flagged(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "my-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: my-skill\n---\nBody.\n")
    errors = _mod.validate(tmp_path)
    assert any("missing blueprint.yaml" in e for e in errors)


def test_skill_with_blueprint_but_no_contract_flagged(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "my-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: my-skill\n---\nBody.\n")
    (skill / "blueprint.yaml").write_text("name: my-skill\n")
    errors = _mod.validate(tmp_path)
    assert any("contract block" in e for e in errors)
```

- [ ] **Step 4: Run to confirm it fails**

```bash
cd ~/Documents/AI && python3 -m pytest tests/validate_blueprints.py -v 2>&1 | head -20
```

Expected: failure loading module.

- [ ] **Step 5: Create `skills/my-writing-skills/validators/blueprints.py`**

```python
"""Validate blueprint presence and contract-block sync rules for local skills."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_SYNC_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "sync_skill_blueprints.py"

CONTRACT_START = "<!-- BEGIN BLUEPRINT CONTRACT -->"
CONTRACT_END = "<!-- END BLUEPRINT CONTRACT -->"


def validate(repo_root: Path) -> list[str]:
    errors: list[str] = []
    skills_root = repo_root / "skills"
    blueprint_template = repo_root / "references" / "blueprint" / "template.yaml"

    if not skills_root.is_dir():
        return errors

    if not blueprint_template.exists():
        errors.append(f"{blueprint_template}: missing blueprint template reference file")

    for skill_dir in sorted(p for p in skills_root.iterdir() if p.is_dir()):
        skill_file = skill_dir / "SKILL.md"
        blueprint_path = skill_dir / "blueprint.yaml"

        if not skill_file.exists():
            continue
        if not blueprint_path.exists():
            errors.append(f"{skill_dir}: missing blueprint.yaml")
            continue

        text = skill_file.read_text(encoding="utf-8")
        start_count = text.count(CONTRACT_START)
        end_count = text.count(CONTRACT_END)
        has_contract = start_count > 0 or end_count > 0

        if start_count != end_count:
            errors.append(f"{skill_file}: blueprint contract markers are unbalanced")
        if start_count > 1 or end_count > 1:
            errors.append(f"{skill_file}: blueprint contract block must appear at most once")
        if not has_contract:
            errors.append(f"{skill_file}: local skill is missing generated blueprint contract block")

    if errors:
        return errors

    result = subprocess.run(
        [sys.executable, str(_SYNC_SCRIPT), "--check"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        if result.stdout:
            errors.extend(result.stdout.splitlines())
        if result.stderr:
            errors.extend(result.stderr.splitlines())

    return errors


def main() -> int:
    errors = validate(Path(__file__).resolve().parents[3])
    if errors:
        print("error: invalid blueprint skill layout.", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 6: Run tests to confirm they pass**

```bash
cd ~/Documents/AI && python3 -m pytest tests/validate_blueprints.py -v
```

Expected: all 3 tests PASS.

- [ ] **Step 7: Delete old file**

```bash
git -C ~/Documents/AI rm tools/check_skill_blueprints.py
```

- [ ] **Step 8: Commit**

```bash
git -C ~/Documents/AI add skills/my-writing-skills/validators/blueprints.py skills/my-writing-skills/scripts/sync_skill_blueprints.py tests/validate_blueprints.py
git -C ~/Documents/AI commit -m "feat: move blueprints validator and sync script into my-writing-skills"
```

---

## Task 6: Convert `check-names` bash → `names.py`

**Files:**
- Create: `skills/my-writing-skills/validators/names.py`
- Create: `tests/validate_names.py`
- Delete: `.githooks/skill/check-names`

- [ ] **Step 1: Write the failing tests**

Create `tests/validate_names.py`:

```python
"""Tests for skills/my-writing-skills/validators/names.py."""
from __future__ import annotations

import importlib.util
from pathlib import Path

_VALIDATOR = (
    Path(__file__).resolve().parents[1]
    / "skills" / "my-writing-skills" / "validators" / "names.py"
)
_spec = importlib.util.spec_from_file_location("names", _VALIDATOR)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def _make_skill(skills: Path, name: str, frontmatter_name: str | None = None) -> None:
    d = skills / name
    d.mkdir(parents=True, exist_ok=True)
    fn = frontmatter_name if frontmatter_name is not None else name
    (d / "SKILL.md").write_text(f"---\nname: {fn}\n---\nBody.\n")


def test_no_skills_passes(tmp_path: Path) -> None:
    (tmp_path / "skills").mkdir()
    assert _mod.validate(tmp_path) == []


def test_valid_name_passes(tmp_path: Path) -> None:
    _make_skill(tmp_path / "skills", "my-skill")
    assert _mod.validate(tmp_path) == []


def test_single_word_name_flagged(tmp_path: Path) -> None:
    _make_skill(tmp_path / "skills", "myskill")
    errors = _mod.validate(tmp_path)
    assert any("myskill" in e for e in errors)


def test_uppercase_name_flagged(tmp_path: Path) -> None:
    _make_skill(tmp_path / "skills", "My-Skill")
    errors = _mod.validate(tmp_path)
    assert any("My-Skill" in e for e in errors)


def test_missing_skill_md_flagged(tmp_path: Path) -> None:
    (tmp_path / "skills" / "my-skill").mkdir(parents=True)
    errors = _mod.validate(tmp_path)
    assert any("missing SKILL.md" in e for e in errors)


def test_frontmatter_name_mismatch_flagged(tmp_path: Path) -> None:
    _make_skill(tmp_path / "skills", "my-skill", frontmatter_name="wrong-name")
    errors = _mod.validate(tmp_path)
    assert any("frontmatter name" in e for e in errors)


def test_name_with_numbers_passes(tmp_path: Path) -> None:
    _make_skill(tmp_path / "skills", "skill-v2")
    assert _mod.validate(tmp_path) == []
```

- [ ] **Step 2: Run to confirm they fail**

```bash
cd ~/Documents/AI && python3 -m pytest tests/validate_names.py -v 2>&1 | head -20
```

Expected: failure loading module.

- [ ] **Step 3: Create `skills/my-writing-skills/validators/names.py`**

```python
"""Validate skill directory names and frontmatter name fields."""
from __future__ import annotations

import re
import sys
from pathlib import Path


def validate(repo_root: Path) -> list[str]:
    """Return error strings for every skill with an invalid name or mismatched frontmatter."""
    errors: list[str] = []
    skills_dir = repo_root / "skills"
    if not skills_dir.is_dir():
        return errors

    for skill_dir in sorted(p for p in skills_dir.iterdir() if p.is_dir()):
        skill_name = skill_dir.name
        skill_file = skill_dir / "SKILL.md"

        if not re.match(r'^[a-z0-9]+(-[a-z0-9]+)+$', skill_name):
            errors.append(
                f"{skill_dir}: skill directory name must be lower-case "
                f"dash-separated with at least two words"
            )

        if not skill_file.exists():
            errors.append(f"{skill_dir}: missing SKILL.md")
            continue

        frontmatter_name = ""
        for line in skill_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("name:"):
                frontmatter_name = line[5:].strip()
                break

        if frontmatter_name != skill_name:
            errors.append(
                f"{skill_file}: frontmatter name '{frontmatter_name}' "
                f"must match directory name '{skill_name}'"
            )

    return errors


def main() -> int:
    errors = validate(Path(__file__).resolve().parents[3])
    if errors:
        print("error: invalid skill names.", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
cd ~/Documents/AI && python3 -m pytest tests/validate_names.py -v
```

Expected: all 7 tests PASS.

- [ ] **Step 5: Delete the bash hook**

```bash
git -C ~/Documents/AI rm .githooks/skill/check-names
```

- [ ] **Step 6: Commit**

```bash
git -C ~/Documents/AI add skills/my-writing-skills/validators/names.py tests/validate_names.py
git -C ~/Documents/AI commit -m "feat: replace check-names bash hook with names.py validator"
```

---

## Task 7: Convert `check-dependencies` bash → `dependencies.py`

**Files:**
- Create: `skills/my-writing-skills/validators/dependencies.py`
- Create: `tests/validate_dependencies.py`
- Delete: `.githooks/skill/check-dependencies`

The bash script does six things:
1. Checks for forbidden parent path refs (only `../references/` or `../../tools/` allowed)
2. Checks for deprecated markers (`Sub-skills to invoke:`, `Depends on:`)
3. Verifies a `Dependencies:` block exists in SKILL.md
4. Verifies a `depends_on_skills` sidecar file exists
5. Checks Dependencies block matches sidecar
6. Checks exact skill-name mentions in the SKILL.md body match the sidecar

Special case: `update-skill-guidelines` may also reference `../../.githooks/`.

- [ ] **Step 1: Write the failing tests**

Create `tests/validate_dependencies.py`:

```python
"""Tests for skills/my-writing-skills/validators/dependencies.py."""
from __future__ import annotations

import importlib.util
from pathlib import Path

_VALIDATOR = (
    Path(__file__).resolve().parents[1]
    / "skills" / "my-writing-skills" / "validators" / "dependencies.py"
)
_spec = importlib.util.spec_from_file_location("dependencies", _VALIDATOR)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def _make_skill(skills: Path, name: str, content: str, sidecar: str | None = "") -> None:
    d = skills / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(content)
    if sidecar is not None:
        (d / "depends_on_skills").write_text(sidecar)


VALID_SKILL = """\
---
name: my-skill
---

Dependencies: none

Body with no cross-skill mentions.
"""


def test_valid_skill_passes(tmp_path: Path) -> None:
    _make_skill(tmp_path / "skills", "my-skill", VALID_SKILL, sidecar="")
    assert _mod.validate(tmp_path) == []


def test_missing_dependencies_block_flagged(tmp_path: Path) -> None:
    _make_skill(tmp_path / "skills", "my-skill",
                "---\nname: my-skill\n---\nBody.\n", sidecar="")
    errors = _mod.validate(tmp_path)
    assert any("missing Dependencies block" in e for e in errors)


def test_missing_sidecar_flagged(tmp_path: Path) -> None:
    _make_skill(tmp_path / "skills", "my-skill",
                "---\nname: my-skill\n---\nDependencies: none\n\nBody.\n",
                sidecar=None)  # no sidecar file
    errors = _mod.validate(tmp_path)
    assert any("depends_on_skills" in e for e in errors)


def test_block_sidecar_mismatch_flagged(tmp_path: Path) -> None:
    skills = tmp_path / "skills"
    _make_skill(skills, "other-skill", "---\nname: other-skill\n---\nDependencies: none\n", sidecar="")
    content = "---\nname: my-skill\n---\nDependencies:\n- other-skill\n\nBody.\n"
    _make_skill(skills, "my-skill", content, sidecar="")  # sidecar empty, block has other-skill
    errors = _mod.validate(tmp_path)
    assert any("does not match" in e for e in errors)


def test_deprecated_marker_flagged(tmp_path: Path) -> None:
    content = "---\nname: my-skill\n---\nSub-skills to invoke:\n- other\n\nDependencies: none\n"
    _make_skill(tmp_path / "skills", "my-skill", content, sidecar="")
    errors = _mod.validate(tmp_path)
    assert any("Dependencies block" in e for e in errors)


def test_forbidden_parent_path_flagged(tmp_path: Path) -> None:
    content = "---\nname: my-skill\n---\nDependencies: none\n\nSee ../../some-other/file.md\n"
    _make_skill(tmp_path / "skills", "my-skill", content, sidecar="")
    errors = _mod.validate(tmp_path)
    assert any("parent paths" in e for e in errors)


def test_allowed_parent_path_passes(tmp_path: Path) -> None:
    content = "---\nname: my-skill\n---\nDependencies: none\n\nSee ../../tools/helper.py\n"
    _make_skill(tmp_path / "skills", "my-skill", content, sidecar="")
    assert _mod.validate(tmp_path) == []
```

- [ ] **Step 2: Run to confirm they fail**

```bash
cd ~/Documents/AI && python3 -m pytest tests/validate_dependencies.py -v 2>&1 | head -20
```

Expected: failure loading module.

- [ ] **Step 3: Create `skills/my-writing-skills/validators/dependencies.py`**

```python
"""Validate skill dependency declarations and parent path references."""
from __future__ import annotations

import re
import sys
from pathlib import Path


def _find_forbidden_parent_refs(text: str, skill_name: str) -> list[tuple[int, str]]:
    if skill_name == "update-skill-guidelines":
        allowed = r'(?:references|tools|\.githooks)'
    else:
        allowed = r'(?:references|tools)'

    parent_re = re.compile(r'(?:^|[^A-Za-z0-9_.\/~\-])(?:\.\.?\/)*\.\./')
    allowed_re = re.compile(
        r'(?:^|[^A-Za-z0-9_.\/~\-])(?:\.\.?\/)*\.\./'
        + allowed
        + r'(?:\/|[\s`\'"]|$)'
    )

    results = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if parent_re.search(line) and not allowed_re.search(line):
            results.append((lineno, line))
    return results


def _extract_dependencies_block(text: str) -> list[str] | None:
    """Return sorted dep list from the Dependencies block.

    Returns [] for 'Dependencies: none', None if block is absent.
    """
    lines = text.splitlines()
    in_fm = False
    in_deps = False
    found_block = False
    deps: list[str] = []

    for i, line in enumerate(lines):
        if i == 0 and line.strip() == "---":
            in_fm = True
            continue
        if in_fm:
            if line.strip() == "---":
                in_fm = False
            continue

        if re.match(r'^Dependencies:\s*none\s*$', line, re.IGNORECASE):
            return []
        if re.match(r'^Dependencies:\s*$', line):
            in_deps = True
            found_block = True
            continue
        if in_deps:
            m = re.match(r'^\s+-\s+(.+?)\s*$', line)
            if m:
                deps.append(m.group(1))
            else:
                in_deps = False

    return sorted(deps) if found_block else None


def _body_without_deps(text: str) -> str:
    """Strip frontmatter and Dependencies block; return remaining body."""
    lines = text.splitlines()
    result: list[str] = []
    in_fm = False
    in_deps = False

    for i, line in enumerate(lines):
        if i == 0 and line.strip() == "---":
            in_fm = True
            continue
        if in_fm:
            if line.strip() == "---":
                in_fm = False
            continue
        if re.match(r'^Dependencies:\s*none\s*$', line, re.IGNORECASE):
            continue
        if re.match(r'^Dependencies:\s*$', line):
            in_deps = True
            continue
        if in_deps:
            if re.match(r'^\s+-\s+', line):
                continue
            else:
                in_deps = False
        result.append(line)

    return "\n".join(result)


def _read_sidecar(path: Path) -> list[str]:
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = re.sub(r'\s+#.*$', '', line).strip()
        if line and not line.startswith('#'):
            lines.append(line)
    return sorted(lines)


def _all_skill_names(repo_root: Path) -> set[str]:
    skills_dir = repo_root / "skills"
    names: set[str] = {p.name for p in skills_dir.iterdir() if p.is_dir()}
    for sidecar in skills_dir.glob("*/depends_on_skills"):
        for line in sidecar.read_text(encoding="utf-8").splitlines():
            line = re.sub(r'\s+#.*$', '', line).strip()
            if line and not line.startswith('#'):
                names.add(line)
    return names


def _skill_mentions_in_body(body: str, known_skills: set[str], exclude: str) -> list[str]:
    found = []
    for name in known_skills:
        if name == exclude:
            continue
        pattern = r'(?<![A-Za-z0-9:_\-])' + re.escape(name) + r'(?![A-Za-z0-9:_\-])'
        if re.search(pattern, body):
            found.append(name)
    return sorted(found)


def validate(repo_root: Path) -> list[str]:
    errors: list[str] = []
    skills_dir = repo_root / "skills"
    if not skills_dir.is_dir():
        return errors

    known_skills = _all_skill_names(repo_root)

    for skill_file in sorted(skills_dir.glob("*/SKILL.md")):
        skill_dir = skill_file.parent
        skill_name = skill_dir.name
        sidecar_path = skill_dir / "depends_on_skills"

        text = skill_file.read_text(encoding="utf-8")

        # 1. Forbidden parent path references
        for lineno, line in _find_forbidden_parent_refs(text, skill_name):
            errors.append(
                f"{skill_file}:{lineno}: parent paths may only point to "
                f"../references or ../../tools: {line.strip()}"
            )

        # 2. Deprecated dependency markers
        for lineno, line in enumerate(text.splitlines(), start=1):
            if re.search(r'Sub-skills to invoke:|Depends on:', line):
                errors.append(
                    f"{skill_file}:{lineno}: use the Dependencies block "
                    f"plus depends_on_skills"
                )

        # 3. Dependencies block must exist
        deps_block = _extract_dependencies_block(text)
        if deps_block is None:
            errors.append(f"{skill_file}: missing Dependencies block")

        # 4. Sidecar must exist
        if not sidecar_path.exists():
            errors.append(f"{skill_file}: missing {sidecar_path}")
            continue

        sidecar_deps = _read_sidecar(sidecar_path)

        # 5. Dependencies block must match sidecar
        if deps_block is not None and sorted(deps_block) != sidecar_deps:
            errors.append(
                f"{skill_file}: Dependencies block does not match {sidecar_path}"
            )

        # 6. Body mentions must match sidecar
        body = _body_without_deps(text)
        body_mentions = _skill_mentions_in_body(body, known_skills, skill_name)
        if body_mentions != sidecar_deps:
            errors.append(
                f"{skill_file}: exact skill-name mentions in SKILL.md body "
                f"do not match {sidecar_path}"
            )

    return errors


def main() -> int:
    errors = validate(Path(__file__).resolve().parents[3])
    if errors:
        print("error: invalid skill dependencies.", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
cd ~/Documents/AI && python3 -m pytest tests/validate_dependencies.py -v
```

Expected: all 7 tests PASS.

- [ ] **Step 5: Delete the bash hook**

```bash
git -C ~/Documents/AI rm .githooks/skill/check-dependencies
```

- [ ] **Step 6: Commit**

```bash
git -C ~/Documents/AI add skills/my-writing-skills/validators/dependencies.py tests/validate_dependencies.py
git -C ~/Documents/AI commit -m "feat: replace check-dependencies bash hook with dependencies.py validator"
```

---

## Task 8: Create `validators/runner.py`

**Files:**
- Create: `validators/runner.py`

The runner discovers validators in two packages:
- `validators/` (this package — skip `__init__` and `runner`)
- `skills/my-writing-skills/validators/` (skill-system package)

Each module is loaded by file path (avoids package naming conflicts) and must export `validate(repo_root: Path) -> list[str]`.

- [ ] **Step 1: Write a failing runner test**

Append to `tests/validate_platform_neutral.py`:

```python
def test_runner_exits_zero_on_clean_repo(tmp_path: Path) -> None:
    import subprocess
    runner = Path(__file__).resolve().parents[1] / "validators" / "runner.py"
    result = subprocess.run(
        ["python3", str(runner), "--repo-root", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_runner_exits_nonzero_on_violation(tmp_path: Path) -> None:
    import subprocess
    d = tmp_path / "skills" / "a-skill"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("Use Claude here.\n")
    runner = Path(__file__).resolve().parents[1] / "validators" / "runner.py"
    result = subprocess.run(
        ["python3", str(runner), "--repo-root", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
```

- [ ] **Step 2: Run to confirm they fail**

```bash
cd ~/Documents/AI && python3 -m pytest tests/validate_platform_neutral.py::test_runner_exits_zero_on_clean_repo -v 2>&1 | head -20
```

Expected: FAIL — `runner.py` doesn't exist.

- [ ] **Step 3: Create `validators/runner.py`**

```python
#!/usr/bin/env python3
"""Discover and run all validator modules.

Scans two packages:
- validators/          (global repo validators)
- skills/my-writing-skills/validators/  (skill-system validators)

Each *.py module (excluding __init__ and runner) must export:
    validate(repo_root: Path) -> list[str]
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

_REPO_ROOT_DEFAULT = Path(__file__).resolve().parents[1]
_VALIDATOR_PACKAGES = [
    Path(__file__).resolve().parent,                               # validators/
    _REPO_ROOT_DEFAULT / "skills" / "my-writing-skills" / "validators",
]
_SKIP_STEMS = {"__init__", "runner"}


def _load_validator(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_all(repo_root: Path) -> int:
    all_errors: list[str] = []

    for package_dir in _VALIDATOR_PACKAGES:
        if not package_dir.is_dir():
            continue
        for validator_path in sorted(package_dir.glob("*.py")):
            if validator_path.stem in _SKIP_STEMS:
                continue
            mod = _load_validator(validator_path)
            errors: list[str] = mod.validate(repo_root)
            all_errors.extend(errors)

    if all_errors:
        for error in all_errors:
            print(f"- {error}")
        return 1

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run all repo validators.")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=_REPO_ROOT_DEFAULT,
        help="Path to the repository root (default: parent of this file).",
    )
    args = parser.parse_args()
    return run_all(args.repo_root)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run all validator tests**

```bash
cd ~/Documents/AI && python3 -m pytest tests/validate_platform_neutral.py tests/validate_skill_metadata.py tests/validate_boundaries.py tests/validate_blueprint_relationships.py tests/validate_blueprints.py tests/validate_names.py tests/validate_dependencies.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Smoke-test the runner against the live repo**

```bash
python3 ~/Documents/AI/validators/runner.py
```

Expected: exits 0. If there are real violations, fix them before continuing.

- [ ] **Step 6: Commit**

```bash
git -C ~/Documents/AI add validators/runner.py tests/validate_platform_neutral.py
git -C ~/Documents/AI commit -m "feat: add validator runner; discovers both validator packages"
```

---

## Task 9: Move behavior tests to their skill homes

**Files:**
- Move: `tests/test_skill_blueprint_tools.py` → `skills/my-writing-skills/tests/test_blueprint_tools.py`
- Move: `tests/test_claude_install.py` → `skills/install-assistant-tools/tests/test_claude_install.py`
- Move: `tests/test_codex_install.py` → `skills/install-assistant-tools/tests/test_codex_install.py`
- Move: `tests/install_test_utils.py` → `skills/install-assistant-tools/tests/install_test_utils.py`
- Move: `skills/recurring-tasks/scripts/test_enable_disable.py` → `skills/recurring-tasks/tests/test_enable_disable.py`
- Move: `skills/recurring-tasks/scripts/test_sync_units.py` → `skills/recurring-tasks/tests/test_sync_units.py`

- [ ] **Step 1: Move blueprint tooling test**

```bash
mkdir -p ~/Documents/AI/skills/my-writing-skills/tests
git -C ~/Documents/AI mv tests/test_skill_blueprint_tools.py skills/my-writing-skills/tests/test_blueprint_tools.py
```

Update the `REPO_ROOT` line inside the moved file (currently `parents[1]`, needs to stay `parents[3]` from new location):

```bash
sed -i 's|Path(__file__).resolve().parents\[1\]|Path(__file__).resolve().parents[3]|g' \
  ~/Documents/AI/skills/my-writing-skills/tests/test_blueprint_tools.py
```

- [ ] **Step 2: Confirm it still runs**

```bash
cd ~/Documents/AI && python3 -m pytest skills/my-writing-skills/tests/test_blueprint_tools.py -v 2>&1 | head -30
```

Expected: tests PASS (or skip if env conditions aren't met — not FAIL).

- [ ] **Step 3: Move install tests**

```bash
git -C ~/Documents/AI mv tests/test_claude_install.py skills/install-assistant-tools/tests/test_claude_install.py
git -C ~/Documents/AI mv tests/test_codex_install.py skills/install-assistant-tools/tests/test_codex_install.py
git -C ~/Documents/AI mv tests/install_test_utils.py skills/install-assistant-tools/tests/install_test_utils.py
```

Update internal imports in both test files — `install_test_utils` is imported relatively. In each of `test_claude_install.py` and `test_codex_install.py`, find:

```python
from tests.install_test_utils import ...
```

or:

```python
sys.path.insert(0, str(REPO_ROOT / "tests"))
```

and update to point to `skills/install-assistant-tools/tests/` instead. Check the actual import:

```bash
grep -n "install_test_utils\|sys.path" ~/Documents/AI/skills/install-assistant-tools/tests/test_claude_install.py ~/Documents/AI/skills/install-assistant-tools/tests/test_codex_install.py
```

Adjust the path in each file to `Path(__file__).resolve().parent` (already in the same directory).

- [ ] **Step 4: Move recurring-tasks tests**

```bash
mkdir -p ~/Documents/AI/skills/recurring-tasks/tests
git -C ~/Documents/AI mv skills/recurring-tasks/scripts/test_enable_disable.py skills/recurring-tasks/tests/test_enable_disable.py
git -C ~/Documents/AI mv skills/recurring-tasks/scripts/test_sync_units.py skills/recurring-tasks/tests/test_sync_units.py
```

Update `REPO_ROOT` paths in both files if they use `parents[N]` — check first:

```bash
grep -n "parents\|resolve" ~/Documents/AI/skills/recurring-tasks/tests/test_enable_disable.py ~/Documents/AI/skills/recurring-tasks/tests/test_sync_units.py
```

- [ ] **Step 5: Confirm recurring-tasks tests still pass**

```bash
cd ~/Documents/AI && python3 -m pytest skills/recurring-tasks/tests/ -v 2>&1 | head -30
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git -C ~/Documents/AI commit -m "refactor: move behavior tests into their skill directories"
```

---

## Task 10: Distribute remaining `tools/` utilities

**Files:**
- Move: `tools/invoke_skill_export.py` → `scripts/invoke_skill_export.py`
- Move: `tools/migrate_all_skills_to_blueprints.py` → `scripts/migrate_all_skills_to_blueprints.py`
- Delete: `tools/` (now empty)

- [ ] **Step 1: Move utilities**

```bash
mkdir -p ~/Documents/AI/scripts
git -C ~/Documents/AI mv tools/invoke_skill_export.py scripts/invoke_skill_export.py
git -C ~/Documents/AI mv tools/migrate_all_skills_to_blueprints.py scripts/migrate_all_skills_to_blueprints.py
```

- [ ] **Step 2: Update REPO_ROOT in both scripts**

Both currently use `parents[1]` (from `tools/`). They should now use `parents[1]` still (from `scripts/` — same depth). No change needed.

Verify:

```bash
grep -n "parents" ~/Documents/AI/scripts/invoke_skill_export.py ~/Documents/AI/scripts/migrate_all_skills_to_blueprints.py | head -10
```

If any path was `parents[1]`, it stays `parents[1]`. If any was hardcoded differently, fix it.

- [ ] **Step 3: Confirm tools/ is now empty and remove it**

```bash
ls ~/Documents/AI/tools/ 2>/dev/null && echo "still has files" || echo "empty"
```

If empty:

```bash
rmdir ~/Documents/AI/tools/
git -C ~/Documents/AI add -A
```

- [ ] **Step 4: Commit**

```bash
git -C ~/Documents/AI commit -m "refactor: move tools/ utilities to scripts/; delete tools/ directory"
```

---

## Task 11: Simplify pre-commit hook, delete obsolete hooks

**Files:**
- Modify: `.githooks/pre-commit`
- Delete: `.githooks/skill/check-platform-neutral`
- Delete: `.githooks/skill/check-metadata`
- Delete: `.githooks/skill/check-blueprints`
- Delete: `.githooks/skill/check-boundaries`
- Delete: `.githooks/skill/check-blueprint-tooling`
- Delete: `.githooks/skill/check-install`
- Delete: `.githooks/git/check-not-detached`

- [ ] **Step 1: Delete all remaining bash hooks**

```bash
git -C ~/Documents/AI rm \
  .githooks/skill/check-platform-neutral \
  .githooks/skill/check-metadata \
  .githooks/skill/check-blueprints \
  .githooks/skill/check-boundaries \
  .githooks/skill/check-blueprint-tooling \
  .githooks/skill/check-install \
  .githooks/git/check-not-detached
```

- [ ] **Step 2: Rewrite `.githooks/pre-commit`**

```bash
#!/usr/bin/env bash
set -euo pipefail

if ! git symbolic-ref HEAD >/dev/null 2>&1; then
    echo "error: repository is in detached HEAD state." >&2
    echo "Check out a named branch before committing:" >&2
    echo "  git checkout master" >&2
    exit 1
fi

REPO_ROOT="$(git rev-parse --show-toplevel)"

# Regenerate PROFILES.md if config files changed
if [ -f "$REPO_ROOT/scripts/generate-settings-table.sh" ]; then
    bash "$REPO_ROOT/scripts/generate-settings-table.sh" >/dev/null 2>&1
    if ! git diff --quiet "$REPO_ROOT/PROFILES.md" 2>/dev/null; then
        echo "✓ Updated PROFILES.md based on latest config files"
        git add "$REPO_ROOT/PROFILES.md"
    fi
fi

python3 "$REPO_ROOT/validators/runner.py"
```

- [ ] **Step 3: Test the hook directly**

```bash
bash ~/Documents/AI/.githooks/pre-commit
```

Expected: exits 0 — runner finds no violations.

- [ ] **Step 4: Confirm `.githooks/skill/` directory is now empty and remove it**

```bash
ls ~/Documents/AI/.githooks/skill/ 2>/dev/null && echo "still has files" || rmdir ~/Documents/AI/.githooks/skill/ ~/Documents/AI/.githooks/git/
```

- [ ] **Step 5: Commit**

```bash
git -C ~/Documents/AI add .githooks/pre-commit
git -C ~/Documents/AI commit -m "refactor: simplify pre-commit to single runner call; delete all bash check-* hooks"
```

---

## Final State

```
.githooks/
  pre-commit                              # detached-HEAD guard + calls runner
validators/
  __init__.py
  platform_neutral.py                     # validate() → list[str]
  runner.py                               # discovers both packages; --repo-root flag
skills/
  my-writing-skills/
    SKILL.md
    validators/
      __init__.py
      skill_metadata.py                   # validate() → list[str]
      blueprints.py                       # validate() → list[str]
      boundaries.py                       # validate() → list[str]
      blueprint_relationships.py          # validate() → list[str]
      names.py                            # validate() → list[str]
      dependencies.py                     # validate() → list[str]
    scripts/
      sync_skill_blueprints.py            # utility (called by blueprints.py)
    tests/
      test_blueprint_tools.py             # behavior test (moved from tests/)
  install-assistant-tools/
    tests/
      test_claude_install.py              # moved from tests/
      test_codex_install.py               # moved from tests/
      install_test_utils.py               # moved from tests/
  recurring-tasks/
    tests/
      test_enable_disable.py              # moved from scripts/
      test_sync_units.py                  # moved from scripts/
scripts/
  generate-settings-table.sh             # unchanged
  invoke_skill_export.py                  # moved from tools/
  migrate_all_skills_to_blueprints.py    # moved from tools/
tests/
  validate_platform_neutral.py           # pytest — proves validator catches violations
  validate_skill_metadata.py             # pytest — proves validator catches violations
  validate_boundaries.py                 # pytest — proves validator catches violations
  validate_blueprint_relationships.py    # pytest — proves validator catches violations
  validate_blueprints.py                 # pytest — proves validator catches violations
  validate_names.py                      # pytest — proves validator catches violations
  validate_dependencies.py               # pytest — proves validator catches violations
```

**Validator contract:** every `*.py` in `validators/` or `skills/my-writing-skills/validators/` (except `__init__.py` and `runner.py`) must export `validate(repo_root: Path) -> list[str]`. Empty list = clean. Adding a new validator is one file drop — no hook edits needed.
