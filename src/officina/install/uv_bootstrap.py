"""Bootstrap a machine-local, managed ``uv`` binary at ``FamulusPaths.uv_bin``
from the real astral-sh/uv GitHub release for the pinned
``install-info.toml``'s ``bootstrap.uv_version``.

Every fresh machine without ``uv`` already on ``PATH`` hard-fails during
install (see ``skills/install-assistant-tools/_rtx/_phase_entry.py``'s
``_build_managed_runtime_candidate``, which requires ``paths.uv_bin`` to
exist before calling ``officina.install.managed_runtime.build_candidate_release``).
This module is that missing bootstrap step.

This module is deliberately platform-name-free: it takes an already-resolved
release-asset target-triple and archive extension rather than an OS name,
because ``officina.install`` is shared, host-generic code (unlike
``skills/install-assistant-tools/_rtx/_install_scaffold.py``, which already
owns the canonical host-platform vocabulary via ``_platform_name()`` and,
for uv specifically, ``uv_release_target()`` -- that is the one place that
translates a platform name into uv's concrete release-asset naming).
Callers resolve the triple/extension pair there and pass it in here.

Verification: the real astral-sh/uv release for the pinned version publishes
a same-origin ``<asset>.sha256`` file next to every binary asset (plain
``sha256sum``-format text: ``<hex digest><two spaces><filename>``). That is
what this module checks the downloaded archive against. This only proves the
download was not corrupted or truncated in transit -- it does NOT prove the
release itself is authentic, because the checksum is published in the same
GitHub release as the artifact it covers (an attacker who could tamper with
the release could tamper with both). The uv release notes mention GitHub
Artifact Attestations (verifiable via ``gh attestation verify``) as a
stronger provenance mechanism, but no GPG/sigstore/cosign signature is
published, and shelling out to the ``gh`` CLI would violate this module's
stdlib-only, no-ambient-tool constraint -- so a same-origin sha256 checksum
is the realistic baseline implemented here, honestly documented as such.

Never invokes an ambient ``python``/``python3`` interpreter as the install
mechanism: downloading (``urllib.request``) and archive extraction
(``tarfile``/``zipfile``) are pure stdlib, run from whatever Python process
is already executing this module.
"""
from __future__ import annotations

import hashlib
import io
import subprocess
import tarfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import officina.common.atomic_files as atomic_files

_RELEASE_BASE_URL = "https://github.com/astral-sh/uv/releases/download"

_DEFAULT_NETWORK_TIMEOUT_SECONDS = 120.0
_DEFAULT_VERSION_CHECK_TIMEOUT_SECONDS = 30.0

# The one archive extension that uses a flat zip layout with an ".exe"
# suffixed member (uv's real release convention for that target family);
# every other extension is a gzipped tarball with a "uv-<triple>/uv" member.
_ZIP_ARCHIVE_EXTENSION = ".zip"


class UvBootstrapError(Exception):
    """Raised when a machine-local managed uv cannot be bootstrapped:
    unsupported release target, network failure, checksum mismatch, or
    archive-extraction failure."""


def _asset_name(*, triple: str, extension: str) -> str:
    return f"uv-{triple}{extension}"


def _current_version_matches(uv_bin: Path, version: str) -> bool:
    """Return whether ``uv_bin --version`` reports the pinned ``version``.

    Any failure to run it at all (missing, not executable, corrupted,
    unexpected non-zero exit) is treated as "does not match" rather than
    propagated, so the caller falls through to a real (re)bootstrap instead
    of crashing on a stale or broken binary.
    """
    try:
        result = subprocess.run(
            [str(uv_bin), "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=_DEFAULT_VERSION_CHECK_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False
    # Real ``uv --version`` output is "uv <version> (<commit> <date>)"; parse
    # out the exact version token rather than doing a substring check, so a
    # pinned version that happens to be a prefix of a different real version
    # (e.g. pin "0.11.2" vs. real "0.11.29") is not wrongly treated as a
    # match.
    parts = result.stdout.split()
    if len(parts) < 2:
        return False
    return parts[1] == version


def _fetch_bytes(url: str, *, timeout: float) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.read()
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise UvBootstrapError(f"could not download {url}: {exc}") from exc


def _verify_checksum(data: bytes, *, checksum_text: str, asset_name: str) -> None:
    stripped = checksum_text.strip()
    if not stripped:
        raise UvBootstrapError(f"empty checksum content for {asset_name}")
    # Real astral-sh/uv .sha256 sidecars are plain sha256sum-format text:
    # "<hex digest>  <filename>". Only the first whitespace-separated token
    # (the digest) is required here.
    expected = stripped.split()[0]
    actual = hashlib.sha256(data).hexdigest()
    if actual.casefold() != expected.casefold():
        raise UvBootstrapError(
            f"checksum mismatch for {asset_name}: expected {expected}, got {actual}"
        )


def _extract_uv_binary(archive_bytes: bytes, *, extension: str, triple: str) -> bytes:
    if extension == _ZIP_ARCHIVE_EXTENSION:
        member_name = "uv.exe"
        try:
            with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
                return archive.read(member_name)
        except (zipfile.BadZipFile, KeyError) as exc:
            raise UvBootstrapError(
                f"could not extract {member_name} from the downloaded archive: {exc}"
            ) from exc

    member_name = f"uv-{triple}/uv"
    try:
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as archive:
            member = archive.getmember(member_name)
            extracted = archive.extractfile(member)
            if extracted is None:
                raise UvBootstrapError(f"archive member is not a regular file: {member_name}")
            return extracted.read()
    except (tarfile.TarError, KeyError) as exc:
        raise UvBootstrapError(
            f"could not extract {member_name} from the downloaded archive: {exc}"
        ) from exc


def bootstrap_uv(
    *,
    uv_bin: Path,
    version: str,
    triple: str,
    archive_extension: str,
    network_timeout: float = _DEFAULT_NETWORK_TIMEOUT_SECONDS,
) -> None:
    """Ensure a machine-local managed ``uv`` binary matching ``version``
    exists at ``uv_bin``.

    No-op if ``uv_bin`` already exists and ``uv_bin --version`` reports
    ``version``. Otherwise downloads the real release asset
    ``uv-<triple><archive_extension>`` from the real astral-sh/uv GitHub
    release, verifies it against the real published same-origin sha256
    checksum (see this module's docstring for what that does and does not
    guarantee), and atomically installs the extracted binary to ``uv_bin``
    via ``officina.common.atomic_files.atomic_replace_bytes``.

    ``triple`` and ``archive_extension`` (e.g. ``".tar.gz"`` or ``".zip"``)
    must already be resolved by the caller -- see
    ``skills/install-assistant-tools/_rtx/_install_scaffold.py``'s
    ``uv_release_target()``.

    Raises ``UvBootstrapError`` on any failure: network failure, checksum
    mismatch, or archive-extraction failure. On any such failure, no binary
    is installed at ``uv_bin`` -- a prior binary (if any) is left untouched,
    since verification happens entirely in memory before the atomic install
    step runs.
    """
    if uv_bin.exists() and _current_version_matches(uv_bin, version):
        return

    asset_name = _asset_name(triple=triple, extension=archive_extension)
    archive_url = f"{_RELEASE_BASE_URL}/{version}/{asset_name}"
    checksum_url = f"{archive_url}.sha256"

    archive_bytes = _fetch_bytes(archive_url, timeout=network_timeout)
    checksum_bytes = _fetch_bytes(checksum_url, timeout=network_timeout)
    try:
        checksum_text = checksum_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise UvBootstrapError(
            f"could not decode checksum content for {asset_name} as UTF-8: {exc}"
        ) from exc
    _verify_checksum(
        archive_bytes,
        checksum_text=checksum_text,
        asset_name=asset_name,
    )

    binary_bytes = _extract_uv_binary(archive_bytes, extension=archive_extension, triple=triple)

    try:
        uv_bin.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise UvBootstrapError(f"could not create uv install directory {uv_bin.parent}: {exc}") from exc

    try:
        atomic_files.atomic_replace_bytes(
            uv_bin, binary_bytes, allowed_root=uv_bin.parent, mode=0o755
        )
    except atomic_files.AtomicWriteError as exc:
        raise UvBootstrapError(f"could not atomically install uv to {uv_bin}: {exc}") from exc


__all__ = ["UvBootstrapError", "bootstrap_uv"]
