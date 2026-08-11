# Famulus Public Release Readiness Plan

Status: proposed as of 2026-08-11.

## Goal

Prepare Famulus for its first deliberately promoted public release: a versioned
release that an external user can install, understand, trust, use, diagnose,
and remove without the maintainer's checkout or private knowledge.

The repository is already public. This plan covers readiness to advertise it,
not initial publication.

## Release boundary

The first promoted release does not need feature expansion or exhaustive
repository beautification. It needs:

- a small, explicitly supported workflow surface;
- an honest security and privacy boundary;
- a stable release channel;
- a tested external-user lifecycle; and
- removal of obsolete components that affect the shipped or documented path.

Work is grouped into four independently reviewable workstreams. Each should
receive its own implementation plan before execution.

## Workstream 1: Security and trust

**Outcome:** Users can understand what Famulus accesses and can safely connect,
use, disconnect, and remove its supported integrations.

### Immediate credential response

- [ ] Remove credentials from local repository remote configuration, rotate
  anything exposed beyond its intended store, and use credential-free remotes.
- [ ] Confirm that the Git history and exact shipped payload contain no secrets,
  OAuth clients, tokens, personal data, or generated private state.

### Document the trust boundary

- [ ] Audit the supported workflows' permissions, external services, local
  reads and writes, subprocesses, scheduled jobs, and consequential actions.
- [ ] Document credential and data locations, ownership, removal, OAuth scopes,
  telemetry behavior, and data sent through Claude or Codex.
- [ ] Treat external email, calendar, Drive, web, and document content as data,
  not authority. Define deterministic authorization or confirmation boundaries
  for sends, deletes, credential access, subprocesses, and permission changes.
- [ ] Publish `SECURITY.md` and one linked security/privacy page covering
  vulnerability reporting, permissions, destructive actions, disconnect,
  revocation, uninstall, and purge.

### Apply proportionate hardening

- [ ] Minimize OAuth scopes and privileged actions where supported behavior
  permits; justify broad permissions that remain.
- [ ] Make disconnect, revocation, retained-data uninstall, and explicit purge
  complete across all supported credential stores and scheduled integrations.
- [ ] Prevent secrets and private data from appearing in arguments, diagnostics,
  errors, fixtures, logs, caches, or plugin/source directories; enforce secret
  scanning in CI.
- [ ] Verify dependency and executable-bootstrap provenance, reproducibility,
  license compatibility, and required third-party attribution.
- [ ] Test the credential lifecycle and the authorization boundaries of
  advertised workflows that consume untrusted content or can change external
  state.

## Workstream 2: Canonical release system

**Outcome:** One documented path turns a frozen, reviewed commit into the exact
version installed by the advertised Claude and Codex commands.

### Define the public release contract

- [ ] Adopt semantic versioning and one authoritative version synchronized
  across Python, Claude, and Codex manifests.
- [ ] Publish an honest support matrix for Python, operating systems, and host
  CLI versions, distinguishing tested support from best effort.
- [ ] Identify the small set of supported public workflows and their
  prerequisites. Label other documented skills experimental or
  maintainer-facing, and require end-to-end tests only for advertised supported
  workflows.

### Establish the stable release channel

- [ ] Use a frozen release-candidate commit with defined required checks.
- [ ] Provide one release entrypoint or unambiguous checklist that validates
  version, changelog entry, release notes, and release gates.
- [ ] Make the exact advertised marketplace commands resolve to the promoted
  commit rather than mutable development state. If a host cannot install from a
  tag, use an equivalent protected stable source.
- [ ] Publish a version tag and GitHub Release, and document a minimal rollback
  or withdrawal procedure.

### Test the distributed lifecycle

- [ ] Make pull-request checks test the commit under review, and make
  release-candidate checks cover the declared support matrix without requiring
  privileged secrets.
- [ ] Run a genuinely cold install for every fully supported host using the
  exact README commands, empty Famulus/tool/dependency caches, and no access to
  the maintainer checkout.
- [ ] Verify the installed version and source revision, inspect the actual
  shipped payload, and confirm installed files and runtime dependencies match
  the release documentation.
- [ ] Run a credential-free useful workflow after installation.
- [ ] Test reinstall and host plugin-cache replacement without losing user data,
  retaining stale paths, or storing durable state in disposable plugin content.
- [ ] Test retained-data uninstall and explicit purge. Either make resource
  ownership safe or document and enforce a single-install limitation.
- [ ] Verify the public documentation and install path immediately after
  publishing the release.

## Workstream 3: External-user experience

**Outcome:** A newcomer can understand Famulus, reach a useful result, diagnose
a failure, and reverse the installation using only public material.

### Normalize identity and onboarding

- [ ] Replace the old `nullkit` marketplace identity with a Famulus-specific
  public name across manifests, commands, documentation, and tests. Add
  compatibility handling only if a concrete installed consumer requires it.
- [ ] Align product name, package names, descriptions, author information,
  versions, and URLs, and explain the Famulus/Officina relationship without
  requiring framework knowledge.
- [ ] Keep the README user-facing: explain the purpose, supported workflows,
  prerequisites, shortest installation path, expected success result, and one
  concrete example.
- [ ] Provide a five-minute credential-free first-use path before optional
  Google integrations.
- [ ] Document update, reinstall, uninstall, purge, troubleshooting, platform
  limitations, and where to report bugs or security problems.
- [ ] Provide a minimal safe version/environment diagnostic, using existing
  commands where sufficient, so bug reports can identify the installed release
  without exposing secrets.
- [ ] Remove machine-specific paths, personal accounts, private services,
  unexplained terminology, and hidden personal defaults from retained public
  documentation, examples, profiles, and errors.
- [ ] Set basic repository metadata and ensure the license, changelog, user
  documentation, bug-reporting route, and private security-reporting route are
  reachable.

### External-user gate

- [ ] Have an uninvolved technically capable user complete installation and the
  credential-free first-use path using only public documentation.
- [ ] Fix blockers and misleading instructions found in that walkthrough;
  defer cosmetic preferences.

## Workstream 4: Bounded obsolete removal

**Outcome:** No obsolete component is shipped, invoked, or advertised in a way
that confuses users or enlarges the supported surface.

### Retire `script_dispatcher`

- [ ] Identify every import, CLI entrypoint, installer action, hook, validator,
  blueprint, test, and documentation reference that consumes
  `script_dispatcher`.
- [ ] Confirm and migrate each live consumer to the canonical Officina
  dispatcher, then remove the compatibility package and its packaging/path
  configuration together.
- [ ] Verify dispatcher invocation, installation, uninstall, hooks, validators,
  and supported-platform tests after removal.

### Limit further cleanup to release impact

- [ ] Remove or correct other obsolete names, paths, wrappers, migrations, or
  artifacts only when they are shipped, invoked, advertised, misleading,
  insecure, or license-relevant.
- [ ] Require evidence of non-use and a full stale-reference search for each
  removal; defer general tidying and historical archiving.

## Execution order

1. Resolve credential exposure and audit the security boundary.
2. Establish the version, stable channel, and release gates.
3. Retire `script_dispatcher` and any other proven release-affecting obsolete
   surface.
4. Finalize naming, README, user documentation, and diagnostics.
5. Freeze the candidate, run cold lifecycle tests and the external-user
   walkthrough, publish the release, and verify the public install.
6. Advertise only the tested release and its matching documentation.

## Final promotion gate

Famulus is ready to advertise when:

- [ ] a clean, frozen candidate passes the required checks on every claimed
  supported platform;
- [ ] the security/privacy documentation matches verified behavior, permissions
  are justified, and secret/payload scans are clean;
- [ ] the exact advertised Claude and Codex commands install the promoted
  version and its credential-free smoke workflow succeeds;
- [ ] reinstall, plugin-cache replacement, uninstall, and purge preserve the
  documented ownership of user data and credentials;
- [ ] no supported path depends on a maintainer checkout, private service,
  machine-specific path, or unpublished knowledge;
- [ ] public installation, first-use, troubleshooting, limitations, license,
  support, and security-reporting material is reachable and has passed the
  uninvolved-user walkthrough; and
- [ ] the version tag, GitHub Release, changelog entry, release notes, and
  rollback path exist.

## Deferred unless a concrete blocker appears

The following are useful later but do not block the first promoted release:
release-cadence machinery, elaborate compatibility policy, latest-host canaries,
multi-install support, SBOM/signing infrastructure, badges, code of conduct,
pull-request templates, contributor-environment automation, completed-plan
archiving, historical-fixture cleanup, and aesthetic asset deduplication.
