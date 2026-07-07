# install-assistant-tools redesign

Status: approved (design phase). Date: 2026-07-07.

## Problem

The current `install-assistant-tools` installer (`scripts/install.py` →
`setup_symlinks.py` + `setup_tools.py`) is a single all-or-nothing entry
point. It bundles unrelated concerns together (dev-mode symlinking, bin
launcher installation, PATH/env setup, worker directories, git hooks, dev-mode
hook registration, recurring-tasks/systemd scaffolding, and Google OAuth
setup) with no way for a user to opt in/out of pieces independently. Users
who only want a subset (e.g. plugin-mode users who don't want dev symlinks,
or users who want zero agent launchers) have no granular control.

## Goals

- Split the installer into independently runnable, composable subcommands
  along real dependency boundaries (not arbitrary script-file boundaries).
- Preserve existing idempotency semantics (OK/SKIP/replace-symlink policy,
  install manifest for uninstall) for every subcommand.
- Make the interactive/orchestrated flow (a human running the installer
  conversationally) feel coherent: install the machine capability first,
  then customize/connect external services, then point toward the payoff
  (scheduled automation) before the user leaves the session.

## Non-goals

- Not rewriting `script_dispatcher`, `cloud-files`, `g-calendar`, or
  `email-client` internals.
- Not changing the uninstall manifest format.
- Not adding new agents/launchers beyond the existing `assistant`, `collab`,
  `coauthor`, `tw`.

## Key findings from investigation

- `dispatcher` is the one piece of scaffolding almost every skill's
  `SKILL.md` depends on structurally (`dispatcher --caller-skill ...`
  invoked bare). It is **not** installed via the `script_dispatcher`
  package's `[project.scripts]` entry point (pip-installed console
  script) — the installer deliberately hand-writes a bash launcher into
  the managed bin dir instead, to avoid a second copy of first-party code
  that can drift. This means `dispatcher` still needs the bin dir on
  `PATH` to be invoked bare, same as any other launcher.
- Python scripts that need `script_dispatcher` as a library (e.g.
  `list-manager/scripts/cloud_transport.py`,
  `daily-plan/scripts/plan_runtime.py`) do a bare
  `from script_dispatcher import dispatch` with no path manipulation of
  their own. This only works because they run as children of the
  `dispatcher` bash launcher, which exports
  `PYTHONPATH=$AI/script_dispatcher/src` before `exec`'ing python. These
  scripts are not standalone-importable outside that process tree.
- `$AI` and `ASSISTANT_DEFAULT` are not universal dependencies. `$AI` is
  consumed only by the installer's own internals and by
  `recurring-tasks` scripts (`invoke-agent.sh`, `test-live-job.py`).
  `ASSISTANT_DEFAULT` is consumed only by `install-assistant-tools`
  itself and `recurring-tasks`. Neither is a general skill dependency.
- `install_git_hooks` already has an existing safety check
  (`git rev-parse --git-dir`) that skips git-hooks setup with a note if
  `repo_root` isn't a git checkout — this is a good internal safety net
  but is the wrong signal to use for deciding *whether to offer* dev
  mode to the user (see below).
- `email-client` does not use Google OAuth like `cloud-files`/
  `g-calendar` — it uses a nickname-based account registry
  (`accounts-add`/`accounts-list`/etc.), a materially different setup
  shape.

## Design decisions

### 1. Mode selection is an explicit question, not inferred

Do not infer dev-mode eligibility from filesystem probes (e.g. "is
`repo_root` a git checkout"). Ask the user directly:

> "Do you want development mode? This wires `~/.claude`/`~/.codex` to a
> live repo checkout so skill/hook edits take effect immediately, instead
> of a static plugin install. [y/N]" (default: no)

If yes, ask for the repo path explicitly — do not auto-derive it from the
running script's own location. Validate afterward that the path looks
like the expected repo layout; the existing git-hooks-skip-if-no-`.git`
check remains as an internal safety net inside `dev-link`, not as the
mode-decision signal itself.

### 2. Five concerns, five subcommands, two phases

**Phase 1 — Installation** (scripted, mechanical, no external accounts
needed, always safe to run in sequence):

1. `scaffold` — writes the `dispatcher` bash launcher AND the
   `invoke-skill` launcher (moved here from the old recurring-tasks
   cluster — cheap and harmless to always have, removes a later step),
   plus the PATH export needed for both to resolve bare. This is the
   universal floor: runs regardless of plugin vs dev mode, regardless of
   which agents are chosen.
2. `dev-link` — only offered if the user opted into dev mode in step 1.
   Symlinks `~/.claude`/`~/.codex` config dirs to the user-supplied repo
   path, registers dev-mode hooks in `settings.local.json`/`config.toml`,
   sets `core.hooksPath` for git hooks (skipped internally with a note if
   the supplied path isn't a git checkout).
3. `launchers --agents assistant,collab,coauthor,tw` — per selected
   agent: bin launcher + profile config copy + worker directory +
   `ASSISTANT_DEFAULT` export, all bundled together per agent (a
   launcher without its worker dir or without `ASSISTANT_DEFAULT` is
   incomplete). Multi-select, default: none preselected (explicit
   opt-in).

**Phase 2 — Customization** (conversational, delegates to each skill's
own setup mechanics rather than duplicating instructions in the
installer):

4. Connect remotes — offered once, after phase 1 completes:

   > "Want to connect your remotes now? This covers cloud-files (Google
   > Drive), g-calendar (Google Calendar), and email-client — Google-backed
   > today, extendable to other providers via skill-maker. Once connected,
   > I can set up recurring email triage and daily planning for you, so
   > it's worth doing in this session rather than leaving it for later.
   > [y/N]"

   If yes, walk through each of the three by invoking each skill's own
   documented setup path (cloud-files/g-calendar via their existing
   `setup_oauth.py`-driven flow; email-client via `accounts-add`). This
   is done conversationally by the assistant using those skills — no new
   code duplicating their setup instructions inside
   `install-assistant-tools`.

5. Recurring automation — not a subcommand of `install-assistant-tools`
   at all. It becomes `recurring-tasks`' own responsibility: when the
   user actually asks to set up a recurring job, that skill lazily and
   idempotently ensures its own prerequisites (`install_ai_agent_env`,
   `install_recurring_tasks_env_script`, `$AI`/`ASSISTANT_DEFAULT`) are
   in place, rather than the installer doing it upfront on spec. After
   remotes are connected (or declined), close with a direct offer:

   > "Remotes connected. Want me to set you up with recurring triage and
   > daily planning now?"

### 3. Functions relocated out of `install-assistant-tools`

The following functions currently in `setup_tools.py` move to the skills
that actually consume them, called on-demand by those skills instead of
by this installer:

- `install_ai_agent_env` → `recurring-tasks`
- `install_recurring_tasks_env_script` → `recurring-tasks`
- `maybe_run_cloud_files_oauth_setup`, `cloud_files_client_setup_lines`,
  `install_cloud_files_config` → `cloud-files`
- `maybe_run_g_calendar_oauth_setup`, `g_calendar_client_setup_lines` →
  `g-calendar`
- `choose_optional_google_services`, `maybe_run_optional_google_oauth_setups`,
  `google_oauth_publish_guidance_lines`, `google_service_client_setup_lines`
  → split/removed; each service's own skill owns its own OAuth guidance
  text instead of a shared chooser inside the installer.

`install_invoke_skill_launcher` moves earlier, into `scaffold`'s always-run
path (see decision 2, item 1) instead of being gated behind recurring-setup.

### 4. Open item resolved during review

`recurring-tasks`' `jobs.yaml`-based workflow needs to be checked for
whether it already has a dispatcher interface where the lazy
prerequisite setup (item 5 above) can live, or whether that skill needs
a new interface added. This is an implementation-planning question, not
a design blocker — flagged for the writing-plans step.

## Interaction model

Subcommands per concern (not a monolithic flag pile, not a single
opaque wizard):

```
install.py scaffold
install.py dev-link --repo-path PATH
install.py launchers --agents assistant,collab
install.py all   # orchestrates the conversational flow above, chaining
                  # scaffold -> [dev-link] -> launchers -> connect-remotes
                  # -> recurring-automation offer
```

Each subcommand remains independently scriptable/dry-runnable (existing
`--dry-run` support, existing manifest recording for uninstall), for
automated/CI use. `install.py all` (or the assistant driving the
installer conversationally) is what a human actually runs interactively.

## Testing

Existing test files (`test_setup_symlinks.py`,
`test_setup_tools_cloud_files.py`, `test_setup_tools_recurring_env.py`,
`test_codex_install.py`, `test_claude_install.py`) need to be
re-partitioned to match the new subcommand boundaries once
`setup_tools.py` is split. Exact test-file layout is an implementation
detail for the plan, not fixed here.
