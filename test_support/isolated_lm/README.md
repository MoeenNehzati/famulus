# Isolated LM VM requirements and setup

This directory contains the direct QEMU/KVM foundation for disposable Ubuntu
VMs. The full command contract, state layout, lifecycle, and recovery procedure
are documented in [`docs/isolated-lm-testing.md`](../../docs/isolated-lm-testing.md).

## Host requirements

The supported host is Ubuntu 25.10 x86-64 with hardware virtualization enabled.
Install the virtualization packages:

```bash
sudo apt update
sudo apt install cpu-checker qemu-system-x86 qemu-utils cloud-image-utils ubuntu-cloudimage-keyring
```

The host must also provide `curl`, `gpgv`, `sha256sum`, `ssh`, and
`ssh-keygen`. The harness uses the repository's Python 3.11-or-later runtime and
has no additional Python package dependency.

KVM must be usable by the invoking user. If preflight reports only a
`/dev/kvm` permission failure, add that user to the distribution's `kvm` group,
log out and back in, and rerun preflight. Do not make `/dev/kvm` globally
writable.

## Initial setup

From the repository root, choose an absolute state directory outside the
checkout and create a dedicated SSH key:

```bash
ISOLATED_LM_STATE="${XDG_STATE_HOME:-${HOME}/.local/state}/famulus/isolated-lm-testing"
mkdir -p "$ISOLATED_LM_STATE/keys"
ssh-keygen -t ed25519 -N '' -f "$ISOLATED_LM_STATE/keys/isolated-lm"
```

Keep the private key owner-only, then verify the host before downloading an
image or starting a VM:

```bash
./scripts/isolated-lm-vm.py preflight --state-root "$ISOLATED_LM_STATE"
```

A successful preflight requires both a successful bounded `kvm-ok` invocation
and read/write access to `/dev/kvm`. Continue with the complete operator
sequence in [`docs/isolated-lm-testing.md`](../../docs/isolated-lm-testing.md).

This foundation does not require Docker, libvirt, or a VM-management framework.
It also does not install Codex, authenticate an assistant host, seal a baseline,
or install Famulus; those are later workstreams.
