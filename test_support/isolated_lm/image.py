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

    Intent
    ------
    Restrict each initial or redirected network request to one of the three
    immutable Ubuntu artifact URLs authorized by this harness.

    Rationale
    ---------
    Detached-signature verification authenticates a manifest, but it does not
    authorize fetching that manifest from arbitrary network origins.

    Pseudocode
    ----------
    - if url is absent from the fixed allowlist:
      - raise unapproved source URL
    - return none

    Wraps
    -----
    none
    """
    if url not in ALLOWED_SOURCE_URLS:
        raise ValueError(f"unapproved cloud-image URL: {url}")


class _ApprovedRedirectHandler(HTTPRedirectHandler):
    """Validate every redirect target before urllib issues its next request.

    Intent
    ------
    Interpose the same closed URL allowlist on every redirect before urllib can
    issue the follow-up request.

    Rationale
    ---------
    Checking a response's final URL is too late: urllib may already have
    requested a redirect target outside the approved origin.

    Pseudocode
    ----------
    - set redirect_policy = validate target before inherited redirect handling
    - return redirect_policy

    Wraps
    -----
    none
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
        """Refuse an unapproved target before the redirect request is sent.

        Intent
        ------
        Validate the proposed redirect URL and delegate the approved request to
        urllib without changing status, headers, or request context.

        Rationale
        ---------
        Post-response checks cannot undo an unauthorized outbound request, so
        the target must be rejected inside urllib's redirect decision point.

        Pseudocode
        ----------
        - @_validate_source_url(new_url)
        - set redirected_request = inherited redirect request
        - return redirected_request

        Wraps
        -----
        none

        CallsFromRepo
        -------------
        ._validate_source_url:
          why:
            validates: "Rejects a redirect target before urllib constructs its next request."
        """
        try:
            _validate_source_url(new_url)
        except ValueError as error:
            raise ValueError(f"unapproved redirect target: {new_url}") from error
        return super().redirect_request(
            request, file_pointer, status_code, message, headers, new_url
        )


def _prepare_cache_directory(root: Path, directory: Path) -> Path:
    """Create one real cache directory without traversing symlinked components.

    Intent
    ------
    Establish an existing canonical cache descendant under the explicit runtime
    root without following any symlinked component.

    Rationale
    ---------
    A symlink at a state-cache component could redirect temporary downloads or
    the recorded cache path outside the explicitly selected runtime root.

    Pseudocode
    ----------
    - if root is not absolute and real:
      - raise invalid runtime root
    - set relative_directory = directory relative to root
    - for component in relative_directory:
      - if component is a symlink:
        - raise redirected cache directory
      - set component = existing or newly created directory
    - if resolved directory escapes root:
      - raise escaped cache directory
    - return resolved directory

    Wraps
    -----
    none
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

    Intent
    ------
    Select one exact filename-to-digest binding from authenticated checksum
    text and reject malformed or ambiguous target entries.

    Rationale
    ---------
    Substring selection could associate a trusted digest with a similarly
    named but different artifact; duplicate entries make source provenance
    ambiguous.

    Pseudocode
    ----------
    - set matches = empty digest collection
    - for line in checksum text:
      - if line names filename with malformed syntax:
        - raise malformed checksum entry
      - set matches = matches plus exact normalized digest
    - if matches does not contain exactly one digest:
      - raise ambiguous checksum entry
    - return sole digest

    Wraps
    -----
    none
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

    Intent
    ------
    Compute a stable SHA-256 digest for a nonempty regular payload without
    loading an image-sized file into memory.

    Rationale
    ---------
    Source images are large, so whole-file reads waste memory; an empty input
    must never be accepted as a successful image transfer.

    Pseudocode
    ----------
    - if path size is zero:
      - raise empty payload
    - set digest = new SHA-256 accumulator
    - for block in fixed-size file reads:
      - set digest = digest updated with block
    - return hexadecimal digest

    Wraps
    -----
    none
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

    Intent
    ------
    Authenticate the downloaded checksum manifest against one explicit Ubuntu
    keyring while containing all child-process output.

    Rationale
    ---------
    The host user's personal GnuPG configuration is not a trust source for
    Ubuntu cloud images; ``gpgv`` receives one explicit keyring instead.

    Pseudocode
    ----------
    - set verification_command = gpgv with explicit keyring signature and manifest
    - set verification_result = captured checked subprocess invocation
    - return none

    Wraps
    -----
    none
    """
    run(
        ["gpgv", "--keyring", str(keyring), str(signature), str(checksums)],
        capture_output=True,
        check=True,
    )


def download_atomic(
    url: str,
    destination: Path,
    *,
    expected_digest: str | None = None,
) -> Path:
    """Download an approved URL into a temporary file and atomically replace.

    Intent
    ------
    Fetch one allowlisted artifact into a same-directory temporary file,
    optionally verify its digest, and publish it atomically.

    Rationale
    ---------
    A failed or mismatched download must not become visible as a source image
    or overwrite an existing verified cache entry.

    Pseudocode
    ----------
    - @_validate_source_url(url)
    - @_ApprovedRedirectHandler()
    - set temporary_path = streamed allowlisted response
    - @_validate_source_url(effective_url)
    - if expected digest is present:
      - @sha256_file(temporary_path)
    - set destination = atomic replacement of temporary_path
    - return resolved destination

    Wraps
    -----
    none

    CallsFromRepo
    -------------
    ._validate_source_url:
      why:
        validates: "Applies the fixed allowlist before initial and effective URL use."
    ._ApprovedRedirectHandler:
      why:
        validates: "Installs pre-request redirect validation into the urllib opener."
    .sha256_file:
      why:
        validates: "Compares nonempty temporary bytes with an expected authenticated digest."
    """
    _validate_source_url(url)
    destination.parent.mkdir(parents=True, exist_ok=True)
    opener = build_opener(_ApprovedRedirectHandler())
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
    run: Callable[..., subprocess.CompletedProcess[object]] = subprocess.run,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> CloudImageRecord:
    """Acquire and authenticate the pinned Ubuntu image into the runtime cache.

    Intent
    ------
    Acquire the signed checksum evidence and matching Ubuntu image bytes, then
    return immutable provenance for the authenticated cache entry.

    Rationale
    ---------
    Image bytes are usable only when an Ubuntu-keyring-verified manifest names
    their exact digest; cache replacement happens after that byte check.

    Pseudocode
    ----------
    - downloads = _prepare_cache_directory(root and downloads path)
    - images = _prepare_cache_directory(root and images path)
    - checksums = download_atomic(checksum URL and destination)
    - signature = download_atomic(signature URL and destination)
    - @verify_signed_checksums(checksums signature and keyring)
    - digest = parse_sha256sums(authenticated manifest and image filename)
    - cached_path = download_atomic(image URL destination and digest)
    - return authenticated image record

    Wraps
    -----
    none

    CallsFromRepo
    -------------
    .verify_signed_checksums:
      why:
        validates: "Authenticates checksum metadata before its image binding is parsed."

    InstantiationsFromRepo
    ----------------------
    ._prepare_cache_directory:
      why:
        constructs: "Returns each canonical cache directory used for acquired artifacts."
    .download_atomic:
      why:
        constructs: "Returns published evidence and image paths after bounded acquisition checks."
    .parse_sha256sums:
      why:
        transforms: "Returns the sole authenticated digest carried into image download verification."
    """
    downloads = _prepare_cache_directory(paths.root, paths.downloads)
    images = _prepare_cache_directory(paths.root, paths.images)
    checksums = download_atomic(CHECKSUMS_URL, downloads / "SHA256SUMS")
    signature = download_atomic(SIGNATURE_URL, downloads / "SHA256SUMS.gpg")
    verify_signed_checksums(checksums, signature, KEYRING, run=run)
    digest = parse_sha256sums(checksums.read_text(encoding="utf-8"), IMAGE_FILENAME)
    cached_path = download_atomic(
        IMAGE_URL,
        images / IMAGE_FILENAME,
        expected_digest=digest,
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

    Intent
    ------
    Create one fresh sparse QCOW2 overlay only when its recorded backing image
    remains canonical, present, and byte-identical to authenticated provenance.

    Rationale
    ---------
    Reusing an overlay would contaminate a disposable scenario. Rechecking the
    record path and digest prevents a substituted local file from becoming a
    backing image after authenticated acquisition.

    Pseudocode
    ----------
    - if overlay destination is occupied:
      - raise reused overlay path
    - if backing image path is not canonical and regular:
      - raise invalid backing image
    - @sha256_file(backing image)
    - if current digest differs from verified digest:
      - raise substituted backing image
    - set overlay_result = captured checked qemu-img invocation
    - return resolved overlay path

    Wraps
    -----
    none

    CallsFromRepo
    -------------
    .sha256_file:
      why:
        validates: "Rehashes the current backing image before qemu-img receives its path."
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
            capture_output=True,
            check=True,
        )
    except Exception:
        if overlay.exists() or overlay.is_symlink():
            overlay.unlink()
        raise
    return overlay.resolve()
