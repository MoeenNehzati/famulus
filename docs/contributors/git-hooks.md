# Repository Git Hooks

> **Status:** Nonnormative contributor guide.

The repository's Git hooks run local checks before a commit is created and
before `git push` transfers refs. They catch structural, generated-artifact,
secret, and test failures early, while the repository's validators and test
suites remain the authoritative enforcement machinery.

These are Git client hooks. They are distinct from the
[LLM lifecycle hooks](../lifecycle-hooks.md) that add context to an assistant
session.

## Activation

A checkout uses the tracked hooks when its repository-local Git configuration
points `core.hooksPath` at `.githooks`:

```bash
git config core.hooksPath .githooks
git config --get core.hooksPath
```

The second command should print `.githooks`. Git then discovers the executable
top-level files named for hook events: `pre-commit` and `pre-push`.

## Pre-commit

`.githooks/pre-commit` protects the candidate commit. It performs these steps
in order:

1. Reject a detached `HEAD`, because the new commit would not belong to a named
   branch.
2. Run the settings-table generator from the working-tree profile files. If
   the regenerated `PROFILES.md` differs, stage the whole file.
3. Run documentation generation against the working tree, then stage the six
   complete generated paths named by the hook: `docs/skills.md`, four domain
   documents, and `docs/contributors/README.md`.
4. Regenerate the ignored local README HTML preview from the working-tree
   `README.md`.
5. Synchronize both the working-tree and staged plugin-manifest versions from
   the staged `pyproject.toml`, preserving other manifest divergence.
6. Scan staged content with `gitleaks`; a missing `gitleaks` executable is a
   hard failure.
7. Run `python3 repo_checks.py --suite precommit`.

The `precommit` suite validates a temporary mirror of the Git index. Unstaged
and untracked files are absent, so the complete intended candidate must be
staged before this gate can test it. Earlier hook steps deliberately mix
working-tree reads with index updates. In particular, the documentation step's
full-path `git add` can stage pre-existing edits in any of its six named files.
Review both the working-tree and staged diffs after the hook runs and before
finalizing the commit.

## Pre-push

`.githooks/pre-push` runs:

```bash
python3 repo_checks.py --suite pre-push
```

This suite uses the working tree and runs the broad local validator and
functional-test gate selected by the repository-check runner. It does not
regenerate or stage files. A failure stops the push.

A local pre-push result is not proof that remote CI will pass. It does not
reproduce every supported operating system, runner environment, or exact-SHA
matrix element. See the [Continuous Integration Handbook](../ci-handbook.md)
for that boundary.

## Targeted skill checks

The scripts under `.githooks/skill/` are focused contributor entry points, not
Git hook event names. Git does not invoke them automatically. Each runs one
validator against the staged repository view through `repo_checks.py`:

| Script | Validator |
| --- | --- |
| `check-blueprints` | `skill-maker/blueprints` |
| `check-dependencies` | `skill-maker/dependencies` |
| `check-names` | `skill-maker/names` |
| `check-runtime-files` | `repo/skill_runtime_files` |

Use these scripts for focused feedback while developing a skill. They do not
replace the root pre-commit hook, which owns the complete local commit gate.

## Responding to failures

Treat the first failing command as evidence about the candidate and its
environment. Check whether the failure concerns staged content, generated
side effects, a missing required tool, or a host capability before changing
the implementation.

For a pre-commit failure, diagnose with the smallest failing command, stage the
complete intended candidate, and then rerun the complete `pre-commit` hook.
Running only `repo_checks.py --suite precommit` rechecks the hook's final step;
it does not rerun generation, release synchronization, or `gitleaks`.

For a pre-push failure, preserve its working-tree view while narrowing the
failure:

```bash
python3 repo_checks.py --suite validators --repository-view working --validator ID
python3 repo_checks.py --suite pre-push --task TASK --selector FILE_OR_NODE
```

Then rerun the complete `pre-push` suite. `--no-verify` bypasses the repository
gate and is not a normal repair; use it only when an explicit, separately
justified exception has been authorized.

## Sources of truth

- [`.githooks/pre-commit`](../../.githooks/pre-commit) owns commit-time order
  and side effects.
- [`.githooks/pre-push`](../../.githooks/pre-push) owns the push-time command.
- [`repo_checks.py`](../../repo_checks.py) is the repository-check entry point.
- [`src/officina/repository/checks/runner.py`](../../src/officina/repository/checks/runner.py)
  owns suite contents, repository views, selection, and execution policy.
- [Repository Testing](../testing.md) is the command and suite reference.
