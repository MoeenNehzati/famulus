# GitHub marketplace install tests — design

## Purpose

The existing install E2E tests (`skills/install-assistant-tools/tests/test_claude_install.py`,
`test_codex_install.py`, `test_e2e_lifecycle.py`) all add the marketplace from a **local
filesystem path** — never from GitHub. README's actual quick-start tells users to add the
marketplace via `MoeenNehzati/famulus` (Claude) or a local checkout (Codex), which the current
tests don't exercise.

This adds two new tests that run the exact commands from README's "Recommended: plugin install"
section against the real, public `MoeenNehzati/famulus` GitHub repo, to catch packaging problems
that only manifest when installing from what's actually published (e.g. a file present locally
but not committed/pushed, `.gitignore` exclusions, symlinks that don't survive a real clone).

This is a standing health check on the currently-published default branch, not a per-PR diff
gate — Claude's `owner/repo` marketplace source has no ref-pinning option, so it always resolves
whatever is on the GitHub default branch regardless of which local commit triggered the test run.
That's accepted as fine: the goal is "is what a user gets today from GitHub correct and
uncorrupted," not "does this PR's diff work."

Per user decision: no network-availability skip. If GitHub is unreachable, the test fails loudly
like any other command failure — no silent skip.

## What gets added

### `skills/install-assistant-tools/tests/test_claude_github_install.py`

Mirrors `test_claude_install.py`'s structure, with the marketplace source swapped:

```
claude plugins marketplace add MoeenNehzati/famulus
claude plugins install famulus@nullkit
```

against an isolated `CLAUDE_HOME`/`HOME` (same `claude_env()` helper as the existing test).
Assertions carried over unchanged:

- marketplace/plugin show up in `claude plugins marketplace list` / `claude plugins list`
- installed cache path is under `<claude_home>/plugins/cache`, not the repo checkout
- every skill in `expected_skills()` has `skills/<name>/SKILL.md` in the installed cache
- required assets present (`.claude-plugin/plugin.json`, `CLAUDE.md`, `hooks/hooks.json`,
  `agents/*.md`, etc.)
- `claude plugins details` reports correct skill/agent counts and names
- a real `claude -p` session emits `SessionStart` `hook_started`/`hook_response` events with the
  dispatcher-context payload
- uninstall removes the plugin from `claude plugins list` and the marketplace from
  `claude plugins marketplace list`

Not carried over: the local test's own preceding validator run
(`skill_metadata.py`, `platform_neutral.py`) against `REPO_ROOT` — redundant here since the local
test already covers that, and this test's job is specifically the GitHub-sourced install path.

### `skills/install-assistant-tools/tests/test_codex_github_install.py`

Same idea for Codex, using README's `codex plugin marketplace add`/`plugin add` shape but with
the GitHub source instead of a local checkout:

```
codex plugin marketplace add MoeenNehzati/famulus --json
codex plugin add famulus@nullkit --json
```

Assertions carried over from `test_codex_install.py`'s packaging-check portion only (not its
install-assistant-tools bootstrap/launcher-symlink phase, which is a separate concern already
covered without needing network):

- installed cache path resolves under `<codex_home>/plugins/cache`
- every expected skill's `SKILL.md` present, plus required shared assets (`AGENTS.md`,
  `CLAUDE.md`, `agents/*.md`, `profiles/*`)
- each skill is visible via `codex debug prompt-input "Use $famulus:<skill>."`
- `codex plugin remove` + `codex plugin marketplace remove` fully deregister it, and skills are
  no longer visible afterward

### `install_test_utils.py` addition

A small helper to avoid hardcoding `MoeenNehzati/famulus` twice:

```python
def github_owner_repo(repo_root: Path = REPO_ROOT) -> str:
    """owner/repo shorthand, read from the Claude plugin manifest's `repository` URL."""
```

Parses the `"repository"` field of `.claude-plugin/plugin.json` (a full
`https://github.com/<owner>/<repo>` URL) down to `"<owner>/<repo>"`.

## CI wiring

None needed. `.github/workflows/python-tests.yml`'s existing step already runs
`python3 scripts/run-python-tests.py --suite full --verbose`, whose `full`
suite includes `skills/install-assistant-tools/tests/`, on the same
push/pull_request triggers and the same Linux/macOS/Windows matrix as the rest
of the Python suite.

## Out of scope

- No changes to the existing local-path tests — they stay as the fast, no-network check of
  packaging from the working tree.
- No ref-pinning workaround for Claude (not possible with the current CLI).
- No retry/backoff around the network calls — a real failure (including a transient GitHub
  outage) should fail the run, per the "fail if it can't access the web" decision.
