# Famulus First Public Release Plan

Status: in progress.

## Goal

Publish a small, usable first release that:

- installs without the maintainer's checkout or private knowledge;
- uses known dependency versions;
- explains what is supported and how to use it; and
- can be versioned, published, updated, and withdrawn predictably.

## Already complete

- [x] Confirm tracked files and Git history do not expose credentials or private
  state.
- [x] Document the implemented security and privacy boundary in
  [`docs/security-and-privacy.md`](../security-and-privacy.md) and
  [`SECURITY.md`](../../SECURITY.md).
- [x] Label Google integrations and recurring agent workflows experimental.
- [x] Implement pinned `uv`, managed Python, and a blueprint-derived runtime
  lock consumed by the installer with hash enforcement.

## 1. Know what is shipped

- [ ] Record the exact upstream versions of vendored ELK and MathJax assets.
- [ ] Include the applicable third-party notices and license texts.
- [x] Close the runtime-lock audit gap: bind offline validation to the complete
  generated lock, reject wildcard pins, publish only after successful
  validation, and rerun the clean release-payload installation test.
- [ ] Confirm the release payload contains the documented runtime lock and no
  untracked local state.

## 2. Finish the public documentation

- [ ] Keep the README focused on purpose, prerequisites, supported workflows,
  installation, and one credential-free example.
- [ ] Document update, reinstall, uninstall, purge, troubleshooting, and known
  platform limitations.
- [ ] Publish a small support matrix that distinguishes supported workflows
  from experimental ones.
- [x] Use `famulus` consistently as the plugin name and `nullkit` as the
  marketplace name; remove machine-specific paths, personal defaults, and
  private-service assumptions.

## 3. Verify portability and installation

- [ ] Choose the operating systems, Python version, and Claude/Codex hosts
  claimed as supported for the first release.
- [ ] Run a cold install on each claimed platform using only the public
  instructions and release payload.
- [ ] Run one credential-free workflow from the installed copy.
- [ ] Test update/reinstall and retained-data uninstall; document any purge or
  single-install limitation that remains.
- [ ] Confirm no supported path depends on the maintainer checkout.

## 4. Establish the release mechanism

- [ ] Adopt semantic versioning and one authoritative version used by all
  shipped manifests.
- [ ] Add a short release checklist or command covering version, changelog,
  tests, payload inspection, and the existing CI secret scan.
- [ ] Make advertised installation commands resolve to a tagged release or
  other protected stable source rather than the development branch.
- [ ] Publish a version tag and GitHub Release with install instructions,
  release notes, and a simple withdrawal/rollback procedure.
- [ ] After publishing, install once from the public instructions and verify
  the reported version.

## First-release gate

The release is ready when:

- [ ] dependency and vendored-asset versions are recorded;
- [ ] the public documentation matches the tested install and supported scope;
- [ ] cold install, credential-free use, update/reinstall, and uninstall pass
  on every claimed platform; and
- [ ] the tag, GitHub Release, and advertised installation source all identify
  the same version.

## Not required for the first release

SBOMs, signing infrastructure, custom vendored-file hash systems, unattended
Google or recurring-workflow support, exhaustive platform coverage, broad
repository cleanup, release-cadence automation, and contributor-process
templates are deferred unless they become a concrete release blocker.
