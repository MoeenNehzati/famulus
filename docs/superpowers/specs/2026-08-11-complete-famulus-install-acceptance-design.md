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
the managed runtime, verifies that the candidate can use the host credential
store, stages or validates certificate-signing material, activates the
candidate, commits the certificate selector, installs the shared scaffold, and
only then runs later installation phases. A successful Phase 1 means every
required capability is usable.

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

Installation has two named profiles. The **minimum profile** installs the
managed runtime, every required shared command, mandatory certificate support,
and no optional heavy dependencies or optional assistant launchers. The
**maximal profile** installs every declared dependency, including optional
dependencies, and every launcher supported on the selected platform. External
account authorization and recurring-job creation remain post-install workflows,
not installation capabilities. Each acceptance verdict names its profile,
platform, assistant host, and any reviewed platform exclusion.

Required capabilities (`uv`, managed Python, the active runtime, native secret
storage, certificate signing, `dispatcher`, `invoke-skill`, `llm-wakeup`, and
`lw`) must all report `installed` and pass a live probe for Phase 1 to return
zero. Platform-supported launchers requested by the selected profile are also
required. Unsupported platform-specific launchers are permitted only when the
platform matrix names that exclusion. Google onboarding remains optional and
cannot change the Phase 1 verdict.

## Transaction boundary

All install, repair, and uninstall operations for one home are serialized by
one exclusive OS-backed lock under the canonical Famulus install-state root.
The lock spans candidate preparation through the last manifest update. Kernel
lock release handles a dead owner; callers never infer staleness from a PID or
delete the lock file to break contention. Lock acquisition has a documented
timeout and returns a stable busy error without mutation.

Managed-runtime construction is split into preparation and activation:

1. Bootstrap `uv` if needed. The downloaded, digest-verified `uv` binary is a
   reusable installer cache, not an active Famulus installation.
2. Build and validate a uniquely named candidate runtime without changing the
   active-runtime pointer.
3. Run a credential-store roundtrip with the candidate interpreter. Create a
   collision-checked target using at least 128 bits from the system CSPRNG,
   store a random value, read it back, delete it, and perform a final lookup
   proving absence. Never print the identifier's value or the secret.
4. Validate existing certificate state or stage one exact new key pair without
   changing the active selector. A losing or failed staged pair is removed from
   both the keyring and public state and verified absent.
5. On any pre-activation exception—including dependency installation,
   candidate validation, credential probing, certificate staging, resolver
   preparation, or pointer validation—remove only the exact new candidate and
   staged certificate pair. Preserve the previous pointer, manifest, launchers,
   shell files, and active certificate identity. A newly published compatible
   resolver bundle may remain cached but is not active installation state.
6. Durably create a `prepared` recovery journal naming the exact candidate,
   prior pointer, resolver bundle, staged certificate state, and intended
   mutations. Then atomically activate the candidate and advance the journal to
   `committed`; pointer activation is the transaction commit point, but these
   are deliberately two durable writes. On recovery, a `prepared` journal is
   reconciled with `current.json`: if the pointer names the journaled candidate,
   resume as committed; otherwise remove the inert candidate and staged key and
   clear the journal. Commit the staged certificate selector and perform
   scaffold, dev-link, and launcher mutations while advancing the journal before
   each mutation and the install manifest after it.

Activation remains atomic through the existing runtime pointer. The stable,
dependency-free bootstrap resolver is immutable for its schema version. It
reads `current.json`, which names an immutable versioned resolver bundle
containing resolver code and its trust sidecar; switching the pointer therefore
selects the runtime and bundle together. Published bundles are never
overwritten, and a compatible inactive bundle may remain cached after a
pre-commit failure. A failure after the
commit point is a recoverable partial installation, not a rollback: the journal
names the completed mutations, exact retained candidate/certificate state, and
next safe action. Re-run resumes or repairs that transaction; uninstall can
replay it. Certificate provisioning uses the same managed interpreter and
backend that passed preflight, never the ambient interpreter.

The secret-store probe and every child process are timeout-bounded. Cleanup is
mandatory on success and failure; deletion is successful only after a final
lookup proves the exact probe target absent. A collision is regenerated and
never overwritten. A cleanup failure makes preflight fail through a stable
error code without exposing the target's secret or raw backend diagnostics.

The retained-state contract is explicit:

| Failure boundary | Active pointer | New candidate | Certificate state | Manifest/journal |
|---|---|---|---|---|
| Before candidate creation | unchanged | absent | unchanged | unchanged |
| Candidate build/validation | unchanged | removed | unchanged | unchanged |
| Secret probe or certificate staging | unchanged | removed | exact probe/staged pair verified absent | unchanged |
| Resolver preparation or pointer validation | unchanged | removed | staged pair removed | compatible cached resolver bundle permitted; no transaction journal |
| Prepared journal before activation | unchanged | retained and named | staged pair retained and named | durable `prepared` journal |
| Pointer switched before journal advance | new pointer retained | retained and selected | staged pair retained and named | durable `prepared` journal; recovery detects the pointer match and resumes as committed |
| After activation | new pointer retained | retained | active or precisely staged as journaled | durable `committed` partial transaction retained |
| Successful reinstall | exactly one active pointer | current plus one previous rollback release | same active key ID | completed manifest; journal cleared |

Older rollback releases beyond the current and immediately previous successful
release are pruned only after a fully successful install. Failed installs never
prune a known-good release.

## Credential-store requirements

The supported backend remains Python `keyring==25.6.0` backed by the native
host store; changing that pin requires reviewing and updating the concrete
backend allowlist:

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

Preflight accepts only an audited allowlist of concrete native backend classes
for the pinned `keyring` version and current platform. It rejects third-party,
alternate, file, test, chained, Null, and Fail backends even if they have
positive priority and pass a roundtrip. Tests cover selection through
`PYTHON_KEYRING_BACKEND`, keyring configuration and path injection, entry
points, a positive-priority plaintext backend, and a chained backend.

Machine output uses closed error codes:
`unsupported_backend`, `backend_unavailable`, `backend_locked`,
`roundtrip_failed`, and `cleanup_failed`. Installer-authored static text maps
those codes to platform guidance. Raw backend exceptions, tracebacks, child
stdout, and stderr are discarded inside the child or mapped to closed status
codes; they are never returned to the parent or retained. Secrets are never
placed in argv or environment variables. Tests use a malicious backend whose
exceptions contain submitted secrets and verify that literal, Base64,
hexadecimal, JSON-escaped, and PEM-like forms are absent.

For headless Linux, “persistent session” means one supervised D-Bus/Secret
Service session that lives for the complete acceptance run; it does not promise
persistence across logout or reboot. Every certificate consumer in that run
inherits the same session. Processes outside it fail closed with the appropriate
static error. Long-lived service use outside a login session is a separate
future design.

## Certificate-state ownership

Certificate state is one lifecycle unit independent of a versioned plugin
cache. Its public keys and active selector live under a stable canonical
Famulus data root; matching private keys live in the native credential store.
All certifier and drift consumers receive that same public-state root. Updating
or replacing a plugin therefore preserves the active key identity. The existing
repository-local ignored location is migrated once under the install lock, with
conflicts failing closed rather than generating a new identity.

Default uninstall retains both halves and reports the stable public-state path
and non-secret keyring target. Purge deletes the exact private keys first,
verifies their absence, then deletes matching public state. If private-key
deletion or verification fails, purge retains the public half and exits nonzero;
it never orphan-deletes the verification material.

## Components

### Managed-runtime preparation

Refactor the existing managed-runtime builder so preparation returns the exact
candidate release metadata and interpreter path without activation. A separate
activation operation writes a versioned resolver bundle, records its identity
in the prepared journal and pointer schema, and atomically updates
`current.json`; the immutable bootstrap resolver follows that pointer and the
old bundle is not overwritten. Cleanup accepts only the exact candidate created
by the current attempt and refuses broad or unresolved paths. Direct repair validates
the pointer with the same uv-derived trusted interpreter roots used by the
deployed resolver; missing or corrupt resolver/trust state fails before writes.

### Installation preflight

Add a small Officina installation module with a machine-readable command for
credential-store probing and certificate setup. Structured results contain the
allowlisted backend identity, closed status code, and bounded static diagnostic,
but never raw backend text or a secret.
The phase entry and scaffold invoke it through the candidate or active managed
interpreter with shell-free, timeout-bounded subprocess calls.

### Install serialization and records

Add a platform adapter for the per-home OS lock. Make resolver/trust writes,
the install manifest, and the recovery journal confined atomic writes with file
and directory durability. Manifest mutation occurs only while holding the lock.
Concurrent install, repair, uninstall, and certificate-provision attempts fail
busy or serialize without losing ownership records or generating a second key.

### Scaffold ordering

Move all required preflight and staging checks ahead of activation, launcher,
shell, and manifest writes. Certificate setup remains required and is reported
in the existing capability report. Direct repair rejects a missing or unsafe
active runtime before any write. Post-commit writes journal their ownership
before the next mutation so a crash is recoverable.

### VM acceptance support

Extend the isolated-VM operator procedure with a run-scoped headless Secret
Service setup. Keep operating-system prerequisites separate from Famulus-owned
Python dependencies. The baseline may contain public OS packages, but no
Famulus state, certificate key, keyring contents, or reusable unlock secret.

For the Ubuntu 24.04 baseline, the public OS additions are `dbus-daemon` (which
provides `dbus-run-session`) and `gnome-keyring`; their resolved package
versions are recorded when the baseline is sealed and again in every verdict.

The VM harness gains explicit bounded interfaces for: host-to-guest immutable
candidate and documentation transfer; a secret stdin payload passed only to SSH
stdin; starting and identifying the supervised D-Bus/GNOME Keyring session;
running later commands in that same session; sanitized report extraction; and
session/process/socket teardown. Secret bytes are never placed in remote command
text, argv, environment variables, manifests, diagnostics, or retained files.
Famulus installer/preflight processes run as the unprivileged guest and never
invoke `sudo` or `pkexec`; public OS prerequisites are installed in a separate,
recorded operator step.

The acting LM is selected through one host-owned, versioned acceptance-config
object rather than being hard-coded into a scenario. Its `agent.model_tier`
defaults to `cheap`; `agent.models` maps that tier to a concrete model ID. For
the initial Codex adapter, `cheap` resolves to `gpt-5.6-luna`, the current
cost-sensitive GPT-5.6 model. The adapter passes the resolved ID explicitly with
`codex exec --model`; it never inherits an ambient or more expensive default.
Changing the test model is therefore a config edit, not a scenario or harness
code change. An exact-model override is permitted only when explicitly present
in the run config. Unknown tiers, missing mappings, and implicit fallback fail
preflight. The config digest, requested tier, resolved model ID, Codex CLI
version, and any explicit override are retained in sanitized evidence. Candidate
and public gates use the same resolved model unless the operator deliberately
records a comparison run.

Candidate and public acceptance are distinct acquisition gates with identical
post-acquisition assertions. Candidate acceptance installs a production-shaped
local marketplace/plugin artifact whose archive and documentation digests map
to the exact reviewed commit; it never mounts the maintainer checkout. Public
acceptance resolves the marketplace package, records the exact installed
payload digest and resolved source commit, and fails if they do not equal the
expected published commit. If a host CLI cannot pin a ref, the verifier checks
the resolved checkout after acquisition rather than pretending the command was
pinned.

## Acceptance matrix

Automated tests and the live VM run cover all of the following; none may be
converted to a skip merely because the host is headless:

1. Fresh install with no usable secret backend fails before user-visible
   installation state, preserves an older active runtime, and gives exact setup
   guidance.
2. Direct scaffold with no active runtime fails before writes and points to the
   full Phase 1 entrypoint.
3. Full plugin-mode minimum and maximal profiles with a supported backend build
   and activate the managed runtime, install every profile-required command,
   provision signing material, and exit zero. Every declared dependency and
   public command maps to a live probe or a named, reviewed platform exclusion.
4. A separate post-install process in the same login session reloads the
   private key, verifies the public pair, signs a disposable certificate
   payload, and verifies the signature.
5. Dispatcher, `invoke-skill`, `llm-wakeup`, `lw`, and each explicitly selected
   assistant launcher pass real `--help` or equivalent smoke probes through the
   active managed runtime.
6. Re-running the full installer is idempotent: it preserves user-owned files,
   does not rotate signing material, and leaves one valid active runtime.
7. Failure injection at every row of the retained-state table proves its exact
   pointer, release, resolver, certificate, probe, journal, and manifest state.
8. Dev-mode installation against a disposable committed checkout runs exact
   dispatcher interfaces for target `get-weather` at the exact `HEAD` reviewed
   in that guest:

   ```bash
   dispatcher --caller-skill skill-certifier \
     skill-certifier._rtx.interface.certify \
     certify get-weather \
     --reviewed-repository "$DISPOSABLE_REPO" \
     --reviewed-commit "$REVIEWED_COMMIT" --json
   dispatcher --caller-skill skill-drift \
     skill-drift._rtx.interface.drift-status \
     status --skill-root "$DISPOSABLE_REPO/skills/get-weather" --json
   ```

   The first command may write only signing material, certificate histories,
   and generated pooled reviews declared by the certifier contract and must
   report certification success. A fresh process runs the second command and
   reports `certificate-current`. The test snapshots the checkout, then changes
   `skills/get-weather/SKILL.md`, observes `certificate-stale`, restores the
   exact reviewed bytes, and observes `certificate-current`; both drift runs
   leave the snapshot byte-for-byte unchanged.
9. Default-retention and purge scenarios both run the manifest uninstaller and
   native host plugin/marketplace removal. After a fresh host process they
   verify launcher/PATH/hook/config removal, skill invisibility, manifest state,
   runtime/certificate retention or deletion, user-owned sentinels, and cleanup
   after partial installation.
10. Logs, JSON output, serial output, reports, and extracted artifacts contain
    neither the keyring unlock secret nor private signing-key material.
11. Linux, macOS, and Windows unit/integration suites cover their native-backend
    preflight contracts. Dedicated native smoke jobs are mandatory: headless
    Linux Secret Service, macOS Keychain, and Windows Credential Locker. Each
    job records `pytest -rs` output and compares it with a checked-in allowlist;
    unexpected skips fail. Generic runners retain only existing explicitly
    annotated native-backend skips.
12. The existing platform-neutral, documentation, installer, security,
    validator, and full repository gates remain green.
13. Both supported plugin hosts retain mandatory packaging/lifecycle gates.
    The first live VM scenario uses Codex; Claude remains mandatory in its
    dedicated native install lifecycle because its marketplace command cannot
    pin a ref.
14. Every verdict records the scenario version, verifier version, exact
    installer command, OS-package versions, assistant-host version, managed
    Python and `uv` versions, profile, candidate/documentation digests, resolved
    source commit, and installed plugin payload digest.
15. Cleanup proves absence by recorded run-owned PIDs, process argv, D-Bus
    address/socket, QEMU identity, SSH listener, and run-specific canaries—not
    by broad process-name matching.
16. Serialization tests cover install/install and install/uninstall contention,
    repair contention, lock timeout with no mutation, automatic kernel release
    after a dead owner, concurrent certificate provisioning with one key ID,
    exact losing-key cleanup, and repository-local certificate migration with
    identical, missing, and conflicting stable state.

## Documentation and plan reorganization

Update the README minimum-install command to `_phase_entry.py` and label direct
scaffold invocation as repair-only. The installation guide gains a prerequisite
table, headless Linux session instructions, preflight semantics, transaction
boundary, retained-state rules, and complete verification commands. Update
`skills/install-assistant-tools/SKILL.md`, its source and generated blueprint
interface text, the VM operator guide, and both Workstream 1 plans in the same
change. Executable documentation tests compare every published fresh-install,
repair, certification, drift, uninstall, and purge command with its live parser
and interface contract.

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

Implementation is deliberately split into two sequential plans. Plan A owns
installer serialization, stable certificate state, native-backend enforcement,
transaction semantics, automated install profiles, uninstall, and public docs.
Plan B owns VM stdin/session/transfer/report interfaces and live candidate then
public-package certification. Plan B consumes Plan A's committed installer
contract; neither plan may claim the public-package issue closed before the
published artifact passes.

## Out of scope

- Weakening certificate or secret-store policy to make installation green.
- Installing system packages with elevated privileges from the Famulus Python
  installer.
- Storing reusable credentials or Famulus state in the sealed VM baseline.
- Treating a mocked, injected, or test-only keyring as live acceptance.
- Declaring the public-package issue fixed before the corrected commit is
  actually published and retested from the public marketplace.
