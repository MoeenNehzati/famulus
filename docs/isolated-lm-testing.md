# Isolated LM VM operator guide

This guide operates the unit-verified Ubuntu VM foundation. It prepares a
verified Ubuntu Server 24.04 LTS amd64 cloud image, creates a disposable QCOW2
overlay and NoCloud seed, launches the guest through direct QEMU/KVM, reaches it
through a host-loopback SSH forward, and performs bounded shutdown.

The foundation's live VM acceptance is recorded below. This foundation does not
install or authenticate Codex, seal a baseline, automatically reject Famulus
state inside a guest, inject a Famulus candidate or documentation bundle, run a
scenario, or extract a sanitized report.

## Supported host and packages

The first supported host is Ubuntu 25.10 x86-64 with working KVM acceleration.
From the repository root, install only the approved virtualization packages:

```bash
sudo apt update
sudo apt install cpu-checker qemu-system-x86 qemu-utils cloud-image-utils ubuntu-cloudimage-keyring
```

The host must also provide `curl`, `gpgv`, `sha256sum`, `ssh`, and `ssh-keygen`.
The harness itself uses Python 3.11 or later from the repository environment and
has no additional Python dependency.

Run preflight before downloading or launching anything. A failed preflight exits
nonzero and emits every check as JSON on standard output; the concise human
diagnostic goes to standard error. Preflight requires both
`kvm:acceleration` (a captured five-second `kvm-ok` invocation with exit zero)
and `kvm:read-write` (`/dev/kvm` opened read/write). Only when the latter reports
a permission failure, add the invoking user to the distribution's `kvm` group,
log out and back in so the new group is active, and rerun preflight. Do not make
`/dev/kvm` globally writable. Missing commands require installing the declared
package that supplies them, not changing device permissions.

## External state and SSH key

Choose an absolute state root outside the repository checkout. Every command
requires it explicitly; the CLI does not infer state from the checkout, current
directory, environment, or user home.

```bash
ISOLATED_LM_STATE="${XDG_STATE_HOME:-${HOME}/.local/state}/famulus/isolated-lm-testing"
mkdir -p "$ISOLATED_LM_STATE/keys"
ssh-keygen -t ed25519 -N '' -f "$ISOLATED_LM_STATE/keys/isolated-lm"
```

Keep the private key owner-only. `ssh-keygen` normally creates it with mode
`0600`; the harness rejects a symlink, a relative or unresolved path, a
non-regular file, a key unreadable by its owner, or group/other permissions.
The private-key bytes are never copied into a manifest or CLI result. The run
manifest records only the resolved identity-file path needed for later commands.

## Complete operator sequence

The supported surface has exactly seven commands. Success and structured
command failures emit one JSON object on standard output. Human diagnostics use
standard error. The sole plaintext standard-output exception is explicit
`--help` output; it performs no command. Invalid input or a missing, corrupt,
stale, escaped, or symlinked manifest exits `2`; host or lifecycle-operation
failure exits `1`.
Failed preflight exits `1`. `exec` returns `0` for guest success and otherwise
returns the SSH/guest status in `1..255` (using `1` for an out-of-range process
status); its JSON always contains `guest_exit_code`, `stdout`, and `stderr`.

Run the commands from the repository root:

```bash
./scripts/isolated-lm-vm.py preflight --state-root "$ISOLATED_LM_STATE"
./scripts/isolated-lm-vm.py prepare-image --state-root "$ISOLATED_LM_STATE"
./scripts/isolated-lm-vm.py prepare-run --state-root "$ISOLATED_LM_STATE" --run-id manual-001 --ssh-public-key "$ISOLATED_LM_STATE/keys/isolated-lm.pub"
./scripts/isolated-lm-vm.py start-run --state-root "$ISOLATED_LM_STATE" --run-id manual-001 --ssh-private-key "$ISOLATED_LM_STATE/keys/isolated-lm"
./scripts/isolated-lm-vm.py exec --state-root "$ISOLATED_LM_STATE" --run-id manual-001 --ssh-private-key "$ISOLATED_LM_STATE/keys/isolated-lm" --timeout-seconds 300 --max-output-bytes 1048576 -- cloud-init status --long
./scripts/isolated-lm-vm.py status --state-root "$ISOLATED_LM_STATE" --run-id manual-001
./scripts/isolated-lm-vm.py stop-run --state-root "$ISOLATED_LM_STATE" --run-id manual-001 --ssh-private-key "$ISOLATED_LM_STATE/keys/isolated-lm"
```

Run IDs must match `^[a-z0-9][a-z0-9-]{0,62}$` and cannot be reused. The public
key must be one nonempty `ssh-ed25519` line. `prepare-run` creates a four-vCPU,
8-GiB-RAM, 40-GiB sparse-disk record in lifecycle `prepared`. `start-run`
atomically records its allocated SSH port, identity path, and exact QEMU argv,
launches QEMU, waits for SSH and `cloud-init status --wait`, and emits success
only after lifecycle `ready` is persisted.

`exec` requires a ready run and a nonempty argument vector after the literal
`--`. The local process invokes `ssh` with an argument list, not a local shell.
OpenSSH necessarily gives the remote command to the guest login shell, so the
harness POSIX-quotes the remote argument vector into one command string. It uses
the run's dedicated `known_hosts`, `StrictHostKeyChecking=accept-new`,
`IdentitiesOnly=yes`, `BatchMode=yes`, recorded port, fixed `famulus-test` user,
and recorded private-key path. A different supplied private-key path is rejected.
`--timeout-seconds` defaults to 300 and is accepted only in `(0, 3600]`;
`--max-output-bytes` defaults to 1048576 and is accepted only in
`[1, 16777216]`. The harness drains stdout and stderr concurrently, retains at
most the selected cap for each stream, and kills and reaps SSH at the deadline.
Every exec JSON object records `timeout_seconds`, `max_output_bytes`,
`timed_out`, `stdout_truncated`, and `stderr_truncated`. A timeout returns status
1 with `guest_exit_code: null`; an ordinary guest exit preserves its status.
Captured bytes are decoded as UTF-8 with replacement for invalid sequences.

`status` is strictly read-only. It validates the selected run ID, retains
no-follow descriptors for the state root, `runs`, and the selected run directory,
opens only `runs/<run-id>/run.json` for content, and rejects
unknown schema fields, wrong lifecycle facts, noncanonical state-owned paths,
escapes, symlinks, missing or wrong-type state-owned artifacts, and a QEMU argv
that does not exactly reconstruct from the manifest. Existing `qemu.pid` must be
a regular non-symlink file and existing `qmp.sock` must be a Unix socket. A
recorded identity outside state is
treated as historical path text; only `exec` and `stop-run` validate the supplied
live key against it. Status does not scan other runs, probe that external path,
inspect or repair a process, or rewrite any state.

## State layout and manifests

The harness and operator create this layout under the explicit state root:

```text
<state-root>/
  downloads/
    SHA256SUMS
    SHA256SUMS.gpg
  images/
    noble-server-cloudimg-amd64.img
    source-image.json
  keys/                         # operator-created; never emitted as content
    isolated-lm
    isolated-lm.pub
  runs/
    <run-id>/
      overlay.qcow2
      user-data
      meta-data
      seed.iso
      known_hosts
      serial.log
      qmp.sock                  # present only while QEMU owns it
      qemu.pid                  # present only while QEMU owns it
      run.json
```

`source-image.json` is mode `0600`, sorted, newline-terminated JSON. Its fields
are `schema_version`, `image_url`, `checksums_url`, `signature_url`, `filename`,
`verified_source_digest`, `byte_size`, `retrieved_at`, and `cached_path`. The
Ubuntu keyring authenticates `SHA256SUMS` before the named image digest is
trusted; the downloaded bytes must match that digest before the image record is
written.

`run.json` is also mode `0600`, sorted, newline-terminated JSON. Its fields are:

- `schema_version`, `run_id`, `run_dir`, `created_at_utc`, and `lifecycle`;
- `resources` with `vcpus`, `memory_mib`, and `disk_gib`;
- `source_image_digest`, `overlay`, `seed_iso`, `known_hosts`, `serial_log`,
  `qmp_socket`, `pid_file`, and `record_path`;
- `ssh_user`, optional `ssh_port`, optional `identity_file`, and `qemu_command`.

The launch fields are null/empty while `prepared`. Before QEMU is invoked they
are persisted with lifecycle `launching`. Lifecycle becomes `running` only after
QEMU reports launch success and `ready` only after SSH and cloud-init complete.
A bounded launch timeout or exception records `launch-failed` only after an
exact process scan proves absence; an exact surviving VM leaves `launching`
truthfully recoverable. Shutdown records `stopped` only after a fresh `/proc`
scan finds no `qemu-system-x86_64` process with both the exact VM name and
overlay path. Serial logs, manifests, source evidence, and the overlay are
retained for diagnosis; `stop-run` does not silently delete evidence.

## Network and filesystem boundary

QEMU user-mode networking gives the guest outbound NAT. Treat that as outbound
network authority, not as offline isolation: the guest may contact reachable
internet services and QEMU's user-network host endpoint. There is no bridge or
tap interface and no guest service is forwarded except SSH. The sole inbound
forward is a dynamically allocated host TCP port bound to `127.0.0.1` and mapped
to guest port 22, so it is not exposed on a non-loopback host address.

No host directory, repository checkout, 9p share, or virtiofs device is mounted
into the guest. The state root remains host-side QEMU evidence and disk state;
it is not a shared guest filesystem.

## Recovery

- Download or verification failure: no successful source-image manifest is
  emitted. Checksum and signature bytes are downloaded to same-filesystem
  staging names, authenticated there with `gpgv`, and parsed before either
  canonical evidence path is replaced. A bad signature therefore preserves a
  prior canonical checksum/signature pair, and staging files are cleaned.
  Connect and read operations are individually bounded inside one 900-second
  monotonic acquisition budget. Correct the network/package/trust failure and
  rerun `prepare-image`; do not hand-edit evidence or bypass `gpgv`.
- Run preparation failure: the newly created run directory is removed by the
  preparation boundary. Correct the public key, source image, disk, or tool
  failure and rerun `prepare-run`. If a complete run directory already exists,
  preserve it and select a fresh run ID rather than reusing it.
- QEMU launch interruption, timeout, or failure: `run.json` retains launch facts.
  `launching` means the outcome remains uncertain or an exact VM was recovered;
  `launch-failed` means an exact process scan proved absence. Inspect the JSON
  diagnostic and `serial.log`, then use `stop-run`, which can recover from a
  missing, malformed, or reused PID file through exact `/proc` matching. Multiple
  exact matches fail closed. Prepare a fresh run ID after cleanup.
- SSH or cloud-init readiness failure: the manifest remains `running` because a
  VM may still exist. Inspect `status` and `serial.log`, then use `stop-run`.
  Do not invoke `start-run` again on that record; prepare a fresh run after
  cleanup.
- Guest command failure: inspect the `exec` JSON's guest exit code and captured
  streams. The run remains `ready`; correct the guest command and retry it.
- Shutdown failure: the manifest retains its prior lifecycle. Inspect the
  diagnostic, manifest, serial log, and PID/QMP state, then retry `stop-run`.
  The harness does not trust the numeric PID alone: it scans for the exact QEMU
  executable, VM name, and overlay path before SSH or QMP. Investigate multiple
  exact matches instead of weakening the identity check.
- Missing, corrupt, stale, escaped, or symlinked manifest: commands fail closed
  and never repair it. Preserve the directory as evidence, inspect it manually,
  and use a new run ID. Do not make status rewrite paths or lifecycle values.

## Latest live acceptance

Result: **PASS for the VM-foundation scope** on 2026-08-11 America/New_York
(2026-08-12 UTC), using run ID `acceptance-20260811`.

The host had these approved package versions:

```text
cloud-image-utils          0.33-1
cpu-checker                0.7-1.4
qemu-system-x86            1:10.1.0+ds-5ubuntu2.7
qemu-utils                 1:10.1.0+ds-5ubuntu2.7
ubuntu-cloudimage-keyring  2023.11.28.1
```

Observed acceptance evidence:

- all 12 preflight checks passed, including bounded `kvm-ok` and read/write
  `/dev/kvm` access;
- signed-source acquisition succeeded with digest
  `0533b0655c32e68b31d792ecd6ccfca95abdbc536c4446874fe0513bd4140ffe`;
- the guest exposed 4 vCPUs, approximately 8 GiB RAM, and a 40 GiB disk;
- cloud-init reported `done` without errors, and the declared generic guest
  packages were installed;
- outbound HTTPS returned 200; the only inbound listener was the recorded SSH
  forward on `127.0.0.1`;
- no Famulus, Officina, or Codex executable was present, a privileged root scan
  found no Famulus/Officina-named path after excluding the intentional
  `/home/famulus-test` username, the maintainer checkout path was absent, and
  the mount table contained no host filesystem;
- a guest status 7 propagated as process status 7 with both streams preserved;
  a 64-byte cap truncated each stream independently and recorded both flags;
  and a one-second deadline returned `timed_out: true` with no guest exit code;
- the run remained `ready` after adverse command probes, then bounded shutdown
  persisted `stopped`; independent host inspection found neither the loopback
  listener nor an exact QEMU process afterward; and
- the run manifest, 40-GiB sparse QCOW2 overlay, seed, serial log, known-hosts,
  signed checksums, signature, source-image record, and source image remain in
  the explicit external state root for diagnosis.

The live run also found and closed one Python 3.13 compatibility defect in
authenticated acquisition. A complete small `HTTPResponse` read detaches its
socket before the downloader's final EOF iteration. The downloader now accepts
that missing socket only when urllib reports the response already closed; an
open response still must expose a timeout-capable socket. A focused regression
failed before the change, passed after it, and the original live acquisition
then succeeded.

This result does not certify the next-plan capabilities listed below.

## Public Famulus package acceptance

Result: **FAIL at the package-install layer** on 2026-08-11
America/New_York (2026-08-12 UTC), using disposable run
`package-20260811`. The VM foundation itself remained healthy and shut down
cleanly.

The credential-free test used:

- Codex CLI `0.147.0`, installed inside the guest with OpenAI's standalone
  Linux installer from `https://chatgpt.com/codex/install.sh`;
- installer SHA-256
  `ba92dd27e5c06f0d3bbc58bfa4b9cfb6599cd2742fbb1f92a2765e6c07dedb5a`;
- public marketplace commit
  `0f9b98e0d55f2f55e2c8d40b8ad86fdcdc78e41e` from
  `https://github.com/MoeenNehzati/famulus.git`; and
- `famulus@nullkit` version `0.1.0`, whose installed plugin-manifest SHA-256
  was `6c127fbcbcbf205e9a4287e22ecfec0c883a44ac23fa9f8e3dbf9ff5d88ebd48`.

Codex registered the pinned `nullkit` marketplace, listed the package, and
installed it as enabled without requiring host credentials. The package then
failed both relevant setup paths:

1. The README's minimum direct `_install_scaffold.py --repo-root ...` command
   exited 1. It wrote four launchers without an active managed runtime, so the
   initial `dispatcher` and `llm-wakeup` launchers raised `FileNotFoundError`.
   It also failed the required `certificate-signing-material` capability because
   `keyring` was unavailable to the installer process.
2. The full non-interactive `_phase_entry.py --non-interactive --no-dev-mode
   --no-optional-deps` command bootstrapped managed `uv 0.11.29`, built and
   activated the managed runtime, and repaired the launchers, but still exited
   1 on the same mandatory certificate capability.

The failure is reproducible and localized. Certificate provisioning runs in
Ubuntu's ambient Python, where `cryptography` is present but `keyring` is not.
The newly built managed runtime does contain `keyring`, but a headless Ubuntu
Server exposes only `keyring.backends.fail.Keyring` with priority zero, which is
not a usable secret backend. Installing an ambient dependency or forcing an
insecure fallback was deliberately not used to hide this package defect.

After the failed full install, the managed `dispatcher` and `llm-wakeup` were
operational; dispatcher resolved a real list-manager interface from the
installed plugin, with certification status unavailable. The manifest-based
uninstaller then removed all four generated launchers and the managed shell
block, retaining only the documented worker directory. Codex removed the
plugin and marketplace successfully. The managed runtime remained, consistent
with the documented rule that installed Python dependencies are not reversed.
No exact QEMU process or SSH listener remained after bounded VM shutdown.

Therefore the public package does not yet satisfy the broader plan's
from-scratch installation requirement on the declared clean Ubuntu Server
baseline. Fixing certificate provisioning and reconciling the README's direct
scaffold command with its managed-runtime prerequisite are package work, not VM
foundation work.

## Next-plan boundary

The next implementation plan installs Codex through OpenAI's supported Linux
path, performs operator-assisted authentication, verifies the guest contains no
Famulus/private/prior-run state, and seals and versions a Famulus-free baseline.
A later plan defines immutable Famulus-candidate and public-documentation inputs,
bounded injection, scenario execution, deterministic probes, and sanitized
report extraction. None of those capabilities is claimed by this foundation.
