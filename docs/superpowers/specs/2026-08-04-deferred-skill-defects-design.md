# Deferred Skill Defects Design

## Goal

Repair seven defects confirmed after the first twenty skill-refactor iterations, without broadening the standards or changing unrelated behavior.

## Repairs

1. `list-manager`: apply `--sort` to unfiltered reads as well as filtered reads, and expose the option in the route usage.
2. `math-dependency-graph`: make the graph-server dispatch binding accept exactly its three documented options and no positional arguments.
3. `recurring-tasks`: register `_jobs_config.py` as its own behavioral source and declare dependencies from every importing behavioral source.
4. `recurring-tasks`: make the generated catalog summary platform-neutral and remove the stale, unreferenced systemd-only `skill.mmd` diagram.
5. `llm-wakeup`: distinguish policy-state reporting for automatic wakeups from scheduled-time reporting for explicit or inferred scheduling.
6. `skill-drift`: replace the obsolete user-facing “v4 nodes” diagnostic with schema-neutral wording.
7. Nested-module migration: update the exact registered-module inventory after the intentional removal of empty instruction-only `_rtx` roots.

## Constraints

- Preserve existing public route IDs, argument shapes, and output schemas.
- Keep the exact inventory guard; update its expected contents rather than weakening it.
- Add no new canonical standards. The existing direct-ownership and usage-completeness rules already cover the defects.
- Treat five recurring-task importers as consumers of `_jobs_config.py`: job control, job executor, job utilities, healthcheck probe, and unit writer.
- Keep unrelated dirty work on `master` untouched by working on `codex/deferred-fixes`.

## Verification

Each executable behavior or machine contract gets a focused regression test that fails on the old state. Instruction-only routing is pressure-tested in the independent review instead of source-grep testing. After implementation, run the affected skill tests, structural blueprint checks, precommit suite, and a separate audit. The clean-worktree inventory test runs after the checkpoint commit because it deliberately rejects dirty repositories.
