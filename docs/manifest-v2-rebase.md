# Ownership-Aware Install Manifest (v2) — Rebase Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status: NOT STARTED.** This is a plan document only — no implementation has begun. Written on 2026-08-02 specifically so this follow-up isn't lost after being deferred across all six of the 2026-07-27 v5-rebase plans (installer-runtime, dispatcher-contracts, google-onboarding, recurring-reliability, downstream-workflows, macos-acceptance) plus the uv-bootstrap task — all of which are now implemented, reviewed, and CI-validated.

---

## Why this plan exists

Two concrete problems exist right now, today, on `master`, that this plan is the only thing that closes:

1. **Uninstall is not reference-safe, and nothing tests that it is.** The installer has no concept of which on-disk resources (launchers, hooks, config/state paths, scheduler registrations, credential references) belong to *which* installation. If a user has two independent installs sharing state (e.g. a plugin-mode install and a dev-mode checkout, or two plugin installs at different versions), running uninstall on one has no principled way to know whether a given resource is safe to delete or still owned by the other. Today this isn't a theoretical gap: `tests/test_install_lifecycle.py`'s uninstall/purge tests and the macos-acceptance plan's v1→v2 migration test are already written — they were written *for* this plan's eventual API — but are currently `@pytest.mark.skip(reason="requires manifest-v2, tracked separately")`. That means uninstall/purge currently ships with **zero acceptance-level test coverage**. This plan is what turns those skips into real, passing tests.

2. **The original design for this was written once (2026-07-24), then orphaned by two structural rewrites.** It lived only inside `docs/plans/osx_feedback_fix/01-installer-runtime.md`, a file now explicitly marked superseded/historical/not-executable (predates the "unified architecture v4" and "nested modules v5" migrations, wrong file paths throughout). The 2026-07-27 installer-runtime-v5-rebase plan correctly deferred re-doing this work to keep its own diff reviewable, but only left a one-line pointer behind ("track it as a separate follow-up plan") — not an actual rebased, actionable plan. That's a real risk of the idea getting permanently lost between "predates the rewrite" and "was never actually re-planned." This document is that rebase: it re-grounds the original design's still-sound ownership-model ideas against the real, current v5 code (most of which didn't exist yet when the original was written), and corrects the parts that are now wrong (see "What already exists" below — a substantial part of what the original design set out to build has since been built for real, differently).

**What currently depends on this landing (already written, currently skipped, waiting on this plan):**
- `tests/test_install_lifecycle.py`'s uninstall/purge tests (added in the macos-acceptance-v5-rebase plan, commit `3eb6412`).
- `docs/superpowers/plans/2026-07-27-macos-acceptance-v5-rebase.md` Task 2's v1→v2 migration acceptance test.

---

## What already exists — do not reimplement this part

The original 2026-07-24 design bundled two things together: (a) an atomic, journaled *release activation* mechanism, and (b) an *ownership-aware manifest* for uninstall. Part (a) is **already fully built, reviewed, and CI-validated** by this session's installer-runtime-v5-rebase, as a simpler, real mechanism:

- `src/officina/install/runtime_pointer.py`: `RuntimePointer`, `load_current_pointer`, `activate_release` — atomic `current.json` writes (via `officina.common.atomic_files`), with adversarially-verified symlink-containment security checks (`trusted_interpreter_roots`).
- `src/officina/install/managed_runtime.py`: `build_candidate_release` — builds a real venv + installs real dependencies via `uv`, deploys the resolver atomically, and only activates via `runtime_pointer.activate_release` after every step succeeds. Failed/partial builds never get an activated pointer (though see Known limitation below).
- Releases already live at `<runtime_root>/releases/<release_id>/`, retained (not pruned) for rollback.

**Do not build a second release-activation/journal mechanism.** Manifest v2's job is narrower than the original design assumed: track *ownership* of installer-created resources (launchers, hooks, config/state paths, scheduler registrations, credential references) for uninstall/purge — referencing the *already-existing* `current.json`/release-id as one more owned resource, not re-implementing how it gets activated.

Also already existing and to be migrated *from*, not replaced piecemeal:
- `skills/install-assistant-tools/_rtx/_state_record.py`: the current v1 manifest. Flat shape: `{"version": 1, "entries": [{"kind": ..., "path": ..., ...}]}`, no ownership concept, dedupes on `(kind, path)`. `MANIFEST_VERSION = 1`. Real entry kinds already recorded: `symlink`, `marker_block`, `json_hook_commands`, `git_hooks_path`, `file`, `config_dir` (purge-only), `pip_editable`, `registry_env`. Located at `home / ".local" / "state" / "assistant-tools" / "install-manifest.json"` (verify this path convention against `FamulusPaths` before implementing — it predates `FamulusPaths` and may need to move under `resolve_famulus_paths(...).install_state_root`, which already exists for exactly this purpose; check whether anything already reads/writes the v1 path directly that would need a compat shim).
- **Known limitation to fix as part of this plan, not carry forward:** `managed_runtime.build_candidate_release` currently never prunes a failed/orphaned `releases/<id>/` directory (a real gap found by this session's `/code-review max` pass, not yet fixed — every failed build attempt leaves an orphaned, possibly multi-hundred-MB venv on disk forever). Manifest v2's ownership tracking is the natural place to also close this: a `runtime_release` resource that's ownerless (build failed before any installation referenced it) should be a real garbage-collection candidate.

---

## Detailed requirements

Give `install-assistant-tools` a manifest that can answer "what does this specific installation (plugin-mode vs. dev-mode, or two independent plugin installs) own" precisely enough that uninstall can remove *exactly* that installation's resources and nothing another still-live installation depends on. Concretely, the finished plan must provide:

1. A **v2 schema** with explicit `installations` and `resources`, every resource carrying an explicit `owners` list (no implicit ownership).
2. A **lossless, atomic v1→v2 migration** — every existing v1 entry maps to a v2 resource owned by a synthetic legacy installation; the migration itself must be crash-safe (old file or new file, never a partial write).
3. **Reference-safe uninstall** — removes only the selected installation's ownership; a resource is only actually deleted once its owners list is empty; validated against the two-installations-share-one-resource case, not just the single-installation case.
4. **`--purge` semantics for credentials** that go through the real secret store API rather than deleting files directly, and default (non-purge) uninstall must never touch credentials.
5. **Real acceptance coverage** — turns the two currently-skipped test files into real, passing, unweakened tests.

## Tech Stack

Python 3.11+, pytest, the existing `officina.common.atomic_files` primitives for the migration write.

## Hard dependency

All six 2026-07-27 v5-rebase plans plus the uv-bootstrap task (all merged into `worktree-osx-installer-runtime-v5` as of this writing). This plan assumes `officina.install.runtime_pointer`/`managed_runtime` and `officina.common.famulus_paths` exist exactly as built there.

---

## Plan of implementation

### Task 0: Verify current state (no code change)

- [ ] **Step 1:** Confirm `_state_record.py`'s real path/shape hasn't drifted since this doc was written — re-read the file, re-run `python3 -m pytest -q -o pythonpath=src skills/install-assistant-tools/tests/ -k manifest -v` and note what currently exists.
- [ ] **Step 2:** Grep the whole repo for every current reader/writer of the v1 manifest (`Manifest(...)`, `manifest_path(...)`) to build the exact list of call sites this plan must update — do not assume the original design's file inventory is still accurate; it predates this repo's real module layout.
- [ ] **Step 3:** Confirm `tests/test_install_lifecycle.py`'s skipped uninstall/purge tests and the macos-acceptance plan's skipped migration test still exist with the same skip reasons, and read their bodies fully — they were written *for* this plan's eventual API (`load_manifest`, `MANIFEST_VERSION == 2`, `.all_paths()`) and are a real, already-written spec for part of this plan's public surface. Treat their exact expectations as authoritative where they conflict with anything below.

### Task 1: Define the v2 schema and a lossless v1→v2 migration

- [ ] **Step 1:** Write failing tests for the v2 schema shape and a real v1→v2 migration (construct a realistic v1 manifest with at least one of each real entry kind listed above, migrate it, assert every v1 entry maps to a resource owned by a synthetic `legacy-install` installation, and assert the migration is atomic — a crash mid-write must leave either the complete old v1 file or the complete new v2 file, never a partial/corrupt one, using `officina.common.atomic_files`).
- [ ] **Step 2:** Implement the v2 shape (adapt, don't copy verbatim, from the original design's JSON sketch):
  ```json
  {
    "schema_version": 2,
    "installations": {
      "plugin": {"mode": "plugin", "resources": ["runtime:current", "launcher:dispatcher"]}
    },
    "resources": {
      "runtime:current": {"kind": "runtime_release", "release_id": "<matches runtime_pointer's current.json>", "owners": ["plugin"]},
      "launcher:dispatcher": {"kind": "launcher", "path": "/absolute/path", "owners": ["plugin"]}
    }
  }
  ```
  Resource kinds needed (derive from the real v1 entry kinds found in Task 0 Step 2, plus): `runtime_release` (references `runtime_pointer`'s release_id, does not duplicate its activation state), `launcher`, `hook`, `config_path`, `state_path`, `scheduler_registration`, `credential` (purge-only, stores only a secret-store reference — **never serialize secret values**, matching this session's existing google-credentials convention of storing only `*_ref` fields on disk).
- [ ] **Step 3:** Validation: exact bidirectional references (every resource's `owners` names an installation that lists it, and vice versa), reject the whole manifest on any mismatch rather than silently dropping bad entries — mirror the strictness of this session's other schema-validated formats rather than the original design's prose alone.
- [ ] **Step 4:** Run tests, confirm green. Commit: `git add` the manifest module + tests, `git commit -m "feat(install-assistant-tools): ownership-aware manifest v2 with lossless v1 migration"`.

### Task 2: Ownership-aware uninstall

- [ ] **Step 1:** Write failing tests: two installations sharing one resource (e.g. two plugin installs both referencing the same `runtime_release`) — uninstalling one must remove only that installation's ownership and leave the shared resource in place for the other; uninstalling the last owner of a resource must actually remove it (file/launcher/hook/registration deleted for real, not just from the manifest).
- [ ] **Step 2:** Implement uninstall against the v2 manifest: remove the selected installation from each owned resource's `owners`; for resources that become ownerless, actually delete them, in dependency order (disable/remove scheduler registrations → public launchers/hooks → unreferenced config/state → only then, if this was the last installation entirely, the runtime release and `current.json` itself, deferring to whatever cleanup `runtime_pointer`/`managed_runtime` already expose rather than deleting `releases/<id>/` by hand).
- [ ] **Step 3:** `--purge`: additionally remove `credential` resources by calling the real secret-store deletion API (check `officina.common.secret_store`'s real interface) and drop the registry reference — never touch credentials on a non-purge uninstall.
- [ ] **Step 4:** Un-skip `tests/test_install_lifecycle.py`'s uninstall/purge tests and the macos-acceptance plan's migration test (remove the `@pytest.mark.skip` now that the API they were written against is real) — do not weaken their existing assertions to make them pass; if any assertion turns out to be wrong given the real implementation, that's a signal to revisit this plan's design, not the test.
- [ ] **Step 5:** Run the full suite, confirm no regressions. Commit.

### Task 3: Garbage-collect ownerless failed release builds

- [ ] **Step 1:** Write a failing test: `managed_runtime.build_candidate_release` fails partway (mirroring this session's existing `test_failed_update_leaves_prior_pointer_and_release_usable`-style failure injection), and assert the orphaned `releases/<id>/` directory is either never left ownerless in the manifest, or is cleaned up by a real GC pass.
- [ ] **Step 2:** Implement: either have `build_candidate_release` itself register+deregister the candidate release as a resource around its build attempt (cleanest, closes the gap at the source), or add an explicit GC pass invoked from uninstall/repair that removes ownerless `runtime_release` resources' on-disk directories. Prefer the former if it doesn't require reopening `managed_runtime.py`'s already-reviewed core logic in a way that risks the invariants this session's adversarial review already verified there (symlink containment, no-partial-pointer-on-failure) — investigate before choosing.
- [ ] **Step 3:** Run tests, confirm green. Commit.

---

## Explicitly out of scope

- Re-implementing release activation/journaling — `runtime_pointer.py`/`managed_runtime.py` already do this; this plan only adds ownership tracking on top.
- Any change to `officina.common.secret_store`'s own API — this plan is a consumer of it for `--purge`, not a modifier.
