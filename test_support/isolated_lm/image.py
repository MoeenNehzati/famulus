"""Authenticated Ubuntu cloud-image acquisition and disposable QCOW2 overlays.

Every network URL is constrained to the approved Ubuntu cloud-image origin.
The image cache receives bytes only after ``gpgv`` validates the checksum
manifest and the downloaded image matches its selected checksum.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
import hashlib
import os
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.request import HTTPRedirectHandler, build_opener

from test_support.isolated_lm.model import CloudImageRecord, RuntimePaths, VmResources


ALLOWED_IMAGE_ORIGIN = ("https", "cloud-images.ubuntu.com")
"""The sole scheme and host authorized for source-image acquisition."""

ALLOWED_PATH_PREFIX = "/noble/current/"
"""The Ubuntu 24.04 current-image directory authorized by this harness."""

IMAGE_FILENAME = "noble-server-cloudimg-amd64.img"
"""The exact Ubuntu Server 24.04 amd64 cloud-image filename."""

IMAGE_URL = f"https://cloud-images.ubuntu.com/noble/current/{IMAGE_FILENAME}"
CHECKSUMS_URL = "https://cloud-images.ubuntu.com/noble/current/SHA256SUMS"
SIGNATURE_URL = "https://cloud-images.ubuntu.com/noble/current/SHA256SUMS.gpg"
KEYRING = Path("/usr/share/keyrings/ubuntu-cloudimage-keyring.gpg")

ALLOWED_SOURCE_URLS = frozenset({IMAGE_URL, CHECKSUMS_URL, SIGNATURE_URL})
"""The complete, closed set of URLs this acquisition boundary may request."""

_SHA256SUM_LINE = re.compile(r"^([0-9A-Fa-f]{64}) [ *](.+)$")


def _validate_source_url(url: str) -> None:
    """Reject every source URL except the three explicitly approved artifacts.

    Rationale
    ---------
    Detached-signature verification authenticates a manifest, but it does not
    authorize fetching that manifest from arbitrary network origins.

    Pseudocode
    ----------
    - compare the supplied URL with the complete fixed allowlist
    - reject a different artifact even when its origin and path prefix match

    Call boundary
    -------------
    ``download_atomic`` and ``_ApprovedRedirectHandler`` call this before a
    network request, so a caller cannot widen the acquisition authority.
    """
    if url not in ALLOWED_SOURCE_URLS:
        raise ValueError(f"unapproved cloud-image URL: {url}")


class _ApprovedRedirectHandler(HTTPRedirectHandler):
    """Validate every redirect target before urllib issues its next request.

    Rationale
    ---------
    Checking a response's final URL is too late: urllib may already have
    requested a redirect target outside the approved origin.

    Pseudocode
    ----------
    - receive the proposed redirect URL before urllib follows it
    - validate the URL against the same approved origin and path namespace
    - delegate only an approved redirect to urllib's normal redirect behavior

    Call boundary
    -------------
    ``download_atomic`` installs one instance in every supplied opener factory,
    including test factories, so its redirect policy cannot be bypassed.
    """

    def redirect_request(
        self,
        request: object,
        file_pointer: object,
        status_code: int,
        message: str,
        headers: object,
        new_url: str,
    ) -> object:
        """Refuse an unapproved target before the redirect request is sent."""
        try:
            _validate_source_url(new_url)
        except ValueError as error:
            raise ValueError(f"unapproved redirect target: {new_url}") from error
        return super().redirect_request(
            request, file_pointer, status_code, message, headers, new_url
        )


def _prepare_cache_directory(root: Path, directory: Path) -> Path:
    """Create one real cache directory without traversing symlinked components.

    Rationale
    ---------
    A symlink at a state-cache component could redirect temporary downloads or
    the recorded cache path outside the explicitly selected runtime root.

    Pseudocode
    ----------
    - require the requested directory to be a descendant of the absolute root
    - create each missing component and reject a symlink or non-directory
    - resolve the final directory and require it to remain under the root

    Call boundary
    -------------
    ``prepare_cloud_image`` validates both download and image cache directories
    before it asks an opener to make any network request.
    """
    if not root.is_absolute():
        raise ValueError("runtime root must be absolute")
    if root.is_symlink():
        raise ValueError("runtime root must not be a symlink")
    root.mkdir(parents=True, exist_ok=True)
    if not root.is_dir():
        raise ValueError("runtime root must be a directory")
    try:
        relative = directory.relative_to(root)
    except ValueError as error:
        raise ValueError("cache directory must be below runtime root") from error
    current = root
    for component in relative.parts:
        current = current / component
        if current.is_symlink():
            raise ValueError(f"cache directory component is a symlink: {current}")
        current.mkdir(exist_ok=True)
        if current.is_symlink() or not current.is_dir():
            raise ValueError(f"cache directory is not a real directory: {current}")
    resolved_root = root.resolve()
    resolved_directory = current.resolve()
    if not resolved_directory.is_relative_to(resolved_root):
        raise ValueError("cache directory resolves outside runtime root")
    return resolved_directory


def parse_sha256sums(text: str, filename: str) -> str:
    """Extract exactly one SHA-256 digest for the exact requested filename.

    Rationale
    ---------
    Substring selection could associate a trusted digest with a similarly
    named but different artifact; duplicate entries make source provenance
    ambiguous.

    Pseudocode
    ----------
    - parse checksum-manifest lines that have the standard digest form
    - retain only entries whose filename equals the requested filename
    - require one retained entry and return its normalized lowercase digest

    Call boundary
    -------------
    ``prepare_cloud_image`` calls this only after ``verify_signed_checksums``
    accepts the detached signature for the downloaded manifest.
    """
    matches: list[str] = []
    for line in text.splitlines():
        parsed = _SHA256SUM_LINE.fullmatch(line)
        if line.endswith(filename):
            if parsed is None or parsed.group(2) != filename:
                raise ValueError(f"malformed checksum entry for {filename}")
            matches.append(parsed.group(1).lower())
    if len(matches) != 1:
        raise ValueError(f"expected exactly one checksum for {filename}")
    return matches[0]


def sha256_file(path: Path) -> str:
    """Hash a nonempty regular payload incrementally with SHA-256.

    Rationale
    ---------
    Source images are large, so whole-file reads waste memory; an empty input
    must never be accepted as a successful image transfer.

    Pseudocode
    ----------
    - reject zero-byte paths before calculating any digest
    - read fixed-size blocks and update one SHA-256 object
    - return the lowercase hexadecimal digest

    Call boundary
    -------------
    ``download_atomic`` hashes its still-temporary output before replacing a
    destination, and ``prepare_cloud_image`` records the resulting digest.
    """
    if path.stat().st_size == 0:
        raise ValueError("empty download cannot be verified")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_signed_checksums(
    checksums: Path,
    signature: Path,
    keyring: Path,
    *,
    run: Callable[..., subprocess.CompletedProcess[object]] = subprocess.run,
) -> None:
    """Verify checksum metadata with only the supplied trusted keyring.

    Rationale
    ---------
    The host user's personal GnuPG configuration is not a trust source for
    Ubuntu cloud images; ``gpgv`` receives one explicit keyring instead.

    Pseudocode
    ----------
    - construct the fixed detached-signature verification argument vector
    - invoke the injected subprocess boundary with check enabled
    - propagate a verification failure without parsing the manifest

    Call boundary
    -------------
    ``prepare_cloud_image`` calls this after atomically downloading the
    checksum and signature files and before reading either manifest entry.
    """
    run(
        ["gpgv", "--keyring", str(keyring), str(signature), str(checksums)],
        check=True,
    )


def download_atomic(
    url: str,
    destination: Path,
    *,
    expected_digest: str | None = None,
    opener_factory: Callable[[_ApprovedRedirectHandler], object] = build_opener,
) -> Path:
    """Download an approved URL into a temporary file and atomically replace.

    Rationale
    ---------
    A failed or mismatched download must not become visible as a source image
    or overwrite an existing verified cache entry.

    Pseudocode
    ----------
    - validate the origin before the network call and create the parent path
    - validate the effective response URL before streaming into a temporary file
    - optionally verify its nonempty SHA-256 digest, then replace destination
    - remove the temporary file on every failure path

    Call boundary
    -------------
    ``prepare_cloud_image`` injects only an opener factory for tests. Every
    factory receives the approved redirect handler before any request is made.
    """
    _validate_source_url(url)
    destination.parent.mkdir(parents=True, exist_ok=True)
    opener = opener_factory(_ApprovedRedirectHandler())
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=destination.parent, prefix=f".{destination.name}-", delete=False
        ) as temporary:
            temporary_name = temporary.name
            with opener.open(url) as response:  # type: ignore[union-attr]
                effective_url = response.geturl()  # type: ignore[union-attr]
                try:
                    _validate_source_url(effective_url)
                except ValueError as error:
                    raise ValueError(f"unapproved redirect target: {effective_url}") from error
                while block := response.read(1024 * 1024):  # type: ignore[union-attr]
                    temporary.write(block)
        temporary_path = Path(temporary_name)
        if expected_digest is not None and sha256_file(temporary_path) != expected_digest.lower():
            raise ValueError("download digest mismatch")
        os.replace(temporary_path, destination)
        temporary_name = None
        return destination.resolve()
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)


def prepare_cloud_image(
    paths: RuntimePaths,
    *,
    opener_factory: Callable[[_ApprovedRedirectHandler], object] = build_opener,
    run: Callable[..., subprocess.CompletedProcess[object]] = subprocess.run,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> CloudImageRecord:
    """Acquire and authenticate the pinned Ubuntu image into the runtime cache.

    Rationale
    ---------
    Image bytes are usable only when an Ubuntu-keyring-verified manifest names
    their exact digest; cache replacement happens after that byte check.

    Pseudocode
    ----------
    - atomically acquire checksum files under downloads and verify the signature
    - parse the single exact image checksum from verified manifest text
    - atomically download and digest-verify image bytes under images
    - return immutable UTC provenance for the resolved cached image

    Call boundary
    -------------
    The VM preparation layer supplies explicit ``RuntimePaths`` and consumes
    the returned ``CloudImageRecord``; tests inject approved-opener factories
    and subprocess fakes.
    """
    downloads = _prepare_cache_directory(paths.root, paths.downloads)
    images = _prepare_cache_directory(paths.root, paths.images)
    checksums = download_atomic(
        CHECKSUMS_URL, downloads / "SHA256SUMS", opener_factory=opener_factory
    )
    signature = download_atomic(
        SIGNATURE_URL, downloads / "SHA256SUMS.gpg", opener_factory=opener_factory
    )
    verify_signed_checksums(checksums, signature, KEYRING, run=run)
    digest = parse_sha256sums(checksums.read_text(encoding="utf-8"), IMAGE_FILENAME)
    cached_path = download_atomic(
        IMAGE_URL,
        images / IMAGE_FILENAME,
        expected_digest=digest,
        opener_factory=opener_factory,
    )
    retrieved_at = now().astimezone(UTC)
    return CloudImageRecord(
        schema_version=1,
        image_url=IMAGE_URL,
        checksums_url=CHECKSUMS_URL,
        signature_url=SIGNATURE_URL,
        filename=IMAGE_FILENAME,
        verified_source_digest=digest,
        byte_size=cached_path.stat().st_size,
        retrieved_at=retrieved_at,
        cached_path=cached_path,
    )


def create_overlay(
    image: CloudImageRecord,
    overlay: Path,
    resources: VmResources,
    *,
    run: Callable[..., subprocess.CompletedProcess[object]] = subprocess.run,
) -> Path:
    """Create a new sparse QCOW2 run disk over one still-verified source image.

    Rationale
    ---------
    Reusing an overlay would contaminate a disposable scenario. Rechecking the
    record path and digest prevents a substituted local file from becoming a
    backing image after authenticated acquisition.

    Pseudocode
    ----------
    - reject a pre-existing destination and unresolved/nonabsolute record path
    - require nonempty current bytes to match the record's verified digest
    - invoke qemu-img with the fixed QCOW2 backing format and requested size
    - return the resolved overlay path for the later run record

    Call boundary
    -------------
    Task 3 owns ``RunRecord`` serialization and records this returned path with
    the already-verified ``CloudImageRecord.verified_source_digest``.
    """
    if overlay.exists() or overlay.is_symlink():
        raise FileExistsError(f"overlay destination already exists: {overlay}")
    backing_image = image.cached_path
    if not backing_image.is_absolute():
        raise ValueError("backing image must be absolute")
    if backing_image != backing_image.resolve():
        raise ValueError("backing image record path must be resolved")
    if not backing_image.is_file():
        raise FileNotFoundError(f"backing image does not exist: {backing_image}")
    if sha256_file(backing_image) != image.verified_source_digest:
        raise ValueError("backing image digest does not match verified record")
    try:
        run(
            [
                "qemu-img", "create", "-f", "qcow2", "-F", "qcow2", "-b",
                str(backing_image), str(overlay), f"{resources.disk_gib}G",
            ],
            check=True,
        )
    except Exception:
        if overlay.exists() or overlay.is_symlink():
            overlay.unlink()
        raise
    return overlay.resolve()
