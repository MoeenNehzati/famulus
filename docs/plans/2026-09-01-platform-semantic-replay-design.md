# Platform Semantic Replay Design

## Purpose

Catch known cross-platform semantic regressions on Linux without pretending to emulate another operating system. Native CI remains authoritative for discovery. When CI proves that a test is sensitive to a modeled platform policy, `ci-debug` records that exact test in a committed replay registry. Linux CI and pre-push then replay the registered test under the relevant semantic model.

This is reactive regression coverage, not an oracle for predicting every platform-sensitive test.

## Semantic boundary

A replay boundary is a production API that chooses policy from the current platform. The first boundary is `famulus-paths`.

`officina.platforms.model` owns:

- canonical models: `linux`, `macos`, and `windows`;
- `current_platform_name(token=None)`, which preserves the dispatcher's current host-name normalization and returns unsupported tokens unchanged;
- `boundary_model(boundary_id, explicit=None)`, which returns an explicit model when supplied, otherwise the active replay model or native host model;
- `platform_replay(model_id, observer=None)`, a scoped `ContextVar` override that always resets in `finally`;
- observer notification when an implicit lookup crosses a modeled boundary.

Explicit platform arguments retain their current behavior (`darwin` selects macOS, `win32` selects Windows, and other values select POSIX policy) and do not count as replay boundary crossings. Native filesystem, process, socket, browser, keyring, timing, and performance behavior are outside semantic replay and stay covered by native CI.

## Replay registry

`tests/platform-semantic-replay.json` is the committed authority for known replay regressions:

```json
{
  "schema_version": 1,
  "entries": [
    {
      "nodeid": "tests/test_officina_famulus_paths.py::test_implicit_paths_keep_feature_roots_derived",
      "boundary": "famulus-paths",
      "models": ["macos", "windows"],
      "provenance": {
        "kind": "seed",
        "reference": "docs/plans/2026-09-01-platform-semantic-replay-design.md"
      },
      "reason": "Exercises implicit Famulus path policy through stable derived-root assertions."
    }
  ]
}
```

For a native-CI discovery, provenance is:

```json
{
  "kind": "native-ci",
  "run_id": "123456789",
  "sha": "0123456789abcdef0123456789abcdef01234567",
  "os": "windows-latest"
}
```

The loader rejects unknown fields, unknown boundaries or models, duplicate `(nodeid, boundary)` pairs, empty reasons, malformed provenance, and node IDs that are absolute, option-like, traverse directories, name missing/non-test files, or lack a nonempty `::` suffix. `models` is a nonempty duplicate-free subset of `macos` and `windows`. Seed provenance has exactly `kind` and `reference`; native provenance has exactly `kind`, numeric `run_id`, 40-hex `sha`, and `os` equal to `macos-latest` or `windows-latest`. The loader also requires entries sorted by `(nodeid, boundary)` and models in `macos`, `windows` order. The runner rejects registry files excluded by the pre-push shared-test profile. Grouping de-duplicates each node/model while unioning all boundaries expected by that pair. Pytest collection is the final proof that the exact node suffix exists.

## Native-CI learning loop

There is no static boundary-discovery step. Registration uses observed evidence:

1. Native CI fails on macOS or Windows.
2. `ci-debug` isolates the exact failing node and diagnoses a modeled platform-policy cause. It chooses the exact contract-owning replay node: first try the native failing node only when it belongs to the shared profile and invokes the affected production entry point through the implicit boundary; otherwise follow the failure stack to that entry point, locate its existing functional test, and augment that test with the failing case. Create a new production-path test only when no existing test owns the contract.
3. If the relevant production boundary exists, the repair writes a provisional entry in its working tree and runs targeted Linux replay before changing production behavior. It retains the entry only when replay observes the declared boundary and reproduces the failure. If the first candidate does not, remove it and return once to step 2 to select or augment the owning test; classify the case as native-only or unresolved only when no contract-owning candidate reproduces.
4. The retained replay must pass after the repair. If no boundary exists, the repair first adds the smallest reusable production boundary; the native failure and a red/green boundary contract test must justify that scope before registration.
5. Verification runs the exact Linux replay node, the exact native node, the affected native matrix element, and then the full matrix.

CI reports evidence; it never edits Git. The `ci-debug` repair agent makes the registry change in its assigned branch and path scope. A later green run does not remove an entry. Renames and removals are explicit changes that must leave collection and replay green. Models are added only when native evidence or a contract requires them.

Failures caused only by physical host behavior are not registered.

## Replay execution

`repo_checks.py` exposes `tests:semantic-replay` on Linux. It loads the registry, groups exact node IDs by canonical model, and invokes pytest once per non-empty group with `--officina-platform-replay-model=<model>`. On non-Linux hosts it prints an explicit skip and succeeds without launching model subprocesses.

The pytest plugin activates replay only for selected registry nodes and records observed boundary crossings. A selected node fails replay if it does not collect, skips, xfails, unexpectedly passes an xfail, fails in setup/call/teardown, or does not reach every boundary declared for that node and model. Registry node IDs are the only selectors accepted by the replay task.

The pre-push suite runs:

1. its existing combined/shared baseline;
2. registered macOS replay, then Windows replay, serially, only when the baseline is green and the host is Linux;
3. its existing browser phase regardless of earlier results.

The full Linux suite also runs replay after combined tests and before browser tests. Pre-commit is unchanged. The existing portability tests remain a separate heterogeneous sentinel; they neither select nor replace registered replay tests. The pre-push hook command and CI matrix stay unchanged, apart from making `tests:semantic-replay` selectable in manual CI dispatch.

## Success criteria

- Native CI is the sole discovery source for unforeseen platform-sensitive failures.
- Every registered test names an exact node, boundary, model set, reason, and provenance record.
- Stale or misclassified entries fail loudly instead of silently shrinking coverage.
- One registry loader and one replay plugin serve local, pre-push, CI, and `ci-debug` use.
- Replay stays limited to semantic policy; native CI retains physical-platform authority.
