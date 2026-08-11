"""Tests for authenticated isolated-VM cloud-image acquisition."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from subprocess import CalledProcessError, CompletedProcess

import pytest

from test_support.isolated_lm.image import (
    CHECKSUMS_URL,
    IMAGE_FILENAME,
    IMAGE_URL,
    SIGNATURE_URL,
    create_overlay,
    download_atomic,
    parse_sha256sums,
    prepare_cloud_image,
    verify_signed_checksums,
)
from test_support.isolated_lm.model import RuntimePaths, VmResources


class _Response:
    """Provide a context-managed byte stream at the urllib boundary."""

    def __init__(
        self,
        data: bytes,
        *,
        fail_after_first_read: bool = False,
        final_url: str = IMAGE_URL,
    ) -> None:
        self._data = data
        self._offset = 0
        self._fail_after_first_read = fail_after_first_read
        self._final_url = final_url

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        if self._fail_after_first_read and self._offset:
            raise OSError("interrupted download")
        if size < 0:
            size = len(self._data)
        block = self._data[self._offset:self._offset + size]
        self._offset += len(block)
        return block

    def geturl(self) -> str:
        return self._final_url


def test_parse_sha256sums_selects_exact_image_once() -> None:
    """Reject filename prefixes instead of accepting a different trusted image."""
    text = (
        f"{'b' * 64} *noble-server-cloudimg-amd64.img.backup\n"
        f"{'a' * 64} *noble-server-cloudimg-amd64.img\n"
    )

    assert parse_sha256sums(text, "noble-server-cloudimg-amd64.img") == "a" * 64


@pytest.mark.parametrize(
    "text",
    [
        "not-a-digest *noble-server-cloudimg-amd64.img\n",
        f"{'a' * 64} noble-server-cloudimg-amd64.img extra\n",
        f"{'a' * 64} *noble-server-cloudimg-amd64.img\n"
        f"{'b' * 64} *noble-server-cloudimg-amd64.img\n",
    ],
    ids=["malformed-digest", "malformed-filename", "duplicate"],
)
def test_parse_sha256sums_rejects_malformed_or_duplicate_target(text: str) -> None:
    """Require exactly one syntactically valid checksum for the requested image."""
    with pytest.raises(ValueError):
        parse_sha256sums(text, "noble-server-cloudimg-amd64.img")


def test_parse_sha256sums_rejects_a_malformed_target_alongside_valid_entry() -> None:
    """Do not silently ignore a competing malformed entry for the target name."""
    text = (
        f"{'a' * 64} *noble-server-cloudimg-amd64.img\n"
        "not-a-digest *noble-server-cloudimg-amd64.img\n"
    )

    with pytest.raises(ValueError):
        parse_sha256sums(text, "noble-server-cloudimg-amd64.img")


def test_verify_signed_checksums_uses_only_trusted_keyring(tmp_path: Path) -> None:
    """Pin detached-signature verification to the distribution keyring only."""
    calls: list[list[str]] = []

    verify_signed_checksums(
        tmp_path / "SHA256SUMS",
        tmp_path / "SHA256SUMS.gpg",
        Path("/usr/share/keyrings/ubuntu-cloudimage-keyring.gpg"),
        run=lambda argv, **kwargs: calls.append(argv) or CompletedProcess(argv, 0),
    )

    assert calls == [[
        "gpgv", "--keyring", "/usr/share/keyrings/ubuntu-cloudimage-keyring.gpg",
        str(tmp_path / "SHA256SUMS.gpg"), str(tmp_path / "SHA256SUMS"),
    ]]


@pytest.mark.parametrize(
    "url",
    [
        "http://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img",
        "https://mirror.example/noble/current/noble-server-cloudimg-amd64.img",
        "https://cloud-images.ubuntu.com/jammy/current/noble-server-cloudimg-amd64.img",
        "https://cloud-images.ubuntu.com/noble/current/%2e%2e/jammy/current/image",
    ],
    ids=["non-https", "wrong-host", "wrong-path-prefix", "encoded-path-traversal"],
)
def test_download_atomic_rejects_unapproved_image_origins(tmp_path: Path, url: str) -> None:
    """Refuse a download before an unapproved URL reaches the network boundary."""
    with pytest.raises(ValueError):
        download_atomic(url, tmp_path / "image", urlopen=lambda _: _Response(b"image"))


def test_download_atomic_rejects_a_redirect_outside_the_approved_origin(
    tmp_path: Path,
) -> None:
    """Do not let urllib request an unapproved redirect target."""
    calls: list[str] = []

    class _RedirectingOpener:
        def __init__(self, handler: object) -> None:
            self._handler = handler

        def open(self, url: str) -> object:
            calls.append(url)
            return self._handler.redirect_request(  # type: ignore[union-attr]
                object(), None, 302, "redirect", {},
                "https://mirror.example/noble/current/image",
            )

    with pytest.raises(ValueError, match="unapproved redirect"):
        download_atomic(
            IMAGE_URL,
            tmp_path / IMAGE_FILENAME,
            build_opener=lambda handler: _RedirectingOpener(handler),
        )

    assert calls == [IMAGE_URL]


def test_download_atomic_discards_partial_file_when_transfer_fails(tmp_path: Path) -> None:
    """Leave no visible destination or temporary payload after a failed transfer."""
    destination = tmp_path / IMAGE_FILENAME

    with pytest.raises(OSError, match="interrupted download"):
        download_atomic(
            IMAGE_URL,
            destination,
            urlopen=lambda _: _Response(b"partial", fail_after_first_read=True),
        )

    assert not destination.exists()
    assert list(tmp_path.iterdir()) == []


def test_download_atomic_rejects_digest_mismatch_without_replacing_destination(
    tmp_path: Path,
) -> None:
    """Keep a pre-existing cache entry when newly downloaded bytes are untrusted."""
    destination = tmp_path / IMAGE_FILENAME
    destination.write_bytes(b"known-good")

    with pytest.raises(ValueError, match="download digest mismatch"):
        download_atomic(
            IMAGE_URL,
            destination,
            expected_digest="a" * 64,
            urlopen=lambda _: _Response(b"different"),
        )

    assert destination.read_bytes() == b"known-good"
    assert list(tmp_path.iterdir()) == [destination]


def test_download_atomic_rejects_empty_payload_even_when_its_digest_matches(
    tmp_path: Path,
) -> None:
    """Never treat an empty transfer as a verified cloud-image payload."""
    destination = tmp_path / IMAGE_FILENAME

    with pytest.raises(ValueError, match="empty download"):
        download_atomic(
            IMAGE_URL,
            destination,
            expected_digest=(
                "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
            ),
            urlopen=lambda _: _Response(b""),
        )

    assert not destination.exists()


def test_prepare_cloud_image_verifies_signature_before_caching_image(tmp_path: Path) -> None:
    """Authenticate checksum metadata before transferring bytes into the image cache."""
    paths = RuntimePaths.from_root(tmp_path / "state")
    image = b"abc"
    digest = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    events: list[str] = []
    responses = {
        CHECKSUMS_URL: _Response(f"{digest} *{IMAGE_FILENAME}\n".encode()),
        SIGNATURE_URL: _Response(b"signature"),
        IMAGE_URL: _Response(image),
    }

    record = prepare_cloud_image(
        paths,
        urlopen=lambda url: events.append(f"download:{url}") or responses[url],
        run=lambda argv, **kwargs: events.append("verify-signature")
        or CompletedProcess(argv, 0),
        now=lambda: datetime(2026, 8, 11, 12, 0, tzinfo=UTC),
    )

    assert events == [
        f"download:{CHECKSUMS_URL}",
        f"download:{SIGNATURE_URL}",
        "verify-signature",
        f"download:{IMAGE_URL}",
    ]
    assert record.verified_source_digest == digest
    assert record.byte_size == len(image)
    assert record.cached_path == (paths.images / IMAGE_FILENAME).resolve()
    assert record.cached_path.read_bytes() == image
    assert record.to_json() == (
        '{"byte_size":3,"cached_path":"'
        f"{record.cached_path}"
        '\",\"checksums_url\":\"https://cloud-images.ubuntu.com/noble/current/SHA256SUMS\",'
        '\"filename\":\"noble-server-cloudimg-amd64.img\",'
        '\"image_url\":\"https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img\",'
        '\"retrieved_at\":\"2026-08-11T12:00:00+00:00\",'
        '\"schema_version\":1,\"signature_url\":\"https://cloud-images.ubuntu.com/noble/current/SHA256SUMS.gpg\",'
        f'\"verified_source_digest\":\"{digest}\"}}\n'
    )


def test_create_overlay_uses_verified_absolute_backing_image(tmp_path: Path) -> None:
    """Create one sparse run disk with the mandated backing-image command."""
    backing_image = (tmp_path / "base.qcow2").resolve()
    backing_image.write_bytes(b"verified-image")
    overlay = tmp_path / "run.qcow2"
    calls: list[list[str]] = []

    returned = create_overlay(
        backing_image,
        overlay,
        VmResources(disk_gib=40),
        run=lambda argv, **kwargs: calls.append(argv) or CompletedProcess(argv, 0),
    )

    assert returned == overlay.resolve()
    assert calls == [[
        "qemu-img", "create", "-f", "qcow2", "-F", "qcow2",
        "-b", str(backing_image), str(overlay), "40G",
    ]]


def test_create_overlay_refuses_reused_destination_or_relative_backing_image(
    tmp_path: Path,
) -> None:
    """Avoid destructive reuse and reject an unauditable relative backing path."""
    overlay = tmp_path / "run.qcow2"
    overlay.touch()

    with pytest.raises(FileExistsError):
        create_overlay(tmp_path / "base.qcow2", overlay, VmResources())
    with pytest.raises(ValueError, match="absolute"):
        create_overlay(Path("base.qcow2"), tmp_path / "new.qcow2", VmResources())


def test_create_overlay_removes_partial_destination_when_qemu_img_fails(tmp_path: Path) -> None:
    """Do not leave a failed qemu-img output reusable as a run disk."""
    backing_image = (tmp_path / "base.qcow2").resolve()
    backing_image.write_bytes(b"verified-image")
    overlay = tmp_path / "run.qcow2"

    def fail_after_creating_overlay(argv: list[str], **kwargs: object) -> CompletedProcess[object]:
        overlay.write_bytes(b"partial")
        raise CalledProcessError(1, argv)

    with pytest.raises(CalledProcessError):
        create_overlay(backing_image, overlay, VmResources(), run=fail_after_creating_overlay)

    assert not overlay.exists()


def test_create_overlay_refuses_a_dangling_symlink_destination(tmp_path: Path) -> None:
    """Treat a dangling symlink as an occupied overlay path, not an empty slot."""
    backing_image = (tmp_path / "base.qcow2").resolve()
    backing_image.write_bytes(b"verified-image")
    overlay = tmp_path / "run.qcow2"
    overlay.symlink_to(tmp_path / "missing.qcow2")

    with pytest.raises(FileExistsError):
        create_overlay(
            backing_image,
            overlay,
            VmResources(),
            run=lambda *args, **kwargs: pytest.fail("qemu-img must not run"),
        )
