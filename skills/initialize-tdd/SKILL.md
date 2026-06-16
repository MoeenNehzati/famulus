---
name: initialize-tdd
description: Use when starting a brand new project that should follow a staged, approval-gated TDD workflow (design -> tests -> implementation -> docs). Scaffolds a new project directory with CLAUDE.md, README, .gitignore, git init, the superpowers skills plugin, and (for Python) a venv + centralized logger/config modules + a starter test suite.
---

# Initialize TDD Project

Scaffolds a new project set up the way described in CLAUDE.md's staged TDD
workflow: design+stubs -> tests -> implementation -> docs, with approval
gates between steps.

## Inputs

- **name**: human-readable project name (e.g. "Jarvis"). Used for:
  - the new directory name (slugified: lowercase, spaces/underscores -> `-`,
    strip anything outside `[a-z0-9-]`)
  - the `# {{PROJECT_NAME}}` title in CLAUDE.md / README.md
  - (Python) the `name` field in `pyproject.toml` (slugified form)
- **language**: `python` gets the full scaffold (venv, logger, config,
  starter tests). Anything else gets the generic scaffold (CLAUDE.md,
  README, .gitignore, git init, superpowers) with no language-specific
  tooling — note this to the user so they know logger/config/tests aren't
  included.

If either input is missing, ask the user before proceeding.

## Steps

1. **Create the project directory**
   - Compute `slug` from `name` as described above.
   - `mkdir <slug>` in the current working directory (fail/ask if it already
     exists and is non-empty — don't overwrite existing work).
   - All subsequent steps happen inside `<slug>/`.

2. **Copy common assets**
   - Copy everything from `assets/common/` into the project dir:
     `CLAUDE.md`, `README.md`, `.gitignore`.

3. **Copy language-specific assets (if `language == python`)**
   - Copy everything from `assets/python/` into the project dir:
     `requirements.txt`, `install.sh`, `pyproject.toml`, `CLAUDE.md`,
     `README.md` (these overwrite the common versions — they're
     Python-specific supersets), `src/project/`, `tests/`, `logs/.gitkeep`.
   - Append `assets/python/.gitignore` to the `.gitignore` already copied
     from `assets/common/` (concatenate, don't overwrite — common covers
     editor/assistant files, python covers venv/python/logs).
   - `chmod +x install.sh`.

   If `language != python`: skip this step, but still do your best for the
   requested language — e.g. add a sensible `.gitignore` for that language's
   build artifacts/dependency dirs if you know them, and adapt the
   "Project conventions" section of CLAUDE.md (config files, dependency
   manifest, env setup) to that language's idioms where obvious.

   Regardless, surface a clear warning to the user: this skill is designed
   for Python (full scaffold with venv, logger, config, starter tests); for
   `<language>` it only did a best-effort generic scaffold (CLAUDE.md,
   README, .gitignore, git init, superpowers) — logger/config modules and a
   starter test suite were not created.

4. **Fill in placeholders**
   - In every copied text file, replace:
     - `{{PROJECT_NAME}}` -> `name` (human-readable, as given)
     - `{{PACKAGE_DIST_NAME}}` -> `slug` (only present in `pyproject.toml`)

5. **Initialize git**
   - `git init` in the project directory.

6. **Install superpowers skills**
   - Check `claude plugin marketplace list` (or equivalent) for
     `superpowers-marketplace`. If absent, run:
     `claude plugin marketplace add obra/superpowers-marketplace`.
   - Check installed plugins for `superpowers`. If absent, run:
     `claude plugin install superpowers@superpowers-marketplace`.
   - These are user-level/global, so skip entirely if already present from a
     prior project.

7. **Bootstrap and verify (Python only)**
   - Run `./install.sh` to create `.venv` and install dependencies. This is
     a starting-point script — if the environment differs from what it
     assumes (e.g. `python3` not on PATH, a different interpreter needed),
     adjust `install.sh` accordingly rather than regenerating it from
     scratch.
   - Run `.venv/bin/pytest -q` and confirm the starter tests pass (2 tests,
     for `config`/`logger`).
   - If anything fails, fix it before reporting success — don't claim the
     scaffold works without fresh verification output.

8. **Report, don't commit**
   - Summarize what was created and confirm tests pass (with output).
   - Do NOT create a git commit — that's a separate step the user asks for
     explicitly.

## Notes

- The Python package is generically named `project` (`src/project/`,
  `from project.logger import get_logger`, etc.) on purpose — the
  human-readable project name lives in `pyproject.toml`'s `name` field and
  in CLAUDE.md/README titles, not in the package/import paths. Don't rename
  the package per-project.
- `.env` / `.testenv` are intentionally not created by this skill (they're
  gitignored and project-specific) — modules read sensible defaults
  (`LOG_LEVEL=INFO`, `LOG_DIR=logs`) when they're absent.
