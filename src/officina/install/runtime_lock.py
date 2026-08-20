"""Generate and validate the hash-checked core runtime dependency lock."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess


_SUPPORTED_MANIFEST_VERSIONS = frozenset({1, 2})
_SUPPORTED_PLATFORMS = ("linux", "macos", "windows")
_PLATFORM_MARKERS = {
    "linux": "sys_platform == 'linux'",
    "macos": "sys_platform == 'darwin'",
    "windows": "sys_platform == 'win32'",
}
# Installer-owned packages required before the first-party wheel can be built.
# Skill-owned dependencies remain authoritative in the generated blueprint
# manifest; these two requirements are the managed-runtime bootstrap policy.
BOOTSTRAP_REQUIREMENTS = ("PyYAML==6.0.2", "setuptools==80.9.0")

_HEADER_PATTERN = re.compile(r"^# ([a-z][a-z0-9-]*): (.+)$")
_EXACT_REQUIREMENT_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.-]*(?:\[[A-Za-z0-9_,.-]+\])?==[^\s;\\*]+(?:\s*;.*)?$"
)
_SHA256_PATTERN = re.compile(r"--hash=sha256:[0-9a-fA-F]{64}(?:\s|$)")


class RuntimeLockError(Exception):
    """Raised when generated requirements or their compiled lock are invalid."""


@dataclass(frozen=True)
class RuntimeLockMetadata:
    """Verified provenance carried by a generated runtime lock."""

    input_sha256: str
    uv_version: str
    python_version: str
    lock_sha256: str


def _package_spec(name: str, version: object) -> str:
    if not isinstance(version, str) or not version or version == "any":
        return name
    if version.startswith(("<", ">", "=", "!", "~")):
        return f"{name}{version}"
    return f"{name}=={version}"


def _platform_marker(platforms: object) -> str:
    enabled = tuple(
        platform
        for platform in _SUPPORTED_PLATFORMS
        if isinstance(platforms, dict) and platforms.get(platform) is True
    )
    if not enabled:
        raise RuntimeLockError("python-package dependency has no supported platform")
    if enabled == _SUPPORTED_PLATFORMS:
        return ""
    return " or ".join(_PLATFORM_MARKERS[platform] for platform in enabled)


def selected_runtime_module_ids(
    manifest_path: Path, *, optional_module_ids: tuple[str, ...] = ()
) -> tuple[str, ...]:
    """Return core modules plus the explicitly selected optional modules.

    The generated manifest is the selection authority.  Version-one manifests
    predate installation tiers and are therefore treated as all-core for the
    compatibility readers that still accept them.
    """
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeLockError(f"could not read runtime dependency manifest: {exc}") from exc
    if payload.get("version") not in _SUPPORTED_MANIFEST_VERSIONS:
        raise RuntimeLockError(
            f"unsupported runtime dependency manifest version: {payload.get('version')!r}"
        )
    skills = payload.get("skills")
    if not isinstance(skills, dict):
        raise RuntimeLockError("runtime dependency manifest 'skills' must be an object")

    selected: set[str] = set()
    for module_id, module in skills.items():
        if not isinstance(module_id, str) or not isinstance(module, dict):
            raise RuntimeLockError("runtime dependency manifest has an invalid module record")
        if payload["version"] == 1 or module.get("installation_tier", "core") == "core":
            selected.add(module_id)
    for module_id in optional_module_ids:
        module = skills.get(module_id)
        if not isinstance(module, dict) or module.get("installation_tier") != "optional":
            raise RuntimeLockError(f"unknown optional module: {module_id}")
        selected.add(module_id)
    return tuple(sorted(selected))


def render_runtime_requirements(
    manifest_path: Path, *, selected_module_ids: tuple[str, ...] = ()
) -> str:
    """Pool every distinct core blueprint requirement into canonical input.

    Distinct constraints for the same normalized name are intentionally kept:
    uv resolves their intersection and fails if it is impossible. This avoids
    declaration-order semantics such as the installer's former first-wins
    behavior.
    """

    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeLockError(f"could not read runtime dependency manifest: {exc}") from exc
    if payload.get("version") not in _SUPPORTED_MANIFEST_VERSIONS:
        raise RuntimeLockError(
            f"unsupported runtime dependency manifest version: {payload.get('version')!r}"
        )
    skills = payload.get("skills")
    if not isinstance(skills, dict):
        raise RuntimeLockError("runtime dependency manifest 'skills' must be an object")

    selected = set(selected_runtime_module_ids(
        manifest_path, optional_module_ids=selected_module_ids
    ))
    requirements = set(BOOTSTRAP_REQUIREMENTS)
    for module_id, skill in skills.items():
        if module_id not in selected:
            continue
        interfaces = skill.get("interfaces", {}) if isinstance(skill, dict) else {}
        if not isinstance(interfaces, dict):
            continue
        for interface in interfaces.values():
            dependencies = interface.get("dependencies", []) if isinstance(interface, dict) else []
            if not isinstance(dependencies, list):
                continue
            for dependency in dependencies:
                if not isinstance(dependency, dict) or dependency.get("kind") != "python-package":
                    continue
                name = dependency.get("name")
                if not isinstance(name, str) or not name.strip():
                    raise RuntimeLockError("python-package dependency has no valid name")
                requirement = _package_spec(name, dependency.get("version"))
                marker = _platform_marker(dependency.get("platforms"))
                requirements.add(f"{requirement} ; {marker}" if marker else requirement)

    body = "".join(f"{requirement}\n" for requirement in sorted(requirements, key=str.casefold))
    return (
        "# Generated from references/blueprint/runtime_dependencies.json.\n"
        "# Do not edit by hand.\n"
        "\n"
        f"{body}"
    )


def _logical_lock_records(lock_text: str) -> tuple[str, ...]:
    records: list[str] = []
    current: list[str] = []
    for raw_line in lock_text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        current.append(stripped.removesuffix("\\").strip())
        if stripped.endswith("\\"):
            continue
        records.append(" ".join(current))
        current = []
    if current:
        raise RuntimeLockError("runtime lock ends with an incomplete continuation record")
    if not records:
        raise RuntimeLockError("runtime lock contains no requirements")
    return tuple(records)


def _lock_headers(lock_text: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    for line in lock_text.splitlines():
        match = _HEADER_PATTERN.match(line)
        if match:
            headers[match.group(1)] = match.group(2)
    return headers


def _lock_body(lock_text: str) -> str:
    """Return the generated requirement body after the metadata separator."""
    separator = "\n#\n"
    separator_index = lock_text.find(separator)
    if separator_index < 0:
        raise RuntimeLockError("runtime lock has no metadata separator")
    return lock_text[separator_index + len(separator):]


def validate_runtime_lock(
    *,
    manifest_path: Path,
    input_path: Path,
    lock_path: Path,
    expected_uv_version: str,
    expected_python_version: str,
    selected_module_ids: tuple[str, ...] = (),
) -> RuntimeLockMetadata:
    """Validate generated-input drift, lock provenance, exact pins and hashes."""

    canonical_input = render_runtime_requirements(
        manifest_path, selected_module_ids=selected_module_ids
    )
    try:
        actual_input = input_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeLockError(f"could not read generated runtime requirements input: {exc}") from exc
    if actual_input != canonical_input:
        raise RuntimeLockError("generated runtime requirements input is stale")

    input_sha256 = hashlib.sha256(canonical_input.encode("utf-8")).hexdigest()
    try:
        lock_bytes = lock_path.read_bytes()
        lock_text = lock_bytes.decode("utf-8", errors="strict").replace("\r\n", "\n").replace("\r", "\n")
    except (OSError, UnicodeDecodeError) as exc:
        raise RuntimeLockError(f"could not read runtime lock: {exc}") from exc
    headers = _lock_headers(lock_text)
    lock_body = _lock_body(lock_text)
    lock_content_sha256 = hashlib.sha256(lock_body.encode("utf-8")).hexdigest()
    if headers.get("lock-content-sha256") != lock_content_sha256:
        raise RuntimeLockError(
            "runtime lock content digest mismatch: "
            f"expected {lock_content_sha256!r}, got {headers.get('lock-content-sha256')!r}"
        )
    expected_headers = {
        "famulus-runtime-lock-schema": "1",
        "input-sha256": input_sha256,
        "uv-version": expected_uv_version,
        "python-version": expected_python_version,
    }
    for name, expected in expected_headers.items():
        if headers.get(name) != expected:
            raise RuntimeLockError(
                f"runtime lock {name} mismatch: expected {expected!r}, got {headers.get(name)!r}"
            )

    for record in _logical_lock_records(lock_body):
        requirement = record.split(" --hash=", 1)[0].strip()
        if not _EXACT_REQUIREMENT_PATTERN.match(requirement):
            raise RuntimeLockError(f"runtime lock requirement is not pinned exactly: {requirement}")
        if not _SHA256_PATTERN.search(record + " "):
            raise RuntimeLockError(f"runtime lock requirement has no SHA-256 hash: {requirement}")

    return RuntimeLockMetadata(
        input_sha256=input_sha256,
        uv_version=expected_uv_version,
        python_version=expected_python_version,
        lock_sha256=hashlib.sha256(lock_bytes).hexdigest(),
    )


def _run_uv(argv: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=600,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeLockError(f"could not run uv for runtime lock generation: {exc}") from exc


def generate_runtime_lock(
    *,
    manifest_path: Path,
    input_path: Path,
    lock_path: Path,
    uv_bin: Path,
    expected_uv_version: str,
    python_version: str,
    selected_module_ids: tuple[str, ...] = (),
) -> RuntimeLockMetadata:
    """Render canonical input and compile it with the exact pinned uv."""

    version_result = _run_uv([str(uv_bin), "--version"])
    version_parts = version_result.stdout.split() if version_result.returncode == 0 else []
    actual_uv_version = version_parts[1] if len(version_parts) >= 2 else None
    if actual_uv_version != expected_uv_version:
        raise RuntimeLockError(
            f"expected uv {expected_uv_version}, got {actual_uv_version or 'an unusable uv binary'}"
        )

    canonical_input = render_runtime_requirements(
        manifest_path, selected_module_ids=selected_module_ids
    )
    input_path.parent.mkdir(parents=True, exist_ok=True)
    input_tmp = Path(str(input_path) + ".tmp")
    lock_tmp = Path(str(lock_path) + ".tmp")
    try:
        input_tmp.write_text(canonical_input, encoding="utf-8")
        lock_tmp.unlink(missing_ok=True)
        compile_result = _run_uv(
            [
                str(uv_bin),
                "pip",
                "compile",
                "--universal",
                "--python-version",
                python_version,
                "--generate-hashes",
                "--no-header",
                "--output-file",
                str(lock_tmp),
                str(input_tmp),
            ]
        )
        if compile_result.returncode != 0:
            raise RuntimeLockError(
                f"uv runtime lock compilation failed (exit {compile_result.returncode}): "
                f"{compile_result.stderr.strip()}"
            )
        compiled = lock_tmp.read_text(encoding="utf-8").replace(
            f"-r {input_tmp}", f"-r {input_path}"
        )
        try:
            relative_input_tmp = input_tmp.resolve().relative_to(Path.cwd().resolve())
            relative_input = input_path.resolve().relative_to(Path.cwd().resolve())
        except ValueError:
            pass
        else:
            compiled = compiled.replace(
                f"-r {relative_input_tmp}", f"-r {relative_input}"
            )
        input_sha256 = hashlib.sha256(canonical_input.encode("utf-8")).hexdigest()
        lock_content_sha256 = hashlib.sha256(compiled.encode("utf-8")).hexdigest()
        header = (
            "# famulus-runtime-lock-schema: 1\n"
            f"# input-sha256: {input_sha256}\n"
            f"# uv-version: {expected_uv_version}\n"
            f"# python-version: {python_version}\n"
            f"# lock-content-sha256: {lock_content_sha256}\n"
            "#\n"
        )
        lock_tmp.write_text(header + compiled, encoding="utf-8")
        validate_runtime_lock(
            manifest_path=manifest_path,
            input_path=input_tmp,
            lock_path=lock_tmp,
            expected_uv_version=expected_uv_version,
            expected_python_version=python_version,
            selected_module_ids=selected_module_ids,
        )
        os.replace(input_tmp, input_path)
        os.replace(lock_tmp, lock_path)
    except OSError as exc:
        raise RuntimeLockError(f"could not write generated runtime lock: {exc}") from exc
    finally:
        input_tmp.unlink(missing_ok=True)
        lock_tmp.unlink(missing_ok=True)

    return validate_runtime_lock(
        manifest_path=manifest_path,
        input_path=input_path,
        lock_path=lock_path,
        expected_uv_version=expected_uv_version,
        expected_python_version=python_version,
        selected_module_ids=selected_module_ids,
    )


__all__ = [
    "BOOTSTRAP_REQUIREMENTS",
    "RuntimeLockError",
    "RuntimeLockMetadata",
    "generate_runtime_lock",
    "render_runtime_requirements",
    "selected_runtime_module_ids",
    "validate_runtime_lock",
]
