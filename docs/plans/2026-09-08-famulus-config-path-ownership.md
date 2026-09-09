# Famulus Configuration Path Ownership

Status: proposed, revised after three independent audits

## Goal

Make `officina.common.famulus_paths` the sole authority for the
platform-specific location of every persistent Famulus configuration directory.
Plugin reinstall, plugin-data identity changes, and plugin-cache replacement
must not change where Google credential descriptors or service bindings live.

This is a pre-first-release cleanup. There is no compatibility or migration
requirement for existing service-local paths.

## Decision criteria

Apply these criteria in order:

1. Correct ownership: durable configuration cannot depend on plugin installation
   or plugin-data identity.
2. Simplicity: retain one descriptor format, one path resolver, and one binding
   route per service.
3. Maintainability: keep platform selection in `famulus_paths` and keep
   skill-owned filenames in their skills.
4. Reuse: extract and reuse existing `resolve_famulus_paths` calculations,
   validation/error conventions, blueprint generation, atomic writers, secret
   store, credential-file loader, and service binders. Do not build parallel
   machinery where an existing component already owns the behavior.
5. Scope: no migration layer, compatibility fallback, new path registry, or
   unrelated OAuth behavior change.

## Current inconsistency

`connect-google` derives its directory from `FamulusPaths.config_root`, but
`cloud-files`, `online-calendar`, and `email-client` construct Linux-shaped
`~/.config/<skill>` paths themselves. Those paths bypass XDG overrides, macOS
and Windows layouts, and the durable-config/plugin-data boundary.

The code also retains three Google credential representations: timestamped
credential files, the older shared `credential_id` registry, and service-local
OAuth data. That leaves multiple credential authorities and fallback orders.

## Path API

Add one public composition helper:

```python
def resolve_skill_config_dir(
    skill_name: str,
    *,
    platform: str,
    home: Path,
    environ: Mapping[str, str],
) -> Path:
    """Return the durable configuration directory for one Famulus skill."""
```

The name is intentionally different from the application-wide
`FamulusPaths.config_root`. It returns:

```python
_resolve_application_config_root(
    platform=platform,
    home=home,
    environ=environ,
) / skill_name
```

`_resolve_application_config_root` is an extraction of the existing config-root
branch inside `resolve_famulus_paths`, not a second resolver. Both public
functions call it. The complete resolver retains its existing validation of all
application roots and complete plugin context. The config-only helper validates
only `home` and the config override relevant to the selected platform, so
unrelated data/state overrides and incomplete plugin provenance cannot block
durable configuration lookup.

`skill_name` must match ASCII
`^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$`. Empty names, paths, `.` and `..`, and Windows
reserved basenames (`con`, `prn`, `aux`, `nul`, `com1`-`com9`, and
`lpt1`-`lpt9`) are rejected on every platform. Reuse `FamulusPathsError`
conventions for the validation error. Resolution performs no filesystem I/O.

`resolve_skill_config_dir` and the extracted common root calculation require
explicit `home`, `platform`, and `environ`; neither reads ambient state.
Existing runtime adapters may snapshot `Path.home()`, `sys.platform`, and
`os.environ` at their current boundary, including an existing module-level
default, and pass that snapshot to the helper. Remove service-specific path
overrides such as `EMAIL_CLIENT_CONFIG_DIR`, but do not refactor unrelated
runtime call graphs merely to move an already-single ambient snapshot.

Do not add per-skill fields to `FamulusPaths` or centralize leaf filenames.
Those approaches make the common module depend on every skill's internal
configuration schema.

## Canonical layout

```text
<CONFIG>/connect-google/
  client.json
  credentials/<authorization>.json
  selected-credential.json

<CONFIG>/cloud-files/
  config.json

<CONFIG>/online-calendar/
  config.json

<CONFIG>/email-client/
  accounts.json
```

`<CONFIG>` is `FamulusPaths.config_root`:

- Linux: `$XDG_CONFIG_HOME/famulus`, or `~/.config/famulus`
- macOS: `~/Library/Application Support/Famulus/config`
- Windows: `%APPDATA%\Famulus`

Plugin setup receipts and milestones remain under `FAMULUS_PLUGIN_DATA`. No
credential descriptor or service binding may be stored there or under the
installed plugin cache.

## Authentication ownership

`connect-google` is the only filesystem owner for Google OAuth client and
authorization descriptors. Timestamped descriptor files contain metadata and
opaque secret references, not raw client secrets or refresh tokens. Actual
secrets remain in the host keyring under `Famulus:connect-google`.

Timestamped credential files are the only Google authorization representation.
Remove the older `<CONFIG>/connect-google/credentials.json` registry and its
`credential_id` API.

Service owners persist only a normalized absolute `credential_file` after their
own live provider probe succeeds:

- `cloud-files` writes it to `config.json`.
- `online-calendar` writes it to `config.json`.
- `email-client` writes it to the selected account in `accounts.json`.

On successful binding, the atomic service writer removes `credential_id`,
`credentials_path`, and legacy OAuth metadata from that service record. A failed
probe leaves the complete prior record unchanged. It does not clear any keyring
entry.

## Remove before first release

Remove code that reads or writes:

```text
~/.config/cloud-files/client.json
~/.config/cloud-files/credentials.json
~/.config/online-calendar/client.json
~/.config/online-calendar/credentials.json
<CONFIG>/connect-google/credentials.json
email-client keyring entries addressed through account oauth metadata
```

`~/.config/google-oauth/client.json` has no current runtime reader in this
checkout. Treat it as a historical grep sentinel, not an implemented removal
target.

Remove these interfaces:

```text
cloud-files._rtx.interface.ensure-oauth
cloud-files._rtx.interface.setup-oauth
cloud-files._rtx.interface.use-google-credential
online-calendar._rtx.interface.ensure-oauth
online-calendar._rtx.interface.setup-oauth
online-calendar._rtx.interface.use-google-credential
email-client._rtx.interface.accounts-setup-oauth
email-client._rtx.interface.accounts-use-google-credential
```

Retain the three credential-file binders and `cloud-files`' non-authentication
`write-config` interface. Service setup routes through `connect-google`, then
the owning service's credential-file binder.

"Remove" means delete code readers/writers, tests, source nodes, schema fields,
and interface declarations. Never unlink or overwrite an existing legacy file
and never clear a legacy keyring entry. Cleanup is a separate destructive
operation outside this change.

## Implementation sequence

1. Extract the existing application config-root calculation and add
   `resolve_skill_config_dir`, its validation error, exports, blueprint contract,
   caller relationships, and cross-platform tests.
   `common.interface.famulus-paths` adds exactly `connect-google._rtx`,
   `cloud-files._rtx`, `online-calendar._rtx`, and `email-client._rtx` as callers;
   do not enable `allow_all_modules`. The credentials module is already allowed.
2. Route `connect-google`, `cloud-files`, `online-calendar`, and `email-client`
   through the helper without removing authentication behavior or schema fields.
   Pass existing runtime-adapter ambient snapshots to the explicit helper; do
   not introduce new ambient reads or refactor unrelated call graphs.
3. Remove the shared credential registry API and every service `credential_id`
   reader, writer, schema field, and interface while retaining service-local
   OAuth temporarily.
4. Remove service-local OAuth bootstrap and remaining legacy fallbacks. Keep
   credential-file refresh, live service probes, and email app-password behavior.
5. Within each of steps 2 through 4, update its authoritative blueprints,
   schema, skill prose, and documentation, regenerate its derived skill blocks
   and runtime dependency metadata, and validate/review that task before
   beginning the next one.

## Implementation file authority

The exhaustive modify/delete/generated file tables live in
`docs/plans/2026-09-08-famulus-google-config-coherence.md` and are authoritative
for execution. Files inspected during design but absent from those task tables
are inspect-only. Adding an authored file to a task requires scope review and,
when it increases a per-file or summary LOC cap, explicit user approval.

## LOC budget

The exact per-file budgets and their measurement rule live in
`docs/plans/2026-09-08-famulus-google-config-coherence.md`. In summary: path
centralization permits 78 production and 86 test addition lines, while the
new common path machinery remains capped at 27 lines; removing the
shared registry permits 10 and 15; removing service-local OAuth permits 10 and
15. Blueprint/docs additions and generated churn are reported separately.
Deletion-heavy cleanup cannot be used to justify exceeding an addition cap.

## Verification

Focused tests must establish:

- `resolve_skill_config_dir("cloud-files", ...)` returns the expected child of
  the Linux, macOS, and Windows Famulus config root.
- `XDG_CONFIG_HOME` moves all four Google-related configuration directories
  consistently.
- two valid paired `FAMULUS_HOST`/`FAMULUS_PLUGIN_DATA` contexts produce
  identical config paths and leave sentinel contents unchanged.
- `resolve_skill_config_dir` ignores ambient `os.environ`, incomplete plugin
  context, and invalid irrelevant data/state overrides, while rejecting an
  invalid config override used by the selected platform.
- invalid skill names, including Windows reserved names, cannot escape or alias
  the application config root.
- all service readers and writers use only their resolved config directory.
- binding stores the exact shared descriptor only after a successful live probe.
- failed probes preserve prior configuration byte-for-byte; successful probes
  leave exactly one `credential_file` authority without clearing secrets.
- no runtime source, schema, authored blueprint, or generated interface retains
  `credential_id`, `credentials_path`, service-local OAuth setup, or a removed
  legacy path.
- focused credential-file end-to-end tests pass, followed by blueprint and
  generated-doc validators and `repo_checks.py --suite precommit`.

## Non-goals

- Migrating, deleting, or clearing existing pre-release credentials.
- Moving secrets out of the host keyring.
- Storing configuration in plugin data.
- Centralizing skill-owned filenames or adding per-skill `FamulusPaths` fields.
- Inventing a second resolver, validator framework, atomic writer, secret store,
  or credential representation.
- Changing OAuth scopes, authorization policy, or service behavior.
- Treating a plugin setup receipt as proof that provider credentials work.

## Acceptance criteria

The work is complete when every Google-related local configuration path begins
with a directory returned by `resolve_skill_config_dir`, only `connect-google`
owns one Google OAuth descriptor format, services own only verified
`credential_file` bindings, no runtime legacy authority remains, the handwritten
diff stays within budget, and tests prove plugin-data identity cannot redirect
or erase authentication state. Cache independence is established by the path
API and a static assertion that no credential/configuration source depends on a
plugin-cache path; it is not claimed as a separate installed-copy integration
test.
