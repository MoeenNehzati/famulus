from __future__ import annotations

import json
import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest

from test_support.git_repository import GitTestRepository


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "sync-release-version.py"
MANIFESTS = (
    Path(".claude-plugin/plugin.json"),
    Path(".codex-plugin/plugin.json"),
)


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )


def _create_repository(tmp_path: Path) -> GitTestRepository:
    repository = GitTestRepository.create(tmp_path / "repository")
    (repository.root / "pyproject.toml").write_text(
        '[project]\nname = "famulus-officina"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    for manifest in MANIFESTS:
        _write_json(
            repository.root / manifest,
            {
                "name": "famulus",
                "version": "0.1.0",
                "description": f"original {manifest.parent.name}",
            },
        )
    (repository.root / "unrelated.txt").write_text("original\n", encoding="utf-8")
    repository.git("add", ".")
    repository.git("commit", "--quiet", "-m", "baseline")
    return repository


def _run(repository: GitTestRepository) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=repository.root,
        capture_output=True,
        text=True,
        check=False,
    )


def _index_json(repository: GitTestRepository, path: Path) -> dict[str, object]:
    return json.loads(repository.git("show", f":{path.as_posix()}").stdout)


def test_sync_uses_staged_version_and_preserves_staged_and_unstaged_views(
    tmp_path: Path,
) -> None:
    """Catch whole-file staging that absorbs unrelated working-tree edits."""

    repository = _create_repository(tmp_path)
    (repository.root / "pyproject.toml").write_text(
        '[project]\nname = "famulus-officina"\nversion = "2.3.4"\n',
        encoding="utf-8",
    )
    repository.git("add", "pyproject.toml")

    for manifest in MANIFESTS:
        staged = json.loads((repository.root / manifest).read_text(encoding="utf-8"))
        staged["description"] = f"staged {manifest.parent.name}"
        _write_json(repository.root / manifest, staged)
        repository.git("add", manifest.as_posix())
        working = dict(staged)
        working["description"] = f"unstaged {manifest.parent.name}"
        _write_json(repository.root / manifest, working)

    (repository.root / "unrelated.txt").write_text("staged unrelated\n", encoding="utf-8")
    repository.git("add", "unrelated.txt")
    unrelated_index_before = repository.git("show", ":unrelated.txt").stdout

    completed = _run(repository)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout == "Synchronized release version 2.3.4\n"
    for manifest in MANIFESTS:
        staged = _index_json(repository, manifest)
        working = json.loads((repository.root / manifest).read_text(encoding="utf-8"))
        assert staged == {
            "name": "famulus",
            "version": "2.3.4",
            "description": f"staged {manifest.parent.name}",
        }
        assert working == {
            "name": "famulus",
            "version": "2.3.4",
            "description": f"unstaged {manifest.parent.name}",
        }
    assert repository.git("show", ":unrelated.txt").stdout == unrelated_index_before


def test_sync_is_idempotent(tmp_path: Path) -> None:
    """Catch rewrites or index churn when every version is already synchronized."""

    repository = _create_repository(tmp_path)

    first = _run(repository)
    files_after_first = {
        path: (repository.root / path).read_bytes() for path in MANIFESTS
    }
    index_after_first = repository.git("ls-files", "--stage", "-z").stdout
    second = _run(repository)

    assert first.returncode == 0, first.stdout + first.stderr
    assert second.returncode == 0, second.stdout + second.stderr
    assert {
        path: (repository.root / path).read_bytes() for path in MANIFESTS
    } == files_after_first
    assert repository.git("ls-files", "--stage", "-z").stdout == index_after_first


def test_sync_changes_only_top_level_version_bytes(tmp_path: Path) -> None:
    """Catch JSON reserialization that changes unrelated manifest bytes."""

    repository = _create_repository(tmp_path)
    (repository.root / "pyproject.toml").write_text(
        '[project]\nname = "famulus-officina"\nversion = "1.2.3"\n',
        encoding="utf-8",
    )
    repository.git("add", "pyproject.toml")
    unusual = b'{ "name":"famulus", "version" : "0.1.0", "nested":{"version":"9.9.9"} }\n\n'
    expected = b'{ "name":"famulus", "version" : "1.2.3", "nested":{"version":"9.9.9"} }\n\n'
    for manifest in MANIFESTS:
        (repository.root / manifest).write_bytes(unusual)
        repository.git("add", manifest.as_posix())

    completed = _run(repository)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    for manifest in MANIFESTS:
        assert (repository.root / manifest).read_bytes() == expected
        assert repository.git("show", f":{manifest.as_posix()}").stdout == expected


def test_sync_rejects_malformed_input_before_changing_files_or_index(
    tmp_path: Path,
) -> None:
    """Catch validation that mutates the first manifest before reading the second."""

    repository = _create_repository(tmp_path)
    (repository.root / "pyproject.toml").write_text(
        '[project]\nname = "famulus-officina"\nversion = "1.2.3"\n',
        encoding="utf-8",
    )
    repository.git("add", "pyproject.toml")
    malformed = repository.root / MANIFESTS[1]
    malformed.write_text('{"version": "0.1.0", "version": }\n', encoding="utf-8")
    repository.git("add", MANIFESTS[1].as_posix())
    files_before = {path: (repository.root / path).read_bytes() for path in MANIFESTS}
    index_before = repository.git("ls-files", "--stage", "-z").stdout

    completed = _run(repository)

    assert completed.returncode == 1
    assert "malformed UTF-8 JSON" in completed.stderr
    assert {path: (repository.root / path).read_bytes() for path in MANIFESTS} == files_before
    assert repository.git("ls-files", "--stage", "-z").stdout == index_before


def test_sync_rejects_duplicate_manifest_keys(tmp_path: Path) -> None:
    repository = _create_repository(tmp_path)
    duplicate = repository.root / MANIFESTS[0]
    duplicate.write_text(
        '{"version":"0.1.0","version":"0.1.0"}\n',
        encoding="utf-8",
    )
    repository.git("add", MANIFESTS[0].as_posix())

    completed = _run(repository)

    assert completed.returncode == 1
    assert "duplicate JSON key: version" in completed.stderr


def test_sync_rejects_deleted_staged_manifest_without_mutation(tmp_path: Path) -> None:
    repository = _create_repository(tmp_path)
    surviving = repository.root / MANIFESTS[1]
    surviving_before = surviving.read_bytes()
    repository.git("rm", MANIFESTS[0].as_posix())

    completed = _run(repository)

    assert completed.returncode == 1
    assert surviving.read_bytes() == surviving_before


def test_sync_rejects_malformed_canonical_toml(tmp_path: Path) -> None:
    repository = _create_repository(tmp_path)
    (repository.root / "pyproject.toml").write_text("[project\n", encoding="utf-8")
    repository.git("add", "pyproject.toml")

    completed = _run(repository)

    assert completed.returncode == 1
    assert "malformed UTF-8 TOML" in completed.stderr


@pytest.mark.parametrize(
    "version",
    ("1.2", "01.2.3", "1.02.3", "1.2.03", "1.2.3-rc1", " 1.2.3"),
)
def test_sync_rejects_noncanonical_versions(tmp_path: Path, version: str) -> None:
    """Catch acceptance of version spellings outside the release contract."""

    repository = _create_repository(tmp_path)
    (repository.root / "pyproject.toml").write_text(
        f'[project]\nname = "famulus-officina"\nversion = "{version}"\n',
        encoding="utf-8",
    )
    repository.git("add", "pyproject.toml")

    completed = _run(repository)

    assert completed.returncode == 1
    assert "canonical MAJOR.MINOR.PATCH" in completed.stderr


def test_sync_rolls_back_working_files_when_second_replace_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catch a late filesystem failure that leaves only one manifest updated."""

    repository = _create_repository(tmp_path)
    (repository.root / "pyproject.toml").write_text(
        '[project]\nname = "famulus-officina"\nversion = "1.2.3"\n',
        encoding="utf-8",
    )
    repository.git("add", "pyproject.toml")
    files_before = {path: (repository.root / path).read_bytes() for path in MANIFESTS}
    index_before = repository.git("ls-files", "--stage", "-z").stdout
    spec = importlib.util.spec_from_file_location("sync_release_version", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_repository_root", lambda: repository.root)
    real_replace = module._atomic_replace
    failed = False

    def fail_second_once(path: Path, data: bytes) -> None:
        nonlocal failed
        if path == repository.root / MANIFESTS[1] and not failed:
            failed = True
            raise OSError("injected second replacement failure")
        real_replace(path, data)

    monkeypatch.setattr(module, "_atomic_replace", fail_second_once)

    with pytest.raises(OSError, match="injected second replacement failure"):
        module.synchronize()

    assert {path: (repository.root / path).read_bytes() for path in MANIFESTS} == files_before
    assert repository.git("ls-files", "--stage", "-z").stdout == index_before
