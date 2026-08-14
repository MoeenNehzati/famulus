# Runtime Lock Integrity Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the two audited runtime-lock gaps with one in-file body digest, concrete exact-version validation, and failure-safe generation.

**Architecture:** `requirements-core.lock` remains the only lock artifact. Its metadata header records the SHA-256 of the exact compiled body; offline validation compares that recorded value with the body it reads. Generation compiles and validates temporary input and lock files before replacing either canonical file.

**Tech Stack:** Python standard library, uv `0.11.29`, CPython `3.11.15`, pytest, PEP 508 requirements files.

## Global Constraints

- Do not add a checksum sidecar, signing, SBOM, or another installer interface.
- Keep `references/blueprint/runtime_dependencies.json` authoritative for skill-owned direct dependencies.
- Keep `marker-pdf` and optional heavy dependencies outside the first supported lock.
- Preserve `uv pip install --require-hashes` and local-wheel `--no-deps` behavior.
- Limit production changes to `src/officina/install/runtime_lock.py` and the regenerated lock.

---

### Task 1: Bind and strictly validate the lock body

**Files:**
- Modify: `tests/test_officina_runtime_lock.py`
- Modify: `src/officina/install/runtime_lock.py`
- Modify: `references/runtime/requirements-core.lock`

**Interfaces:**
- Consumes: `validate_runtime_lock(...) -> RuntimeLockMetadata`
- Produces: required `lock-content-sha256` header and rejection of wildcard `==` versions

- [x] **Step 1: Write failing regression tests**

Update the test lock helper to record a body digest, then add parameterized tests that mutate, add, remove, and reorder body records without updating that digest. Add `examplepkg==1.*` to the invalid-requirement cases.

```python
body_sha256 = hashlib.sha256(records.encode("utf-8")).hexdigest()
f"# lock-content-sha256: {body_sha256}\n"

with pytest.raises(RuntimeLockError, match="content digest mismatch"):
    validate_runtime_lock(...)
```

- [x] **Step 2: Verify the tests fail for the audited reasons**

Run: `pytest -q tests/test_officina_runtime_lock.py`

Expected: body mutations are accepted and wildcard `==` is accepted by the current implementation.

- [x] **Step 3: Implement the minimal validation**

In `runtime_lock.py`, split the metadata header from the exact body text, require `lock-content-sha256`, compare it with `sha256(body.encode("utf-8"))`, and reject any exact-version token containing `*`.

```python
if headers.get("lock-content-sha256") != hashlib.sha256(body.encode("utf-8")).hexdigest():
    raise RuntimeLockError("runtime lock content digest mismatch")
```

- [x] **Step 4: Regenerate the committed lock and verify green**

Run: `scripts/generate-runtime-lock.py --uv /path/to/uv-0.11.29`

Run: `pytest -q tests/test_officina_runtime_lock.py`

Expected: regenerated lock contains the body digest and every test passes.

### Task 2: Preserve canonical files when generation fails

**Files:**
- Modify: `tests/test_officina_runtime_lock.py`
- Modify: `src/officina/install/runtime_lock.py`
- Modify: `docs/dependency-and-bootstrap-audit.md`
- Modify: `docs/officina/installation.md`
- Modify: `docs/plans/public-release-readiness.md`
- Modify: `docs/superpowers/specs/2026-08-13-runtime-core-lock-design.md`

**Interfaces:**
- Consumes: `generate_runtime_lock(...) -> RuntimeLockMetadata`
- Produces: canonical input and lock remain unchanged unless temporary compilation and validation both succeed

- [x] **Step 1: Write the failing generation test**

Create existing canonical input and lock sentinels, make the fake pinned uv return a nonzero compile result, and assert that both sentinel files remain byte-for-byte unchanged.

```python
input_path.write_bytes(b"old input\n")
lock_path.write_bytes(b"old lock\n")
with pytest.raises(RuntimeLockError, match="compilation failed"):
    generate_runtime_lock(...)
assert input_path.read_bytes() == b"old input\n"
assert lock_path.read_bytes() == b"old lock\n"
```

- [x] **Step 2: Verify the test fails because canonical input changes**

Run: `pytest -q tests/test_officina_runtime_lock.py::test_generate_runtime_lock_preserves_canonical_files_when_compile_fails`

Expected: the input sentinel is replaced before compilation fails.

- [x] **Step 3: Compile and validate only temporary files**

Pass `input_tmp` to `uv pip compile`, build the complete temporary lock with its body digest, call `validate_runtime_lock` on both temporary paths, then replace the canonical input and lock. Cleanup remains in `finally`.

- [x] **Step 4: Update the four existing documents**

Remove the release-blocker language only after tests pass. Document the body digest, strict wildcard rejection, and generate-before-publish behavior without claiming signing or cross-file crash transactions.

- [x] **Step 5: Run scoped and release-payload verification**

Run:

```bash
scripts/generate-runtime-lock.py --check
python3 -m pytest -q tests/test_officina_runtime_lock.py tests/test_officina_managed_runtime.py tests/test_officina_launcher_entry.py skills/install-assistant-tools/_rtx/tests/test_install.py
python3 repo_checks.py --suite validators --validator repo/platform_neutral --validator repo/skill_runtime_files --validator skill-maker/blueprints --jobs 1
```

Expected: all commands exit zero; only explicitly documented capability or ownership-aware uninstall skips remain.

- [x] **Step 6: Verify and commit only the integrity closure**

Run `git diff --check`, inspect the exact staged names, and commit without `--no-verify` using `fix: bind runtime lock content`.
