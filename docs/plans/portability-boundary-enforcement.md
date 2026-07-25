# Portability Boundary Enforcement

## Goal

Prevent recurring cross-platform failures by consolidating repeated semantics
under existing owners and enforcing those owners through the current validator,
test, and certification paths.

This is a portability hardening pass, not a new conformance system.

## Design rules

1. Extract existing semantics instead of adding parallel implementations.
2. Keep native `Path` values internally and serialize repository identifiers as
   POSIX text only at explicit boundaries.
3. Keep structured values structured; launch commands are transport, not a
   source of semantic data.
4. Add static checks only for violations that can be recognized reliably.
5. Run the same validators in pre-commit, CI, and certification.

## 1. Tracked validator execution

Refactor `validators/runner.py` in place. It remains the only CLI entry point
for repository validators and operates in two phases:

1. The bootstrap phase materializes the staged Git index into a temporary
   mirror and creates isolated Git metadata.
2. A fresh Python process runs the mirror's tracked `validators/runner.py`,
   discovers validators from that mirror, and resolves all repository imports
   from the mirror.

The bootstrap imports no validator or repository policy modules. The child
process receives a mirror-root-only repository import path, so untracked or
unstaged validator code and transitive dependencies such as `docs_tooling`
cannot affect results.

### Mirror contract

- Modes `100644` and `100755` are materialized from stage-0 blob bytes, never
  from working-tree files.
- On POSIX, `100755` receives its executable bits. On hosts without a meaningful
  executable bit, the isolated index remains authoritative.
- Mode `120000` and paths having only nonzero stages remain in the isolated
  index but have no mirror worktree entry. The runner never creates or
  dereferences symlinks.
- Missing blobs, malformed records, duplicate stage-0 entries, unsupported
  modes, and materialization or isolated-Git failures raise
  `ValidatorRunnerError(RuntimeError)`.
- The runner never falls back to the source working tree and never silently
  skips a file or validator.

The existing blueprint validator remains the only validator allowed to inspect
`git ls-files --stage -z`. It owns index modes, conflict stages, and blueprint
synchronization checks. Move the `_cx` executable-mode assertion currently
based on `os.access` in `validators/skill_runtime_files.py` into that
index-aware validator; ordinary validators only walk the supplied mirror.

### Runner API

```python
def run_all(
    repo_root: Path = REPO_ROOT,
    validator_ids: Sequence[str] | None = None,
) -> dict[str, list[str]]: ...
```

Canonical validator IDs are `repo/<stem>` and `skill-maker/<stem>`. Result keys
use those IDs. `--validator` is repeatable and maps to `validator_ids`. IDs and
execution order are sorted. Unknown or duplicate selections, import failures,
missing or non-callable `validate`, and validator exceptions are runner errors;
validator findings remain dictionary values.

Direct hooks invoke the runner with a canonical ID, for example:

```text
validators/runner.py --validator skill-maker/blueprints
```

Tests cover partially staged files in both directions, modes, nonzero stages,
tracked symlinks, partially staged runner code, untracked validators, and live
transitive modules that must not execute.

## 2. Repository-path conversion

Equivalent-root handling already exists in `blueprint_graph.py`, but
`git_provenance.py` and `certification_view.py` have weaker duplicates.
Extract the shared semantics into
`src/officina/common/repository_paths.py` because importing them from
`blueprint_graph.py` would create a graph/inventory/Git-provenance cycle.

```python
class RepositoryPathError(ValueError): ...

def equivalent_root_relative_path(path: Path, root: Path) -> Path: ...

def repository_relative_path(path: Path, repo_root: Path) -> Path: ...

def repository_relative_posix(path: Path, repo_root: Path) -> str: ...
```

`equivalent_root_relative_path` owns lexical containment plus the existing
ancestor-`samefile` fallback for equivalent roots. It does not resolve or
follow descendants. Repository wrappers interpret relative inputs as
`repo_root / path`; they never use the process working directory.

Migrate the existing implementations in:

- `blueprint_graph.py`, including owner-root callers;
- `git_provenance.py`;
- `certification_view.py`;
- certification hashing and certifier serialization;
- dispatcher and Python-runner root conversions.

The helper replaces repository-relative conversion only. Existing module-root,
owner-root, source-root, and gateway confinement checks remain where they are.
Boundary callers preserve their current public error types or readiness
reasons.

The new source receives one common-module behavioral-source blueprint and the
corresponding module content, source, export, dependency, test, architecture,
and certification-basis updates. Its blueprint is already a node-hash input
and is not added separately to the global basis.

## 3. Structured Python process targets

Keep the Python-specific target in the existing adapter owner,
`src/officina/runtime/python_machine_interface.py`:

```python
class PythonProcessTargetError(ValueError): ...

@dataclass(frozen=True)
class PythonProcessTarget:
    gateway_path: Path
    process_entry: str
```

The gateway is module-root-relative, begins with `_rtx`, ends in `.py`, is not
absolute, and contains neither `.` nor `..`. The process entry is one nonempty
Python identifier.

Both `ResolvedInvocation` and `ResolvedInvocationMetadata` gain
`python_target: PythonProcessTarget | None`; `metadata()` copies it.
`ResolvedInvocationMetadata.as_payload()` emits:

```json
{
  "python_target": {
    "gateway_path": "_rtx/_worker.py",
    "process_entry": "Interface"
  }
}
```

Non-Python invocations emit `"python_target": null`. Trace keys become
`(skill_root, PythonProcessTarget)`. Child trace requests and responses use the
same nested object; responses additionally contain their existing `paths`.
Certification evidence uses the same representation.

The runner CLI receives two tokens:

```text
python_machine_interface_runner <gateway-path> <process-entry> [interface args...]
```

`command` remains launch serialization only. Runtime, dry-run, tracing, route
smoke, dependency loading, and certification never parse it to reconstruct the
target.

Provider-neutral blueprints continue to store `gateway.path` and
`process_binding.entry` separately. Migrate runtime code, live blueprint
permission arrays, generated projections, dry-run output, child transport,
certification evidence, and tests atomically. Historical composite parsing is
allowed only in an exact function allowlist for
`interface_injection_migration.py`; live code has no compatibility path.

## 4. Deterministic Git tests

Extract repeated local Git fixtures into `test_support/git_repository.py`:

```python
@dataclass(frozen=True)
class GitTestRepository:
    root: Path

    @classmethod
    def create(
        cls,
        root: Path,
        *,
        branch: str = "main",
        filemode: bool = True,
    ) -> GitTestRepository: ...

    def git(
        self,
        *args: str,
        check: bool = True,
        input_bytes: bytes | None = None,
    ) -> subprocess.CompletedProcess[bytes]: ...
```

`create` requires a nonexistent target directory, initializes exactly that
directory, returns its resolved path, and configures:

- `Famulus Tests <famulus-tests@example.invalid>`;
- `core.autocrlf=false`;
- explicit `core.filemode`;
- a stable initial branch.

`git()` delegates to production `git_provenance.run_git`. The helper never
stages or commits implicitly. Replace existing ordinary `_git` helpers and
repository fixtures rather than leaving parallel versions.

“Ordinary tests” means Python files under `tests/**` and
`skills/*/tests/**`. Only `test_support/git_repository.py` is unconditionally
allowed to call `run_git`. Elsewhere, a raw Git or direct `run_git` call
requires an annotation on the immediately preceding line:

```text
# famulus-raw-git: category=<category>; reason=<nonempty reason>
```

Closed categories are `ambient-config`, `hooks`, `object-format`,
`index-stages`, `validator-isolation`, and `run-git-contract`. The last covers
tests that instrument production `run_git` itself. Exemptions are statement
local: the comment binds only to the following AST statement. Changing the
category set requires changing the standard and validator together.

## 5. Mechanical enforcement

Extend existing owners instead of adding another portability validator:

- `validators/cross_platform.py` detects raw-Git test bypasses, live composite
  Python targets, and composite runner permission arrays in live v4 blueprints
  and generated projections.
- Its exact allowlists cover historical standard fixtures and the named
  migration-only parser.
- Existing focused validators retain TOML, dates, subprocess encoding,
  platform-neutral source placement, and skip hygiene.

Update the canonical standard without creating a new family:

- repository paths and Python targets extend `cross-platform-tools`;
- Git-test helper and exemptions extend `test-file-conventions`;
- sole-runner and tracked-mirror rules extend
  `validator-test-conventions`.

Regenerate only the live Markdown view. Historical migration fixtures remain
frozen.

## 6. Continuous enforcement

The same mechanical owner runs everywhere:

```text
pre-commit -> validator runner -> precommit tests
CI         -> validator runner -> portability sentinel -> full OS suite
certifier  -> validator runner -> semantic review -> hash and sign
```

Remove `_blueprint_sync_check`, the dispatcher parameter used only by that
check, `skip_mechanical`, and `--skip-mechanical`. Also remove the certifier's
now-unused `sync-blueprints` dispatch declaration and corresponding blueprint
use, dependency, and permission.

```python
def run_v4_mechanical_checks(
    repo_root: Path = REPO_ROOT,
) -> CommandResult: ...
```

It invokes the runner once. `certify()` preserves its existing evidence-list
and output-schema shape with
`evidence = [run_v4_mechanical_checks(repository)]`. Tests replace this
module-level function when isolation is required. Launch failure, missing
result, or nonzero result raises `CertificationError` before signing.

The existing blueprint validator owns the synchronization check; artifact
generation remains with the syncer.

### Fast portability sentinel

The `portability` suite is an intentional early-failure subset of the full
suite, not additional coverage. It runs in the existing OS matrix after
validators and before `full`; pre-commit does not run it separately.

| Boundary | Pytest node ID |
|---|---|
| Native atomic append | `tests/test_officina_atomic_files.py::test_secure_append_creates_then_appends_complete_framed_records` |
| Native Windows atomic path | `tests/test_officina_atomic_files.py::test_windows_native_secure_create_replace_append_and_acl` |
| Separated Python target | `tests/test_officina_dispatcher.py::test_python_process_target_keeps_gateway_and_entry_separate` |
| Hostile `core.autocrlf` | `tests/test_officina_git_provenance.py::test_git_test_repository_preserves_exact_bytes_under_ambient_autocrlf` |
| Foreign-platform artifact | `skills/recurring-tasks/tests/test_schedule_backend.py::test_linux_sync_writes_units_and_enables_timer` |
| Equivalent repository root | `tests/test_officina_blueprint_graph.py::test_content_ownership_accepts_equivalent_repository_alias` |
| Isolated index stages | `tests/test_validator_runner.py::test_run_all_isolates_unmerged_index_and_restores_git_environment` |

The separated-target test is added under that exact name; the other node IDs
already exist.

## 7. Certification basis and artifact order

Add only missing executed sources to
`skills/skill-drift/references/certification-basis-roots.json`:

- `references/standards/standard-v6.schema.json`;
- `references/standards/validate_standard_v6.py`;
- `references/standards/render_standard_v6.py`;
- `src/officina/common/repository_paths.py`;
- `docs_tooling/**/*.py`.

The existing validator glob and runtime entries already cover the validator and
Python target owner. Add a test that every repository module imported by a
validator is covered by the basis.

Apply changes in this order:

1. Add the path and Python-target owners with focused tests.
2. Migrate every live process target and Git test fixture.
3. Refactor the runner and move index-mode checking to the blueprint validator.
4. Enable the new cross-platform checks and update the canonical standard.
5. Regenerate the live standard view, blueprints, permissions, and tracked
   projections through their existing owners.
6. Add the sentinel and CI step, update `TESTING.md`, and run the complete
   Linux, macOS, and Windows matrix.
7. Update the basis, commit the exact source state, and recertify
   dependency-first against that commit.
8. Let certification regenerate ignored certificate logs and pooled reviews;
   verify them after certification.

## Non-goals

- No new policy file, standard family, portability validator, or certification
  mechanism.
- No functional or performance testing inside certification.
- No backward compatibility for live composite Python targets.
- No changes to historical migration fixtures.
- No change to plugin admission. Gitless plugin machine-interface dispatch
  remains unsupported until it receives a separate admission design.

## Completion criteria

The work is complete when:

1. validators execute tracked index bytes and tracked import closures only;
2. repository conversion and Python process targets each have one owner;
3. ordinary Git tests use one deterministic helper;
4. existing validators reject recognizable boundary bypasses;
5. hooks, CI, and certification run the same validator runner;
6. certification cannot skip or sign after failed mechanical conformance;
7. the sentinel and full Linux, macOS, and Windows suites pass; and
8. the standard, basis, generated artifacts, certificates, and pooled reviews
   match the final committed source state.
