# Generated Runtime Core Lock Design

## Scope

The first promoted release installs only core Python runtime dependencies.
`marker-pdf` and its OCR/ML closure are excluded from the supported installer
and from the release lock. The blueprints remain authoritative for skill-owned
direct dependencies.

## Architecture

`references/blueprint/runtime_dependencies.json` is pooled into a canonical
requirements input. Every distinct declared constraint is preserved with a
PEP 508 `sys_platform` marker when it is not portable across all three
supported operating-system families. Installer-owned build requirements are
added from one shared policy constant. No package is selected by declaration
order.

Pinned `uv 0.11.29` compiles that input in universal mode for the exact managed
Python 3.11 patch, with hashes. The generated input and lock are committed
under `references/runtime/`. A metadata header binds the lock to the input
digest, uv version, Python version, and SHA-256 digest of the complete compiled
body. Offline validation rejects generated-input drift, body-digest or header
mismatches, missing hashes, wildcard versions, and requirements that lack a
concrete `==` version.

The installer creates a venv with `--managed-python` and the exact patch,
installs the lock with `--require-hashes`, builds the first-party Officina
wheel, records its digest, and installs that wheel with `--no-deps`. Its
artifact record also stores the lock digest and resolved Python identity.

## Interfaces

- `officina.install.runtime_lock.render_runtime_requirements(...)` produces
  canonical generated input.
- `officina.install.runtime_lock.validate_runtime_lock(...)` validates input,
  metadata, exact pins, and hashes without network access.
- `scripts/generate-runtime-lock.py` writes or checks the generated input and
  invokes the exact pinned uv for intentional lock refreshes.
- `build_candidate_release(..., lock_input_path, lock_path, ...)` consumes only
  a validated core lock for third-party installation.

## Failure policy

Missing or stale lock files, a uv-version mismatch, an unpinned or unhashed
record, an incompatible resolution, or a request for optional dependencies
fails before activation. An existing active runtime pointer remains untouched.

## Minimal integrity closure

The lock header records `lock-content-sha256`, computed over the exact compiled
lock body after the Famulus metadata header. Offline validation recomputes that
digest and compares it with the header. This detects any changed,
added, removed, or reordered body record without adding a checksum sidecar or
another installer interface.

An exact requirement must use `==` followed by one concrete version. Wildcard
versions such as `==26.*` are invalid even though they use the equality
operator. Existing PEP 440 release, pre-release, post-release, development, and
local-version spellings remain valid.

Generation writes the canonical input and compiled lock only to temporary paths
until `uv pip compile` succeeds and the resulting lock passes full validation.
Only then does it replace the two canonical files. An ordinary
resolution or validation failure therefore leaves both published files
unchanged. Cross-file crash transactions, signing, attestations, and a separate
checksum artifact are outside the first-release scope.

Runtime installation remains fail-closed: a version paired with hashes for a
different artifact cannot pass `uv pip install --require-hashes`. The body
digest closes the earlier offline certification gap as well.

## Testing

Unit tests cover blueprint pooling, platform markers, exclusion of
`marker-pdf`, duplicate-constraint preservation, stale/malformed lock
rejection, managed-Python creation, hash-required installation, and the
optional-dependency rejection. Regression coverage proves rejection of
lock-record mutation, addition, removal, reordering, and wildcard pins, plus a
failed generation that leaves both canonical files unchanged. Existing
installer lifecycle and real-uv tests remain the integration boundary.
