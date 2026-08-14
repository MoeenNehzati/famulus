# Unified Famulus Release Mechanism

Date: 2026-08-14

## Decision

Famulus uses one conventional GitHub release for its Python runtime, Claude
plugin, and Codex plugin. A release is one commit on `master`, one semantic
version, one immutable Git tag, and one GitHub Release.

There is no separate stable branch, package registry, or host-specific release
line. The Claude and Codex marketplaces distribute the same release commit,
which the Git tag identifies immutably.

## Release identity

`pyproject.toml` is the authoritative version source. The same
`MAJOR.MINOR.PATCH` value must appear in:

- `pyproject.toml`;
- `.claude-plugin/plugin.json`; and
- `.codex-plugin/plugin.json`.

The Git tag and GitHub Release use `vMAJOR.MINOR.PATCH`. The plugin name is
`famulus` and the existing marketplace name is `nullkit`, so both hosts
install `famulus@nullkit`. Renaming the marketplace is outside this release
mechanism.

A deterministic check rejects a release when these identities disagree.

## Branch and release policy

After the first release is promoted, `master` is the latest public release and
must remain installable. Development that is not ready for users stays on
feature branches.

Each release:

1. updates the authoritative version and both host manifests;
2. updates `CHANGELOG.md`;
3. passes the existing repository checks and secret scan;
4. merges the release commit to `master`;
5. creates and pushes an annotated `vMAJOR.MINOR.PATCH` tag;
6. creates a GitHub Release from that existing tag; and
7. verifies one public Claude install and one public Codex install.

The release uses GitHub's generated source archive. Famulus does not publish a
separate Python wheel, npm package, or custom plugin archive: the installer
builds the first-party Python wheel from the installed plugin content.

## User update path

Users add the `nullkit` marketplace once. A new release changes the shared
plugin version, so the host's normal marketplace refresh and plugin update
path delivers the next release. Public documentation lists the update commands
for both hosts.

Development commits are not distributed because they remain off `master`.

## Release checks

A small repository check validates:

- the three version fields match and use semantic version syntax;
- the requested release tag equals `v` plus that version;
- the changelog contains that version;
- both marketplace manifests use `nullkit` and expose the `famulus` plugin;
  and
- the working release commit contains the runtime lock, third-party notices,
  and required license files.

CI runs the non-publishing checks. Publishing remains an explicit maintainer
action documented in `docs/releasing.md`.

## Failure and withdrawal

Tags are never moved or reused. If a published release is faulty, the
maintainer reverts or fixes the problem on a branch and publishes the next
patch version. A seriously broken release is marked withdrawn in its GitHub
Release notes, with the replacement or previous usable version linked.

No automatic publishing occurs on ordinary pushes. This prevents an
unfinished version bump or unrelated `master` push from creating a public
release.

## Verification

Implementation is complete when:

- the unified identity check has passing and mismatch tests;
- Claude and Codex local marketplace-install tests use `famulus@nullkit`;
- the release checklist names the exact commands for checking, tagging,
  pushing, publishing, updating, and withdrawing;
- CI runs the identity check; and
- the public-release plan points to this mechanism.

## Explicitly deferred

PyPI or npm publication, a release branch, signed artifacts, SBOMs, provenance
attestations, automatic tag publishing, and a separate marketplace repository
are not part of the first release.
