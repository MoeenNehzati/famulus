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
import time
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

NETWORK_CONNECT_TIMEOUT_SECONDS = 30.0
NETWORK_READ_TIMEOUT_SECONDS = 30.0
NETWORK_TOTAL_TIMEOUT_SECONDS = 900.0


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
    connect_timeout_seconds: float = NETWORK_CONNECT_TIMEOUT_SECONDS,
    read_timeout_seconds: float = NETWORK_READ_TIMEOUT_SECONDS,
    total_timeout_seconds: float = NETWORK_TOTAL_TIMEOUT_SECONDS,
    monotonic: Callable[[], float] = time.monotonic,
    _deadline: float | None = None,
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
    ._set_response_read_timeout:
      why:
        writes: "Applies the smaller per-read and total budgets to each response read."
    """
    for label, value in (
        ("network connect timeout", connect_timeout_seconds),
        ("network read timeout", read_timeout_seconds),
        ("network total timeout", total_timeout_seconds),
    ):
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not 0 < float(value) < float("inf")
        ):
            raise ValueError(f"{label} must be finite and positive")
    _validate_source_url(url)
    destination.parent.mkdir(parents=True, exist_ok=True)
    opener = build_opener(_ApprovedRedirectHandler())
    deadline = (
        float(_deadline)
        if _deadline is not None
        else monotonic() + float(total_timeout_seconds)
    )
    directory_flags = (
        os.O_RDONLY
        | os.O_DIRECTORY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    parent_descriptor = os.open(destination.parent, directory_flags)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=destination.parent, prefix=f".{destination.name}-", delete=False
        ) as temporary:
            temporary_name = temporary.name
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise TimeoutError("network download deadline expired")
            with opener.open(  # type: ignore[union-attr]
                url,
                timeout=min(float(connect_timeout_seconds), remaining),
            ) as response:
                if monotonic() >= deadline:
                    raise TimeoutError("network download deadline expired")
                effective_url = response.geturl()  # type: ignore[union-attr]
                try:
                    _validate_source_url(effective_url)
                except ValueError as error:
                    raise ValueError(f"unapproved redirect target: {effective_url}") from error
                while True:
                    remaining = deadline - monotonic()
                    if remaining <= 0:
                        raise TimeoutError("network download deadline expired")
                    _set_response_read_timeout(
                        response,
                        min(float(read_timeout_seconds), remaining),
                    )
                    block = response.read(1024 * 1024)  # type: ignore[union-attr]
                    if monotonic() > deadline:
                        raise TimeoutError("network download deadline expired")
                    if not block:
                        break
                    temporary.write(block)
            temporary.flush()
            os.fsync(temporary.fileno())
        temporary_path = Path(temporary_name)
        if expected_digest is not None and sha256_file(temporary_path) != expected_digest.lower():
            raise ValueError("download digest mismatch")
        os.replace(temporary_path, destination)
        os.fsync(parent_descriptor)
        temporary_name = None
        return destination.resolve()
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)
        os.close(parent_descriptor)


def _set_response_read_timeout(response: object, seconds: float) -> None:
    """Apply one explicit timeout to the active urllib response socket.

    Intent
    ------
    Bound the next response read by the lesser of the read timeout and total
    budget remaining.

    Rationale
    ---------
    A connection timeout alone does not guarantee later response reads remain
    bounded; urllib exposes the active socket through its HTTP response stack.

    Pseudocode
    ----------
    - set timeout_setter = direct response seam or urllib buffered response socket seam
    - if timeout_setter is unavailable:
      - raise unsupported bounded response
    - set read_timeout = applied through timeout_setter
    - return after timeout configuration

    Wraps
    -----
    none
    """
    direct = getattr(response, "settimeout", None)
    if callable(direct):
        direct(seconds)
        return
    file_pointer = getattr(response, "fp", None)
    raw = getattr(file_pointer, "raw", None)
    sock = getattr(raw, "_sock", None)
    setter = getattr(sock, "settimeout", None)
    if not callable(setter):
        raise RuntimeError("network response does not expose a bounded read socket")
    setter(seconds)


def _staging_path(directory: Path, canonical_name: str) -> Path:
    """Reserve and release one unpredictable same-filesystem staging name.

    Intent
    ------
    Produce a currently absent private path beside canonical evidence for the
    existing atomic downloader to populate.

    Rationale
    ---------
    Authentication must operate on unpublished files, while same-directory
    staging keeps later publication on the canonical filesystem.

    Pseudocode
    ----------
    - set reserved_path = unpredictable private temporary entry in evidence directory
    - set reserved_path = absent after closing and removing reservation
    - return reserved_path

    Wraps
    -----
    none
    """
    descriptor, name = tempfile.mkstemp(
        prefix=f".{canonical_name}.staging-", dir=directory
    )
    os.close(descriptor)
    path = Path(name)
    path.unlink()
    return path


def _publish_staged(staged: Path, canonical: Path) -> None:
    """Durably publish one authenticated staged evidence artifact.

    Intent
    ------
    Replace a canonical evidence name only from a same-directory staged file
    and synchronize that directory after the replacement.

    Rationale
    ---------
    Publication before authentication can destroy prior trusted evidence, and
    replacement without directory fsync may be lost after a host crash.

    Pseudocode
    ----------
    - if staged and canonical paths do not share a parent:
      - raise invalid evidence staging path
    - set parent_descriptor = retained no-follow directory descriptor
    - set canonical = atomic replacement from staged
    - set parent_directory = synchronized through parent_descriptor
    - return after descriptor close

    Wraps
    -----
    none
    """
    if staged.parent != canonical.parent:
        raise ValueError("staged evidence must share the canonical directory")
    flags = (
        os.O_RDONLY
        | os.O_DIRECTORY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(canonical.parent, flags)
    try:
        os.replace(staged, canonical)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def prepare_cloud_image(
    paths: RuntimePaths,
    *,
    run: Callable[..., subprocess.CompletedProcess[object]] = subprocess.run,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    connect_timeout_seconds: float = NETWORK_CONNECT_TIMEOUT_SECONDS,
    read_timeout_seconds: float = NETWORK_READ_TIMEOUT_SECONDS,
    total_timeout_seconds: float = NETWORK_TOTAL_TIMEOUT_SECONDS,
    monotonic: Callable[[], float] = time.monotonic,
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
    .download_atomic:
      why:
        orchestrates: "Acquires staged evidence and verified image bytes within one total budget."
    ._publish_staged:
      why:
        writes: "Durably publishes authenticated evidence only after staged-pair verification."
    .verify_signed_checksums:
      why:
        validates: "Authenticates checksum metadata before its image binding is parsed."

    InstantiationsFromRepo
    ----------------------
    ._prepare_cache_directory:
      why:
        constructs: "Returns each canonical cache directory used for acquired artifacts."
    ._staging_path:
      why:
        constructs: "Returns unpredictable same-directory unpublished evidence paths."
    .download_atomic:
      why:
        constructs: "Returns published evidence and image paths after bounded acquisition checks."
    .parse_sha256sums:
      why:
        transforms: "Returns the sole authenticated digest carried into image download verification."
    """
    downloads = _prepare_cache_directory(paths.root, paths.downloads)
    images = _prepare_cache_directory(paths.root, paths.images)
    if (
        not isinstance(total_timeout_seconds, (int, float))
        or isinstance(total_timeout_seconds, bool)
        or not 0 < float(total_timeout_seconds) < float("inf")
    ):
        raise ValueError("network total timeout must be finite and positive")
    deadline = monotonic() + float(total_timeout_seconds)
    staged_checksums = _staging_path(downloads, "SHA256SUMS")
    staged_signature = _staging_path(downloads, "SHA256SUMS.gpg")
    try:
        download_atomic(
            CHECKSUMS_URL,
            staged_checksums,
            connect_timeout_seconds=connect_timeout_seconds,
            read_timeout_seconds=read_timeout_seconds,
            total_timeout_seconds=total_timeout_seconds,
            monotonic=monotonic,
            _deadline=deadline,
        )
        download_atomic(
            SIGNATURE_URL,
            staged_signature,
            connect_timeout_seconds=connect_timeout_seconds,
            read_timeout_seconds=read_timeout_seconds,
            total_timeout_seconds=total_timeout_seconds,
            monotonic=monotonic,
            _deadline=deadline,
        )
        verify_signed_checksums(staged_checksums, staged_signature, KEYRING, run=run)
        digest = parse_sha256sums(
            staged_checksums.read_text(encoding="utf-8"), IMAGE_FILENAME
        )
        _publish_staged(staged_checksums, downloads / "SHA256SUMS")
        _publish_staged(staged_signature, downloads / "SHA256SUMS.gpg")
        cached_path = download_atomic(
            IMAGE_URL,
            images / IMAGE_FILENAME,
            expected_digest=digest,
            connect_timeout_seconds=connect_timeout_seconds,
            read_timeout_seconds=read_timeout_seconds,
            total_timeout_seconds=total_timeout_seconds,
            monotonic=monotonic,
            _deadline=deadline,
        )
    finally:
        staged_checksums.unlink(missing_ok=True)
        staged_signature.unlink(missing_ok=True)
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
