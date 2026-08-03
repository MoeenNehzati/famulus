from __future__ import annotations

import hashlib
import io
import os
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest

from officina.install.uv_bootstrap import UvBootstrapError, bootstrap_uv

PINNED_VERSION = "0.11.29"


def _fake_completed_process(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["uv", "--version"], returncode=returncode, stdout=stdout, stderr=stderr)


def _make_tar_gz(triple: str, uv_bytes: bytes) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        info = tarfile.TarInfo(name=f"uv-{triple}/uv")
        info.size = len(uv_bytes)
        archive.addfile(info, io.BytesIO(uv_bytes))
        uvx_bytes = b"fake-uvx"
        info2 = tarfile.TarInfo(name=f"uv-{triple}/uvx")
        info2.size = len(uvx_bytes)
        archive.addfile(info2, io.BytesIO(uvx_bytes))
    return buffer.getvalue()


def _make_zip(uv_bytes: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("uv.exe", uv_bytes)
        archive.writestr("uvx.exe", b"fake-uvx")
        archive.writestr("uvw.exe", b"fake-uvw")
    return buffer.getvalue()


class _FakeResponse:
    def __init__(self, data: bytes):
        self._data = data

    def read(self) -> bytes:
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fake_urlopen(url_to_bytes: dict[str, bytes]):
    def _urlopen(url, timeout=None):  # noqa: ARG001
        if url not in url_to_bytes:
            raise AssertionError(f"unexpected URL fetched: {url}")
        return _FakeResponse(url_to_bytes[url])

    return _urlopen


# ── No-op when already present and matching ──────────────────────────────────


def test_noop_when_uv_bin_exists_and_version_matches(monkeypatch, tmp_path):
    uv_bin = tmp_path / "tools" / "uv"
    uv_bin.parent.mkdir(parents=True)
    uv_bin.write_bytes(b"existing-uv")
    uv_bin.chmod(0o755)

    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: _fake_completed_process(0, stdout=f"uv {PINNED_VERSION} (abc123 2026-07-01)\n"),
    )

    def _fail_urlopen(*a, **k):
        raise AssertionError("must not fetch network when already bootstrapped")

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", _fail_urlopen)

    bootstrap_uv(
        uv_bin=uv_bin, version=PINNED_VERSION,
        triple="x86_64-unknown-linux-gnu", archive_extension=".tar.gz",
    )

    # Untouched: still the original bytes.
    assert uv_bin.read_bytes() == b"existing-uv"


def test_bootstraps_when_pinned_version_is_only_a_substring_of_reported_version(monkeypatch, tmp_path):
    """A pinned version that happens to be a prefix of the real installed
    version's string (e.g. pin "0.11.2" vs. real "0.11.29") must NOT be
    treated as a match -- the comparison must be against the exact parsed
    version token, not a substring check."""
    uv_bin = tmp_path / "tools" / "uv"
    uv_bin.parent.mkdir(parents=True)
    uv_bin.write_bytes(b"stale-uv")
    uv_bin.chmod(0o755)

    pinned_version = "0.11.2"
    real_installed_version = "0.11.29"

    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: _fake_completed_process(
            0, stdout=f"uv {real_installed_version} (abc123 2026-07-01)\n"
        ),
    )

    triple = "x86_64-unknown-linux-gnu"
    payload = b"correct-pinned-uv-binary-bytes"
    archive_bytes = _make_tar_gz(triple, payload)
    checksum_text = f"{hashlib.sha256(archive_bytes).hexdigest()}  uv-{triple}.tar.gz\n"
    archive_url = f"https://github.com/astral-sh/uv/releases/download/{pinned_version}/uv-{triple}.tar.gz"
    checksum_url = archive_url + ".sha256"

    import urllib.request
    monkeypatch.setattr(
        urllib.request, "urlopen",
        _fake_urlopen({archive_url: archive_bytes, checksum_url: checksum_text.encode("utf-8")}),
    )

    bootstrap_uv(uv_bin=uv_bin, version=pinned_version, triple=triple, archive_extension=".tar.gz")

    # Must have proceeded to re-bootstrap rather than wrongly no-op'ing on
    # the substring "match".
    assert uv_bin.read_bytes() == payload


def test_bootstraps_when_version_mismatches_even_if_binary_exists(monkeypatch, tmp_path):
    uv_bin = tmp_path / "tools" / "uv"
    uv_bin.parent.mkdir(parents=True)
    uv_bin.write_bytes(b"stale-uv")
    uv_bin.chmod(0o755)

    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: _fake_completed_process(0, stdout="uv 0.10.0 (old build)\n"),
    )

    triple = "x86_64-unknown-linux-gnu"
    payload = b"new-real-uv-binary-bytes"
    archive_bytes = _make_tar_gz(triple, payload)
    checksum_text = f"{hashlib.sha256(archive_bytes).hexdigest()}  uv-{triple}.tar.gz\n"
    archive_url = f"https://github.com/astral-sh/uv/releases/download/{PINNED_VERSION}/uv-{triple}.tar.gz"
    checksum_url = archive_url + ".sha256"

    import urllib.request
    monkeypatch.setattr(
        urllib.request, "urlopen",
        _fake_urlopen({archive_url: archive_bytes, checksum_url: checksum_text.encode("utf-8")}),
    )

    bootstrap_uv(uv_bin=uv_bin, version=PINNED_VERSION, triple=triple, archive_extension=".tar.gz")

    assert uv_bin.read_bytes() == payload


# ── Fresh install when missing ────────────────────────────────────────────────


def test_downloads_verifies_and_installs_when_missing_gnu_tarball(monkeypatch, tmp_path):
    uv_bin = tmp_path / "tools" / "uv"
    assert not uv_bin.exists()

    triple = "x86_64-unknown-linux-gnu"
    payload = b"totally-real-uv-binary"
    archive_bytes = _make_tar_gz(triple, payload)
    checksum_text = f"{hashlib.sha256(archive_bytes).hexdigest()}  uv-{triple}.tar.gz\n"
    archive_url = f"https://github.com/astral-sh/uv/releases/download/{PINNED_VERSION}/uv-{triple}.tar.gz"
    checksum_url = archive_url + ".sha256"

    import urllib.request
    monkeypatch.setattr(
        urllib.request, "urlopen",
        _fake_urlopen({archive_url: archive_bytes, checksum_url: checksum_text.encode("utf-8")}),
    )

    bootstrap_uv(uv_bin=uv_bin, version=PINNED_VERSION, triple=triple, archive_extension=".tar.gz")

    assert uv_bin.exists()
    assert uv_bin.read_bytes() == payload
    assert os.access(uv_bin, os.X_OK)


def test_downloads_verifies_and_installs_when_missing_darwin_tarball(monkeypatch, tmp_path):
    uv_bin = tmp_path / "tools" / "uv"

    triple = "aarch64-apple-darwin"
    payload = b"arm-uv-binary"
    archive_bytes = _make_tar_gz(triple, payload)
    checksum_text = f"{hashlib.sha256(archive_bytes).hexdigest()}  uv-{triple}.tar.gz\n"
    archive_url = f"https://github.com/astral-sh/uv/releases/download/{PINNED_VERSION}/uv-{triple}.tar.gz"
    checksum_url = archive_url + ".sha256"

    import urllib.request
    monkeypatch.setattr(
        urllib.request, "urlopen",
        _fake_urlopen({archive_url: archive_bytes, checksum_url: checksum_text.encode("utf-8")}),
    )

    bootstrap_uv(uv_bin=uv_bin, version=PINNED_VERSION, triple=triple, archive_extension=".tar.gz")

    assert uv_bin.read_bytes() == payload


def test_downloads_verifies_and_installs_when_missing_zip(monkeypatch, tmp_path):
    uv_bin = tmp_path / "tools" / "uv"

    triple = "x86_64-pc-windows-msvc"
    payload = b"host-uv-exe-bytes"
    archive_bytes = _make_zip(payload)
    checksum_text = f"{hashlib.sha256(archive_bytes).hexdigest()}  uv-{triple}.zip\n"
    archive_url = f"https://github.com/astral-sh/uv/releases/download/{PINNED_VERSION}/uv-{triple}.zip"
    checksum_url = archive_url + ".sha256"

    import urllib.request
    monkeypatch.setattr(
        urllib.request, "urlopen",
        _fake_urlopen({archive_url: archive_bytes, checksum_url: checksum_text.encode("utf-8")}),
    )

    # No uv on disk yet, so the version-check subprocess call would never
    # even fire; still stub it defensively.
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("unexpected")))

    bootstrap_uv(uv_bin=uv_bin, version=PINNED_VERSION, triple=triple, archive_extension=".zip")

    assert uv_bin.read_bytes() == payload


# ── Verification failure ──────────────────────────────────────────────────────


def test_checksum_mismatch_raises_and_installs_nothing(monkeypatch, tmp_path):
    uv_bin = tmp_path / "tools" / "uv"

    triple = "x86_64-unknown-linux-gnu"
    archive_bytes = _make_tar_gz(triple, b"corrupted-or-tampered-payload")
    wrong_checksum_text = f"{'0' * 64}  uv-{triple}.tar.gz\n"
    archive_url = f"https://github.com/astral-sh/uv/releases/download/{PINNED_VERSION}/uv-{triple}.tar.gz"
    checksum_url = archive_url + ".sha256"

    import urllib.request
    monkeypatch.setattr(
        urllib.request, "urlopen",
        _fake_urlopen({archive_url: archive_bytes, checksum_url: wrong_checksum_text.encode("utf-8")}),
    )

    with pytest.raises(UvBootstrapError, match="checksum"):
        bootstrap_uv(uv_bin=uv_bin, version=PINNED_VERSION, triple=triple, archive_extension=".tar.gz")

    assert not uv_bin.exists()


def test_non_utf8_checksum_sidecar_raises_typed_error_and_installs_nothing(monkeypatch, tmp_path):
    """A corrupted or tampered-with checksum sidecar that isn't valid UTF-8
    (e.g. network corruption or an unexpected proxy response) must surface
    as a typed UvBootstrapError, not an unhandled UnicodeDecodeError."""
    uv_bin = tmp_path / "tools" / "uv"

    triple = "x86_64-unknown-linux-gnu"
    archive_bytes = _make_tar_gz(triple, b"some-payload")
    non_utf8_checksum_bytes = b"\xff\xfe\x00invalid-utf8-bytes"
    archive_url = f"https://github.com/astral-sh/uv/releases/download/{PINNED_VERSION}/uv-{triple}.tar.gz"
    checksum_url = archive_url + ".sha256"

    import urllib.request
    monkeypatch.setattr(
        urllib.request, "urlopen",
        _fake_urlopen({archive_url: archive_bytes, checksum_url: non_utf8_checksum_bytes}),
    )

    with pytest.raises(UvBootstrapError, match="checksum"):
        bootstrap_uv(uv_bin=uv_bin, version=PINNED_VERSION, triple=triple, archive_extension=".tar.gz")

    assert not uv_bin.exists()


def test_extraction_failure_raises_and_installs_nothing(monkeypatch, tmp_path):
    """A valid, checksum-matching archive that simply doesn't contain the
    expected member (e.g. astral-sh renamed something upstream) must be a
    clean typed failure, not a crash or a bad install."""
    uv_bin = tmp_path / "tools" / "uv"

    triple = "x86_64-unknown-linux-gnu"
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        data = b"unexpected-layout"
        info = tarfile.TarInfo(name="unexpected/path")
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
    archive_bytes = buffer.getvalue()
    checksum_text = f"{hashlib.sha256(archive_bytes).hexdigest()}  uv-{triple}.tar.gz\n"
    archive_url = f"https://github.com/astral-sh/uv/releases/download/{PINNED_VERSION}/uv-{triple}.tar.gz"
    checksum_url = archive_url + ".sha256"

    import urllib.request
    monkeypatch.setattr(
        urllib.request, "urlopen",
        _fake_urlopen({archive_url: archive_bytes, checksum_url: checksum_text.encode("utf-8")}),
    )

    with pytest.raises(UvBootstrapError, match="extract"):
        bootstrap_uv(uv_bin=uv_bin, version=PINNED_VERSION, triple=triple, archive_extension=".tar.gz")

    assert not uv_bin.exists()


# ── Real-network, opt-in integration test ─────────────────────────────────────

# famulus-skip: category=live-smoke-opt-in; reason=downloads the real pinned uv release from GitHub; alternate=mocked tests above cover the download/verify/install logic without real network access
@pytest.mark.skipif(
    os.environ.get("FAMULUS_RUN_UV_BOOTSTRAP_NETWORK_TEST") != "1",
    reason="real-network uv bootstrap test is opt-in; set FAMULUS_RUN_UV_BOOTSTRAP_NETWORK_TEST=1",
)
def test_real_network_bootstrap_downloads_real_pinned_uv(tmp_path):
    """End-to-end against the real astral-sh/uv GitHub release: downloads the
    actual pinned version for this real test host, verifies its real
    published sha256 checksum, and confirms the installed binary reports the
    pinned version -- proving this module's understanding of uv's real asset
    naming and checksum format is correct, not just self-consistent with the
    mocks above.
    """
    import platform as platform_module
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "install-assistant-tools" / "_rtx"))
    import _install_scaffold as scaffold

    uv_bin = tmp_path / "tools" / "uv"
    platform_name = scaffold._platform_name()
    assert platform_name is not None, "unsupported test host platform"

    triple, archive_extension = scaffold.uv_release_target(
        platform_name=platform_name, machine=platform_module.machine()
    )
    bootstrap_uv(
        uv_bin=uv_bin, version=PINNED_VERSION,
        triple=triple, archive_extension=archive_extension,
    )

    assert uv_bin.exists()
    result = subprocess.run([str(uv_bin), "--version"], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert PINNED_VERSION in result.stdout
