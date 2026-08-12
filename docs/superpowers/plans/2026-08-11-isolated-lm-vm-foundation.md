# Isolated LM VM Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create and manually validate a disposable, Famulus-free Ubuntu VM from a verified cloud image using direct QEMU/KVM.

**Architecture:** Repository-owned Python tooling verifies the host, acquires and authenticates an Ubuntu cloud image, creates a QCOW2 overlay and NoCloud seed, and controls QEMU through explicit subprocess arguments. The guest is reachable only through an SSH port forwarded to host loopback; no maintainer checkout or host directory is mounted into it.

**Tech Stack:** Python 3.11+ standard library, pytest, QEMU/KVM, QCOW2, cloud-init/NoCloud, OpenSSH, Ubuntu Server 24.04 LTS amd64.

## Global Constraints

- The first supported host is Ubuntu 25.10 on x86-64 with usable KVM acceleration.
- Install only `cpu-checker`, `qemu-system-x86`, `qemu-utils`, `cloud-image-utils`, and `ubuntu-cloudimage-keyring`; require existing `curl`, `gpgv`, `sha256sum`, `ssh`, and `ssh-keygen` commands.
- The guest is Ubuntu Server 24.04 LTS amd64 with four virtual CPUs, 8 GiB RAM, and a 40 GiB sparse QCOW2 disk.
- Use direct QEMU/KVM, QEMU user-mode networking, and a loopback-only SSH forward. Do not use Docker, libvirt, a bridge/tap interface, or a host filesystem mount.
- Runtime images, keys, manifests, sockets, and logs live outside the repository under an explicitly supplied state root.
- The source image, signed checksum files, package versions, QEMU arguments, and resulting image digests are evidence and must be recorded without secrets.
- The VM baseline must not contain Famulus, `uv`, a Famulus-managed Python, an Officina wheel, Famulus configuration, provider credentials, private guidance, or prior scenario state.
- Do not add a third-party Python dependency. Use dependency injection around filesystem, network, and subprocess boundaries so unit tests do not require KVM, root, or internet access.
- Give every new module, class, and nontrivial function the repository-required intent, rationale, pseudocode, and call-boundary documentation; comment security and lifecycle invariants that are not obvious from the code.
- Do not commit, push, or install host packages without explicit user approval at the relevant checkpoint.

## Scope Boundary

This plan implements the first independently testable slice of Workstream 1: host preflight, trusted image acquisition, guest seed and overlay creation, VM lifecycle, and one manual boot/SSH/disposal acceptance run. A follow-up plan will authenticate and seal Codex, enforce the Famulus-free guest preflight, and version the sealed baseline. A second follow-up will implement immutable candidate/documentation injection and sanitized evidence extraction. Scenario execution and provider fixtures remain in later workstreams.

## File Structure

- `test_support/isolated_lm/model.py` — immutable resource, path, image, and run records shared by the harness.
- `test_support/isolated_lm/host.py` — host command/package/KVM preflight with injectable boundaries.
- `test_support/isolated_lm/image.py` — signed checksum verification, atomic source-image acquisition, hashing, and QCOW2 overlay creation.
- `test_support/isolated_lm/guest.py` — deterministic cloud-init user/meta data and NoCloud seed generation.
- `test_support/isolated_lm/qemu.py` — loopback port allocation, QEMU command construction, launch, SSH readiness, and shutdown.
- `test_support/isolated_lm/cli.py` — the supported orchestration interface and JSON result transport.
- `scripts/isolated-lm-vm.py` — thin executable repository entrypoint.
- `tests/test_isolated_lm_*.py` — unit and orchestration-contract coverage with no live virtualization requirement.
- `docs/isolated-lm-testing.md` — operator commands, state layout, trust boundary, and manual acceptance procedure.

---

### Task 1: Runtime model and host preflight

**Files:**
- Create: `test_support/isolated_lm/__init__.py`
- Create: `test_support/isolated_lm/model.py`
- Create: `test_support/isolated_lm/host.py`
- Create: `tests/test_isolated_lm_host.py`

**Interfaces:**
- Consumes: an explicit state root and injectable command/path/open functions.
- Produces: `VmResources`, `RuntimePaths`, `CheckResult`, `HostPreflightReport`, and `check_host() -> HostPreflightReport`.

- [x] **Step 1: Write failing model and preflight tests**

Create tests that require normalized paths, the approved resource defaults, all required commands, and a read/write KVM probe. Use injected fakes; never inspect the test runner's real `/dev/kvm`.

```python
def test_runtime_paths_are_derived_only_from_explicit_root(tmp_path: Path) -> None:
    paths = RuntimePaths.from_root(tmp_path / "state")
    assert paths.root == (tmp_path / "state").resolve()
    assert paths.downloads == paths.root / "downloads"
    assert paths.images == paths.root / "images"
    assert paths.runs == paths.root / "runs"


def test_host_preflight_requires_commands_and_writable_kvm() -> None:
    commands = {
        name: f"/usr/bin/{name}"
        for name in REQUIRED_COMMANDS
        if name != "cloud-localds"
    }
    report = check_host(
        which=commands.get,
        open_kvm=lambda: (_ for _ in ()).throw(PermissionError("denied")),
        platform_name=lambda: "Linux-6-test-x86_64-with-glibc",
        machine=lambda: "x86_64",
    )
    assert not report.ok
    assert report.by_name("command:cloud-localds").detail == "not found"
    assert report.by_name("kvm:read-write").detail == "denied"
```

- [x] **Step 2: Run the new tests and verify RED**

Run: `python3 -m pytest -q tests/test_isolated_lm_host.py`

Expected: FAIL during import because `test_support.isolated_lm` does not exist.

- [x] **Step 3: Implement immutable records and the preflight**

Use frozen dataclasses. `RuntimePaths.from_root` must reject a relative root. `check_host` must return every result rather than stopping at the first failure.

```python
REQUIRED_COMMANDS = (
    "kvm-ok",
    "qemu-system-x86_64",
    "qemu-img",
    "cloud-localds",
    "gpgv",
    "sha256sum",
    "ssh",
    "ssh-keygen",
)


@dataclass(frozen=True)
class VmResources:
    vcpus: int = 4
    memory_mib: int = 8192
    disk_gib: int = 40


@dataclass(frozen=True)
class RuntimePaths:
    root: Path
    downloads: Path
    images: Path
    runs: Path

    @classmethod
    def from_root(cls, root: Path) -> "RuntimePaths":
        if not root.is_absolute():
            raise ValueError("state root must be absolute")
        resolved = root.resolve()
        return cls(resolved, resolved / "downloads", resolved / "images", resolved / "runs")


def _open_kvm() -> None:
    descriptor = os.open("/dev/kvm", os.O_RDWR | getattr(os, "O_CLOEXEC", 0))
    os.close(descriptor)
```

Record `platform.platform()`, `platform.machine()`, resolved executable paths, and KVM usability. Reject non-Linux and non-`x86_64` hosts in this first profile.

- [x] **Step 4: Run focused and policy tests**

Run: `python3 -m pytest -q tests/test_isolated_lm_host.py tests/validate_platform_neutral.py`

Expected: PASS.

- [x] **Step 5: Commit the task after approval**

```bash
git add test_support/isolated_lm/__init__.py test_support/isolated_lm/model.py test_support/isolated_lm/host.py tests/test_isolated_lm_host.py
git commit -m "test: add isolated VM host preflight"
```

---

### Task 2: Trusted Ubuntu cloud-image acquisition

**Files:**
- Modify: `test_support/isolated_lm/model.py`
- Create: `test_support/isolated_lm/image.py`
- Create: `tests/test_isolated_lm_image.py`

**Interfaces:**
- Consumes: `RuntimePaths`, the trusted keyring `/usr/share/keyrings/ubuntu-cloudimage-keyring.gpg`, and `https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img`, `https://cloud-images.ubuntu.com/noble/current/SHA256SUMS`, and `https://cloud-images.ubuntu.com/noble/current/SHA256SUMS.gpg`.
- Produces: `CloudImageRecord`, `parse_sha256sums()`, `verify_signed_checksums()`, `download_atomic()`, `sha256_file()`, `prepare_cloud_image()`, and `create_overlay()`.

- [x] **Step 1: Write checksum, signature-command, and atomic-download tests**

Cover exact filename matching, malformed and duplicate entries, digest mismatch, non-HTTPS URLs, wrong hosts, temporary-file cleanup, and an exact `gpgv` argument vector.

```python
def test_parse_sha256sums_selects_exact_image_once() -> None:
    text = f"{'a' * 64} *noble-server-cloudimg-amd64.img\n"
    assert parse_sha256sums(text, "noble-server-cloudimg-amd64.img") == "a" * 64


def test_verify_signed_checksums_uses_only_trusted_keyring(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    verify_signed_checksums(
        tmp_path / "SHA256SUMS",
        tmp_path / "SHA256SUMS.gpg",
        Path("/usr/share/keyrings/ubuntu-cloudimage-keyring.gpg"),
        run=lambda argv, **kwargs: calls.append(argv) or CompletedProcess(argv, 0),
    )
    assert calls == [[
        "gpgv", "--keyring", "/usr/share/keyrings/ubuntu-cloudimage-keyring.gpg",
        str(tmp_path / "SHA256SUMS.gpg"), str(tmp_path / "SHA256SUMS"),
    ]]
```

- [x] **Step 2: Verify the image tests fail**

Run: `python3 -m pytest -q tests/test_isolated_lm_image.py`

Expected: FAIL because `test_support.isolated_lm.image` is absent.

- [x] **Step 3: Implement authenticated acquisition and hashing**

Use `urllib.request.urlopen`, `hashlib.sha256`, `tempfile.NamedTemporaryFile`, `os.replace`, and `subprocess.run(check=True)`. Accept only the approved scheme/host/path prefix. Verify the detached signature before trusting the checksum entry; verify the image bytes before moving the temporary download into the image cache.

```python
ALLOWED_IMAGE_ORIGIN = ("https", "cloud-images.ubuntu.com")
IMAGE_FILENAME = "noble-server-cloudimg-amd64.img"
KEYRING = Path("/usr/share/keyrings/ubuntu-cloudimage-keyring.gpg")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_signed_checksums(checksums: Path, signature: Path, keyring: Path, *, run=subprocess.run) -> None:
    run(
        ["gpgv", "--keyring", str(keyring), str(signature), str(checksums)],
        check=True,
    )
```

`CloudImageRecord` must include schema version, source URLs, exact filename, verified source digest, byte size, retrieval time in UTC, and resolved cached path. Serialize JSON with sorted keys and a final newline.

- [x] **Step 4: Implement QCOW2 overlay creation**

Require an absent destination and an absolute verified backing image. Invoke exactly:

```python
[
    "qemu-img", "create", "-f", "qcow2", "-F", "qcow2",
    "-b", str(backing_image), str(overlay), f"{resources.disk_gib}G",
]
```

Return the created overlay path. Task 3 records that path together with the
`CloudImageRecord` backing-image digest when it introduces `RunRecord`; never
hash an empty or partially downloaded file as success.

- [x] **Step 5: Run image tests and validators**

Run: `python3 -m pytest -q tests/test_isolated_lm_image.py tests/test_isolated_lm_host.py`

Run: `./repo_checks.py --suite validators`

Expected: both commands PASS.

- [x] **Step 6: Commit the task after approval**

```bash
git add test_support/isolated_lm/model.py test_support/isolated_lm/image.py tests/test_isolated_lm_image.py
git commit -m "feat: verify isolated VM source images"
```

---

### Task 3: Deterministic guest seed and disposable run disk

**Files:**
- Modify: `test_support/isolated_lm/model.py`
- Create: `test_support/isolated_lm/guest.py`
- Create: `tests/test_isolated_lm_guest.py`

**Interfaces:**
- Consumes: a verified `CloudImageRecord`, an explicit run ID, a single-line Ed25519 SSH public key, `RuntimePaths`, and `VmResources`.
- Produces: `RunRecord`, `validate_run_id()`, `render_user_data()`, `render_meta_data()`, `write_nocloud_seed()`, and `prepare_run()`.

- [x] **Step 1: Write failing seed-content and confinement tests**

Require a stable guest user named `famulus-test`, locked passwords, passwordless guest-local sudo, the approved package floor, no host path or secret interpolation, and all run artifacts confined below `RuntimePaths.runs`.

```python
def test_user_data_contains_only_generic_guest_prerequisites() -> None:
    rendered = render_user_data("ssh-ed25519 AAAATEST isolated-lm")
    assert "name: famulus-test" in rendered
    assert "lock_passwd: true" in rendered
    assert "openssh-server" in rendered
    assert "ca-certificates" in rendered
    assert "curl" in rendered
    assert "python3" in rendered
    for forbidden in ("Famulus", "officina", "uv", "/maintainer/checkout", "private guidance"):
        assert forbidden not in rendered


def test_prepare_run_rejects_path_like_run_id(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="run ID"):
        validate_run_id("../escape")
```

- [x] **Step 2: Verify the guest tests fail**

Run: `python3 -m pytest -q tests/test_isolated_lm_guest.py`

Expected: FAIL because `test_support.isolated_lm.guest` is absent.

- [x] **Step 3: Render deterministic NoCloud inputs**

Accept run IDs matching `^[a-z0-9][a-z0-9-]{0,62}$` and public keys matching one non-empty line beginning `ssh-ed25519 `. Render `#cloud-config` with `package_update: true`, the four approved guest packages, and this user contract:

```yaml
users:
  - name: famulus-test
    groups: [sudo]
    shell: /bin/bash
    lock_passwd: true
    sudo: ALL=(ALL) NOPASSWD:ALL
    ssh_authorized_keys:
      - {public_key}
ssh_pwauth: false
disable_root: true
```

`meta-data` contains only `instance-id: isolated-lm-<run-id>` and `local-hostname: isolated-lm-<run-id>`. Write both files with mode `0600` and invoke:

```python
["cloud-localds", str(seed_iso), str(user_data), str(meta_data)]
```

- [x] **Step 4: Prepare a complete run directory**

Create `<state>/runs/<run-id>/`, then its overlay, seed, dedicated `known_hosts`, serial log, QMP socket path, PID path, and `run.json`. Refuse reuse of any existing run directory. `RunRecord` must include resource values, source-image digest, overlay/seed paths, SSH user, creation timestamp, and lifecycle state `prepared`.

Freeze the record schema as: schema version, run ID and run directory,
`VmResources`, source-image digest, overlay, seed ISO, `known_hosts`, serial log,
QMP socket, PID file, record path, SSH user, UTC creation timestamp, lifecycle,
optional SSH port, optional identity-file path, and the QEMU argument vector.
Task 3 leaves the SSH port and identity file unset and the argument vector empty;
Task 4 receives the private-key path and fills those launch-time fields. Serialize
paths as absolute strings, resources as an object, and the argument vector as a
JSON list, with sorted keys and a final newline.

- [x] **Step 5: Run guest and image tests**

Run: `python3 -m pytest -q tests/test_isolated_lm_guest.py tests/test_isolated_lm_image.py`

Expected: PASS.

- [x] **Step 6: Commit the task after approval**

```bash
git add test_support/isolated_lm/model.py test_support/isolated_lm/guest.py tests/test_isolated_lm_guest.py
git commit -m "feat: prepare disposable isolated VM runs"
```

---

### Task 4: QEMU launch, SSH readiness, and bounded shutdown

**Files:**
- Modify: `test_support/isolated_lm/model.py`
- Create: `test_support/isolated_lm/qemu.py`
- Create: `tests/test_isolated_lm_qemu.py`

**Interfaces:**
- Consumes: a prepared `RunRecord`, its private SSH key, and injected socket/subprocess/time functions.
- Produces: `allocate_loopback_port()`, `build_qemu_command()`, `build_ssh_command()`, `start_run()`, `wait_for_ssh()`, and `stop_run()`.

- [x] **Step 1: Write failing exact-command and lifecycle tests**

Assert KVM acceleration, loopback-only forwarding, no mount/sharing devices, dedicated PID/QMP/serial paths, strict key confinement, and bounded polling.

```python
def test_qemu_command_has_only_approved_devices(prepared_run: RunRecord) -> None:
    command = build_qemu_command(prepared_run, ssh_port=40222)
    joined = " ".join(command)
    assert command[0] == "qemu-system-x86_64"
    assert "-machine q35,accel=kvm" in joined
    assert "-cpu host" in joined
    assert "hostfwd=tcp:127.0.0.1:40222-:22" in joined
    assert "virtio-net-pci" in joined
    assert "virtiofs" not in joined
    assert "9p" not in joined


def test_ssh_uses_dedicated_host_key_and_identity(prepared_run: RunRecord) -> None:
    command = build_ssh_command(prepared_run, ["true"])
    assert "UserKnownHostsFile=" + str(prepared_run.known_hosts) in command
    assert "StrictHostKeyChecking=accept-new" in command
    assert "IdentitiesOnly=yes" in command
```

- [x] **Step 2: Verify the QEMU tests fail**

Run: `python3 -m pytest -q tests/test_isolated_lm_qemu.py`

Expected: FAIL because `test_support.isolated_lm.qemu` is absent.

- [x] **Step 3: Build the exact QEMU boundary**

Construct arguments without a shell:

```python
[
    "qemu-system-x86_64",
    "-name", f"isolated-lm-{run.run_id}",
    "-machine", "q35,accel=kvm",
    "-cpu", "host",
    "-smp", str(run.resources.vcpus),
    "-m", str(run.resources.memory_mib),
    "-drive", f"file={run.overlay},if=virtio,format=qcow2",
    "-drive", f"file={run.seed_iso},if=virtio,format=raw,readonly=on",
    "-netdev", f"user,id=net0,hostfwd=tcp:127.0.0.1:{ssh_port}-:22",
    "-device", "virtio-net-pci,netdev=net0",
    "-display", "none",
    "-serial", f"file:{run.serial_log}",
    "-qmp", f"unix:{run.qmp_socket},server=on,wait=off",
    "-pidfile", str(run.pid_file),
    "-daemonize",
]
```

Reject commas in every dynamic value embedded in QEMU's comma-delimited
arguments before persistence or launch. OpenSSH ultimately passes its remote
command through a remote shell, so encode the remote argument vector as one
POSIX-quoted command string (for example with `shlex.join`) while continuing to
invoke the local `ssh` process without a shell.

Allocate the SSH port by binding an IPv4 loopback socket to port zero, reading the assigned port, and closing immediately before launch. Record the port, identity, full argument vector, and lifecycle `launching` in `run.json` before the bounded QEMU invocation. Only a verified successful invocation becomes `running`. On timeout, exception, or nonzero exit, scan `/proc` for the exact QEMU executable, VM name, and overlay path: retain `launching` when one exact process exists, record `launch-failed` only when none exists, and fail closed when multiple exact processes exist.

The private-key path is a Task 4 input. Record it together with the allocated
SSH port and command by replacing the frozen `RunRecord` and atomically
rewriting `run.json`; never infer it from the public key supplied to Task 3.
Require the identity file to be absolute, resolved, non-symlinked, regular,
owner-readable, and inaccessible to group/other users. It need not live under
the run directory.

- [x] **Step 4: Implement bounded SSH readiness and shutdown**

Poll `ssh ... true` until a monotonic deadline. On readiness, run `cloud-init status --wait`, require exit zero, and mark the run `ready`. `stop_run` first invokes `sudo -n poweroff`, waits for the PID to disappear, then uses QMP `quit` only if the bounded graceful timeout expires. It must never signal a PID until `/proc/<pid>/cmdline` contains the exact run's QEMU name and overlay path.

`start_run` persists the port, identity, command, and `launching` lifecycle
before QEMU, then records `running` only after QEMU exits successfully. SSH or
cloud-init timeout/failure leaves a live VM in `running` and raises. Shutdown
uses an exact `/proc` scan rather than trusting the PID file: a missing,
malformed, or reused PID can recover one exact process; no exact match proves
absence; multiple matches fail closed. Resolve process identity before SSH and
QMP, and record `stopped` only after a final exact scan proves absence. After
graceful timeout, perform a bounded QMP capabilities/quit exchange and a second
bounded disappearance wait; otherwise retain the prior state and raise.

Treat QMP as a framed stream: retain partial bytes until a newline, preserve
trailing frames, ignore asynchronous events, and correlate capabilities/quit
responses with distinct request IDs. One monotonic deadline must bound connect,
greeting, sends, and replies; per-operation socket timeouts use only the
remaining budget.

- [x] **Step 5: Run lifecycle tests**

Run: `python3 -m pytest -q tests/test_isolated_lm_qemu.py tests/test_isolated_lm_guest.py`

Expected: PASS with no QEMU process launched.

- [x] **Step 6: Commit the task after approval**

```bash
git add test_support/isolated_lm/model.py test_support/isolated_lm/qemu.py tests/test_isolated_lm_qemu.py
git commit -m "feat: control isolated QEMU VM lifecycle"
```

---

### Task 5: Supported CLI and operator documentation

**Files:**
- Create: `test_support/isolated_lm/cli.py`
- Create: `scripts/isolated-lm-vm.py`
- Create: `tests/test_isolated_lm_cli.py`
- Create: `docs/isolated-lm-testing.md`
- Modify: `docs/plans/isolated-llm-testing.md`

**Interfaces:**
- Consumes: Tasks 1-4 and an explicit `--state-root` on every command.
- Produces: JSON-emitting `preflight`, `prepare-image`, `prepare-run`, `start-run`, `exec`, `stop-run`, and `status` commands.

- [x] **Step 1: Write failing CLI contract tests**

Load `scripts/isolated-lm-vm.py` by exact path and require all subcommands, absolute state-root validation, JSON on stdout, diagnostics on stderr, nonzero exit for failed preflight, and no mutation from `status`.

```python
def test_parser_exposes_only_supported_commands() -> None:
    parser = build_parser()
    subparsers = next(
        action for action in parser._actions if isinstance(action, argparse._SubParsersAction)
    )
    assert set(subparsers.choices) == {
        "preflight", "prepare-image", "prepare-run", "start-run", "exec", "stop-run", "status"
    }
```

- [x] **Step 2: Verify the CLI tests fail**

Run: `python3 -m pytest -q tests/test_isolated_lm_cli.py`

Expected: FAIL because the CLI files are absent.

- [x] **Step 3: Implement the thin entrypoint and orchestration CLI**

The executable wrapper inserts the repository root into `sys.path` and calls `test_support.isolated_lm.cli.main()`. Each mutating command writes its manifest before emitting it. `status` reads only the selected run manifest and verifies paths remain under the state root.

Use these stable invocations in tests and documentation:

```text
ISOLATED_LM_STATE="${XDG_STATE_HOME:-${HOME}/.local/state}/famulus/isolated-lm-testing"
./scripts/isolated-lm-vm.py preflight --state-root "$ISOLATED_LM_STATE"
./scripts/isolated-lm-vm.py prepare-image --state-root "$ISOLATED_LM_STATE"
./scripts/isolated-lm-vm.py prepare-run --state-root "$ISOLATED_LM_STATE" --run-id manual-001 --ssh-public-key "$ISOLATED_LM_STATE/keys/isolated-lm.pub"
./scripts/isolated-lm-vm.py start-run --state-root "$ISOLATED_LM_STATE" --run-id manual-001 --ssh-private-key "$ISOLATED_LM_STATE/keys/isolated-lm"
./scripts/isolated-lm-vm.py exec --state-root "$ISOLATED_LM_STATE" --run-id manual-001 --ssh-private-key "$ISOLATED_LM_STATE/keys/isolated-lm" -- cloud-init status --long
./scripts/isolated-lm-vm.py status --state-root "$ISOLATED_LM_STATE" --run-id manual-001
./scripts/isolated-lm-vm.py stop-run --state-root "$ISOLATED_LM_STATE" --run-id manual-001 --ssh-private-key "$ISOLATED_LM_STATE/keys/isolated-lm"
```

`exec` must require a non-empty argument vector after `--`, pass those arguments
directly to SSH without a local shell, and return the guest exit code, stdout,
and stderr in its JSON result. Add `--timeout-seconds` (default 300, maximum
3600) and `--max-output-bytes` (default 1 MiB per stream, maximum 16 MiB).
Drain both streams concurrently, retain only their configured prefixes, and
publish stable timeout and truncation facts after deterministic kill/reap.

- [x] **Step 4: Write the operator guide**

Document:

1. the exact package install command;
2. KVM group/re-login handling only when preflight reports a permission failure;
3. state-root selection outside the checkout;
4. SSH key generation;
5. each supported CLI command;
6. the state layout and manifest fields;
7. outbound NAT and loopback-forward limits;
8. recovery after download, launch, SSH, or shutdown failure;
9. the fact that Codex authentication and sealing are the next plan, not part of this foundation.

Update Workstream 1 checklist status only for capabilities actually implemented and verified; do not mark Codex authentication, sealed-baseline preflight, candidate injection, or report extraction complete.

- [x] **Step 5: Run CLI, documentation, and canonical checks**

Run: `python3 -m pytest -q tests/test_isolated_lm_host.py tests/test_isolated_lm_image.py tests/test_isolated_lm_guest.py tests/test_isolated_lm_qemu.py tests/test_isolated_lm_cli.py`

Run: `./repo_checks.py --suite validators`

Run: `git diff --check`

Expected: all commands PASS.

#### Final review hardening

- [x] Persist crash-recoverable `launching` authority before QEMU invocation.
- [x] Recover exact QEMU identity through `/proc` when PID evidence is absent or stale.
- [x] Bound exec time and retained output without changing the seven-command surface.
- [x] Authenticate staged checksum evidence before canonical publication.
- [x] Bound network acquisition and QEMU launch operations.
- [x] Execute bounded `kvm-ok` in addition to checking `/dev/kvm` access.
- [x] Fsync manifest/evidence parent directories after atomic replacement.

- [x] **Step 6: Commit the task after approval**

```bash
git add test_support/isolated_lm/cli.py scripts/isolated-lm-vm.py tests/test_isolated_lm_cli.py docs/isolated-lm-testing.md docs/plans/isolated-llm-testing.md
git commit -m "docs: add isolated VM operator workflow"
```

---

### Task 6: Install host dependencies and manually validate one VM

**Files:**
- Modify only if live evidence exposes a defect: files owned by Tasks 1-5 and their exact tests.
- Evidence outside Git: the explicit state root's signed checksum files, source image, run manifest, serial log, and QCOW2 overlay.

**Interfaces:**
- Consumes: the supported CLI, Ubuntu package manager, KVM device, and internet access to Ubuntu cloud images.
- Produces: one stopped run whose manifest proves verified image acquisition, KVM launch, cloud-init completion, SSH access, resource allocation, and bounded shutdown.

- [ ] **Step 1: Obtain explicit approval and install only the approved packages**

Run outside the Codex sandbox:

```bash
sudo apt update
sudo apt install cpu-checker qemu-system-x86 qemu-utils cloud-image-utils ubuntu-cloudimage-keyring
```

Expected: all packages install successfully. Do not install libvirt, Docker, or optional virtualization managers.

- [ ] **Step 2: Verify KVM and create host-only harness credentials**

Run:

```bash
kvm-ok
ISOLATED_LM_STATE="${XDG_STATE_HOME:-${HOME}/.local/state}/famulus/isolated-lm-testing"
mkdir -p "$ISOLATED_LM_STATE/keys"
ssh-keygen -t ed25519 -N '' -f "$ISOLATED_LM_STATE/keys/isolated-lm"
./scripts/isolated-lm-vm.py preflight --state-root "$ISOLATED_LM_STATE"
```

Expected: `kvm-ok` reports acceleration available; preflight emits `ok: true`. If `/dev/kvm` is permission-denied, add only the invoking user to the `kvm` group, re-login, and repeat. Do not loosen device permissions globally.

- [ ] **Step 3: Acquire the verified source and prepare one run**

Run:

```bash
./scripts/isolated-lm-vm.py prepare-image --state-root "$ISOLATED_LM_STATE"
./scripts/isolated-lm-vm.py prepare-run --state-root "$ISOLATED_LM_STATE" --run-id manual-001 --ssh-public-key "$ISOLATED_LM_STATE/keys/isolated-lm.pub"
```

Expected: signed checksums verify, the source digest matches, and `manual-001/run.json` reports `prepared`.

- [ ] **Step 4: Boot, reach, and inspect the clean guest**

Run:

```bash
./scripts/isolated-lm-vm.py start-run --state-root "$ISOLATED_LM_STATE" --run-id manual-001 --ssh-private-key "$ISOLATED_LM_STATE/keys/isolated-lm"
./scripts/isolated-lm-vm.py status --state-root "$ISOLATED_LM_STATE" --run-id manual-001
```

Through the harness `exec` command, record outputs for:

```text
./scripts/isolated-lm-vm.py exec --state-root "$ISOLATED_LM_STATE" --run-id manual-001 --ssh-private-key "$ISOLATED_LM_STATE/keys/isolated-lm" -- cloud-init status --long
./scripts/isolated-lm-vm.py exec --state-root "$ISOLATED_LM_STATE" --run-id manual-001 --ssh-private-key "$ISOLATED_LM_STATE/keys/isolated-lm" -- uname -a
./scripts/isolated-lm-vm.py exec --state-root "$ISOLATED_LM_STATE" --run-id manual-001 --ssh-private-key "$ISOLATED_LM_STATE/keys/isolated-lm" -- python3 --version
./scripts/isolated-lm-vm.py exec --state-root "$ISOLATED_LM_STATE" --run-id manual-001 --ssh-private-key "$ISOLATED_LM_STATE/keys/isolated-lm" -- dpkg-query -W cloud-init openssh-server ca-certificates curl python3
./scripts/isolated-lm-vm.py exec --state-root "$ISOLATED_LM_STATE" --run-id manual-001 --ssh-private-key "$ISOLATED_LM_STATE/keys/isolated-lm" -- find / -xdev -iname '*famulus*'
./scripts/isolated-lm-vm.py exec --state-root "$ISOLATED_LM_STATE" --run-id manual-001 --ssh-private-key "$ISOLATED_LM_STATE/keys/isolated-lm" -- find / -xdev -iname '*officina*'
./scripts/isolated-lm-vm.py exec --state-root "$ISOLATED_LM_STATE" --run-id manual-001 --ssh-private-key "$ISOLATED_LM_STATE/keys/isolated-lm" -- mount
```

Expected: cloud-init is complete; declared packages exist; there is no Famulus/Officina state; no host checkout is mounted; `run.json` reports `ready` with the exact QEMU argument vector and SSH port.

- [ ] **Step 5: Stop the guest and verify bounded cleanup state**

Run:

```bash
./scripts/isolated-lm-vm.py stop-run --state-root "$ISOLATED_LM_STATE" --run-id manual-001 --ssh-private-key "$ISOLATED_LM_STATE/keys/isolated-lm"
./scripts/isolated-lm-vm.py status --state-root "$ISOLATED_LM_STATE" --run-id manual-001
```

Expected: no matching QEMU process remains; the manifest reports `stopped`; evidence files remain; the overlay is retained for review rather than silently deleted.

- [ ] **Step 6: Run the repository verification checkpoint**

Run:

```text
python3 -m pytest -q tests/test_isolated_lm_host.py tests/test_isolated_lm_image.py tests/test_isolated_lm_guest.py tests/test_isolated_lm_qemu.py tests/test_isolated_lm_cli.py
./repo_checks.py --suite validators
git diff --check
```

Expected: all commands PASS. Report the pre-existing full-suite baseline failures separately; do not attribute them to this work without a reproducing focused test.

- [ ] **Step 7: Review the live evidence before continuing**

Review `run.json`, the source-image record, serial log, process absence, guest package output, mount output, and forbidden-state scan. Only after this review passes, write the follow-up Codex-authentication and baseline-sealing implementation plan.
