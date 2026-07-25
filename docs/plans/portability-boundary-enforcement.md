# Portability Boundary Enforcement

## Status

Approved design. This document defines the smallest repository changes needed
to prevent the cross-platform failure classes exposed by the v4 certification
migration. It does not introduce a second conformance system.

## Goal

Make portable repository behavior the default by giving repeated boundary
semantics one owner, requiring ordinary code and tests to use those owners, and
running the same mechanical validators during local development, CI, and
certification.

## Principles

1. Centralize semantics, not merely syntax. A helper is justified only when it
   owns a repeated behavioral contract.
2. Keep the canonical skill standard as the policy source. Generated Markdown
   remains a view of that standard.
3. Enforce only mechanically recognizable violations with static validators.
   Semantic blueprint correctness remains the certifier's responsibility.
4. Use the existing validator runner everywhere. Do not create a parallel
   portability checker or certification mechanism.
5. Preserve native filesystem path types internally. Convert to POSIX text only
   at serialization boundaries.
6. Prefer structured values over composite strings that must later be parsed.
7. Keep exceptional low-level tests possible through narrow, documented
   exemptions.

## Design

### 1. One validator execution boundary

`validators/runner.py` remains the sole command-line entry point for ordinary
repository validators. It:

- constructs the Git-index-backed mirror;
- provides that mirror to validators;
- supports selecting one validator for focused hooks or tests;
- fails closed when the tracked mirror or its isolated Git metadata cannot be
  constructed.

Ordinary validators walk the supplied root and do not run `git ls-files`
themselves. The blueprint validator may retain `git ls-files --stage -z`
because index mode, stage, and conflict records are its subject rather than an
enumeration mechanism. Direct validator hooks call the runner's selected mode,
not validator modules directly.

### 2. One repository-path owner

Create `src/officina/common/repository_paths.py` to own equivalent-root-aware
repository path conversion:

```python
class RepositoryPathError(ValueError): ...

def repository_relative_path(path: Path, repo_root: Path) -> Path: ...

def repository_relative_posix(path: Path, repo_root: Path) -> str: ...
```

Inputs are absolute paths or paths interpreted relative to `repo_root`.
`repository_relative_path` returns the host-native `Path`.
`repository_relative_posix` is used only for serialized repository
identifiers. Equivalent filesystem roots such as macOS `/Users` and
`/private/Users` resolve consistently.

Graph, dispatcher, runner, hashing, certification, and Git-provenance callers
reuse this owner and translate `RepositoryPathError` into their established
boundary-specific errors. This new common source receives the normal v4
blueprint, graph, certification-basis, tests, and recertification treatment.

### 3. Structured Python process targets

The Python process adapter carries the gateway path and Python entry separately:

```python
@dataclass(frozen=True)
class PythonProcessTarget:
    gateway_path: Path
    process_entry: str
```

The dispatcher, resolved invocation metadata, dependency tracing, route smoke,
dry-run output, runner transport, and certification evidence consume these
fields directly. No live path constructs or reparses
`"_rtx/file.py:ClassName"`.

The runner command accepts two fixed positional values after its private
transport options:

```text
python_machine_interface_runner <gateway-path> <process-entry> [interface args...]
```

Python-specific entry validation has one owner and is shared by dispatch,
tracing, and certification. Provider-neutral blueprint schemas continue to
store `gateway.path` and `process_binding.entry` separately. Historical
migration-only parsing may remain if explicitly named as legacy behavior; live
blueprints, permissions, generated projections, and runtime paths migrate
atomically.

### 4. Deterministic Git repositories in tests

Add one repository-visible, test-only Git helper that works with pytest and
`unittest` callers. It:

- initializes one repository per test;
- delegates Git execution to production `git_provenance.run_git`;
- fixes the initial branch and user identity;
- sets `core.autocrlf=false`;
- sets `core.filemode` explicitly according to the test contract;
- uses bytes-oriented results;
- never adds or commits implicitly.

Ordinary tests use this helper. Tests whose subject is ambient Git behavior,
hooks, object format, conflict-index state, or validator isolation may invoke
raw Git only beside a narrow annotation:

```text
# famulus-raw-git: category=<category>; reason=<specific reason>
```

Exemptions are call-local, not file-wide.

### 5. Narrow mechanical enforcement

Extend the canonical `cross-platform-tools` standard family with assertions
that name the repository path owner, structured process-target boundary,
deterministic Git-test helper, and sole validator runner.

Add one focused AST validator for reliable bypasses:

- raw Git subprocess calls in ordinary tests;
- direct ordinary-test calls to production `run_git`;
- ordinary validators invoking `git ls-files`;
- live construction or parsing of composite Python process targets;
- invalid or missing raw-Git exemption annotations.

The validator does not attempt general semantic path inference, atomic-write
policy inference, or blueprint correctness. Existing focused validators retain
their current domains.

Update the live canonical YAML and regenerate only its live Markdown view.
Historical migration fixtures remain frozen evidence.

### 6. Continuous enforcement

All development paths use the same mechanical owner:

```text
pre-commit -> validator runner -> precommit tests
CI         -> validator runner -> portability suite -> full OS suite
certifier  -> validator runner -> semantic LLM audit -> hash and sign
```

Certification removes the public and programmatic `skip_mechanical` bypass.
Tests mock the mechanical-check boundary when isolation is required.

The validator runner owns blueprint synchronization. The certifier removes its
separate blueprint-sync dispatch so synchronization is checked once. A
certification behavior test proves that a validator or blueprint-sync failure
prevents signing.

Add an explicit `portability` suite to `scripts/run-python-tests.py`. It uses
exact existing pytest node IDs and runs in the existing OS matrix after
validators and before the full suite. It covers:

- native atomic create, replace, and append;
- separated Python process targets;
- hostile ambient `core.autocrlf`;
- foreign-platform artifact rendering;
- equivalent-root repository paths;
- blueprint index mode and stage isolation.

The pre-commit hook does not run this suite separately because its broader
precommit suite already includes the same tests.

### 7. Certification basis and generated artifacts

The certification basis includes every source executed to enforce or render
the canonical standard, including:

- `references/standards/standard-v6.schema.json`;
- `references/standards/validate_standard_v6.py`;
- `references/standards/render_standard_v6.py`;
- the new repository-path source and its blueprint.

Live blueprint projections, runner permission arguments, pooled reviews, and
certificates are regenerated through their existing owners rather than edited
by hand. The final exact commit is recertified dependency-first across the
repository.

## Scope

This work changes only repeated portability boundaries uncovered by the
cross-platform certification failures. It does not:

- create another policy file or portability framework;
- replace existing TOML, date, subprocess, platform-neutral, or skip validators;
- broaden certification into runtime-performance testing;
- change provider-neutral interface schema semantics;
- preserve backward compatibility for live composite Python targets;
- rewrite historical migration fixtures;
- require certification in plugin mode.

## Completion criteria

The design is complete when:

1. ordinary validators have no independent tracked-file enumeration;
2. live repository path conversion and Python process targets have one owner;
3. ordinary Git-backed tests are deterministic across supported hosts;
4. narrow validators reject recognizable boundary bypasses;
5. local hooks, CI, and certification execute the same validator runner;
6. certification cannot skip mechanical conformance or sign after its failure;
7. the fast portability suite and the full Linux, macOS, and Windows suites pass;
8. standards, basis files, generated artifacts, and certificates match the
   final committed repository state.
