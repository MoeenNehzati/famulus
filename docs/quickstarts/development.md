# Software Development Quickstart

Software-development skills cover repository safety, new TDD projects, remote
CI failures, difficult branch integration, and continuity between work
sessions. Start from the exact repository and task, then choose the narrowest
workflow that matches the situation.

## What to use when

| Need | Skill |
|---|---|
| Initialize a brand-new project with a staged TDD workflow | `initialize-tdd` |
| Inspect or change Git state, make repository edits, or prepare a commit | `git-workflow` |
| Diagnose and repair failing or unexpectedly pending GitHub Actions | `ci-debug` |
| Integrate branches whose structures have diverged beyond a normal merge | `semantic-integration` |
| Preserve decisions, failed approaches, and remaining work before stopping | `prepare-handoff` |

## A typical development workflow

Use `initialize-tdd` only for a brand-new project; it is not the route for
adding tests to an existing repository. Once work is inside a repository,
`git-workflow` provides branch safety, change ownership, exact-scope staging,
and commit hygiene.

Use `ci-debug` when GitHub Actions is failing or stuck. Use ordinary local
debugging for a local test failure. Use `semantic-integration` only when a
normal merge or localized conflict resolution would lose the intent of
substantially diverged branches.

Before pausing or ending substantial project work, use `prepare-handoff` so
decisions, useful failed paths, and unresolved work are preserved for later
sessions rather than existing only in the chat.
