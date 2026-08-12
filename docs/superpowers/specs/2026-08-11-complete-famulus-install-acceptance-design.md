# Complete Famulus installation acceptance — design

## Purpose

The isolated Linux VM now proves the virtualization, acquisition, execution,
and cleanup foundation. Its first public-package trial exposed a separate
installer defect: plugin installation succeeded, but the documented direct
scaffold path created launchers without a managed runtime, and the full Phase 1
installer failed while provisioning mandatory certificate-signing material.
The ambient interpreter lacked `keyring`; the managed runtime contained
`keyring`, but the headless guest had no usable credential-store backend.

This work makes fresh installation coherent and tests the complete supported
installation. It does not make certificate signing optional, silently accept a
partial install, install packages into the ambient Python, or store a private
signing key in plaintext.

## User-visible contract

`_phase_entry.py` is the only supported fresh-install entrypoint. It prepares
the managed runtime, verifies that the runtime can use the host credential
store, activates the candidate, installs the shared scaffold, provisions and
verifies certificate-signing material, and only then runs later installation
phases. A successful Phase 1 means every required capability is usable.

`_install_scaffold.py` remains public only as a targeted repair command. It
requires an already active managed runtime and runs the same credential-store
preflight before changing launchers, PATH wiring, manifests, or certificate
state. If no runtime is active, it exits nonzero with the fresh-install command
and makes no repair mutation.

The installer does not install or configure an operating-system credential
service. A usable native credential store is a public host prerequisite because
creating or unlocking one can require system packages, a login session, and a
user-chosen secret. The installer checks this prerequisite and gives
platform-specific setup guidance before committing user-visible installation
state.

## Transaction boundary

Managed-runtime construction is split into preparation and activation:

1. Bootstrap `uv` if needed. The downloaded, digest-verified `uv` binary is a
   reusable installer cache, not an active Famulus installation.
2. Build and validate a uniquely named candidate runtime without changing the
   active-runtime pointer.
3. Run a credential-store roundtrip with the candidate interpreter: create a
   random probe secret in the Famulus test namespace, read it back, and delete
   it. Never print the secret.
4. If the probe fails, remove the candidate and exit. Do not write launchers,
   shell configuration, install manifests, certificate files, or
   `current.json`; preserve any previously active release.
5. If it succeeds, atomically activate the candidate and run scaffold and later
   phases.

Activation remains atomic through the existing runtime pointer. A failure after
activation must be reported as a failed installation with exact retained state;
it must not claim rollback that did not occur. Certificate provisioning uses
the same managed interpreter and backend that passed preflight, rather than the
ambient interpreter running the entry script.

The secret-store probe is bounded and cleanup is mandatory on success and
failure. A cleanup failure makes preflight fail and identifies the probe target
without revealing its value.

## Credential-store requirements

The supported backend remains Python `keyring` backed by the native host store:

- macOS: Keychain through the system keyring backend;
- Windows: Windows Credential Locker through the system keyring backend;
- desktop Linux: Freedesktop Secret Service in the user's login session;
- headless Linux acceptance: GNOME Keyring in a persistent D-Bus session.

Python keyring's official headless-Linux guidance requires GNOME Keyring, a
D-Bus session, unlocking or creating the login keyring, and running consumers
in that same session. The acceptance environment therefore installs the public
system prerequisites, starts one persistent session, supplies a run-specific
unlock secret through standard input, and runs installation and all later
certificate probes inside that session. The unlock secret is not stored in the
baseline, command arguments, serial log, run manifest, or extracted report.
See [Python keyring: Using Keyring on headless Linux systems](https://keyring.readthedocs.io/en/latest/index.html#using-keyring-on-headless-linux-systems)
and the [Freedesktop Secret Service API](https://specifications.freedesktop.org/secret-service/latest/description.html).

A transient D-Bus wrapper around only the installer is insufficient. The live
acceptance test must start a second process in the same persistent login
session after installation and prove that it can reload the signing key. It
must also prove that an unrelated session without the prerequisite receives an
actionable unavailable-backend error rather than a fallback.

No `keyrings.alt`, unencrypted file backend, repository-local private key,
automatic ambient `pip install`, or test-only backend is accepted as production
evidence.

## Components

### Managed-runtime preparation

Refactor the existing managed-runtime builder so preparation returns the exact
candidate release metadata and interpreter path without activation. A separate
commit operation deploys the stable resolver and atomically updates
`current.json`. Cleanup accepts only the exact candidate created by the current
attempt and refuses broad or unresolved paths.

### Installation preflight

Add a small Officina installation module with a machine-readable command for
credential-store probing and certificate setup. Structured results contain the
backend name, operation status, and bounded diagnostic, but never a secret.
The phase entry and scaffold invoke it through the candidate or active managed
interpreter with shell-free, timeout-bounded subprocess calls.

### Scaffold ordering

Move all required preflight checks ahead of launcher, shell, manifest, and
certificate writes. Certificate setup remains a required capability and is
reported in the existing capability report. Direct repair rejects a missing or
unsafe active-runtime pointer before any write.

### VM acceptance support

Extend the isolated-VM operator procedure with a run-scoped headless Secret
Service setup. Keep operating-system prerequisites separate from Famulus-owned
Python dependencies. The baseline may contain public OS packages, but no
Famulus state, certificate key, keyring contents, or reusable unlock secret.

Candidate acceptance uses an immutable source archive plus a versioned public
documentation bundle, records their digests in the run manifest, and never
mounts the maintainer checkout. Before merge, the archive is produced from the
exact committed candidate. After publication, the same scenario is rerun
against the pinned public marketplace commit.

## Acceptance matrix

Automated tests and the live VM run cover all of the following; none may be
converted to a skip merely because the host is headless:

1. Fresh install with no usable secret backend fails before user-visible
   installation state, preserves an older active runtime, and gives exact setup
   guidance.
2. Direct scaffold with no active runtime fails before writes and points to the
   full Phase 1 entrypoint.
3. Full plugin-mode install with a supported backend builds and activates the
   managed runtime, installs every required shared launcher, provisions signing
   material, and exits zero.
4. A separate post-install process in the same login session reloads the
   private key, verifies the public pair, signs a disposable certificate
   payload, and verifies the signature.
5. Dispatcher, `invoke-skill`, `llm-wakeup`, `lw`, and each explicitly selected
   assistant launcher pass real `--help` or equivalent smoke probes through the
   active managed runtime.
6. Re-running the full installer is idempotent: it preserves user-owned files,
   does not rotate signing material, and leaves one valid active runtime.
7. A staged failure at each transaction boundary leaves the documented prior
   state and no active partial candidate or probe secret.
8. Dev-mode installation against a disposable committed checkout exercises one
   real `skill-certifier` issuance and `skill-drift` verification without
   reading the maintainer checkout.
9. Uninstall removes every manifest-owned launcher and shell/config mutation,
   reports deliberately retained runtime and credential state, and leaves no
   process or listener from the acceptance run.
10. Logs, JSON output, serial output, reports, and extracted artifacts contain
    neither the keyring unlock secret nor private signing-key material.
11. Linux, macOS, and Windows unit/integration suites cover their native-backend
    preflight contracts. Native smoke tests remain mandatory on dedicated
    platform runners; generic runners may use the existing explicit skip only
    where the repository's test policy already permits it.
12. The existing platform-neutral, documentation, installer, security,
    validator, and full repository gates remain green.

## Documentation and plan reorganization

Update the README minimum-install command to `_phase_entry.py` and label direct
scaffold invocation as repair-only. The installation guide gains a prerequisite
table, headless Linux session instructions, preflight semantics, transaction
boundary, retained-state rules, and complete verification commands.

Reorder Workstream 1 so package readiness is completed before scenario
protocol work:

1. coherent fresh-install entrypoint and transactional managed runtime;
2. native credential-store preflight and mandatory certificate setup;
3. complete automated installation matrix;
4. immutable candidate/documentation injection into the VM;
5. live candidate acceptance, then pinned-public-package acceptance;
6. only then authenticate and seal the reusable Famulus-free LM baseline.

The existing public-package failure remains documented as historical evidence.
It is closed only after both the committed candidate and the subsequently
published package pass the same complete scenario.

## Out of scope

- Weakening certificate or secret-store policy to make installation green.
- Installing system packages with elevated privileges from the Famulus Python
  installer.
- Storing reusable credentials or Famulus state in the sealed VM baseline.
- Treating a mocked, injected, or test-only keyring as live acceptance.
- Declaring the public-package issue fixed before the corrected commit is
  actually published and retested from the public marketplace.
