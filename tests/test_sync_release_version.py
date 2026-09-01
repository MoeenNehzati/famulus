from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import ModuleType

import pytest

from test_support.git_repository import GitTestRepository, isolated_git_environment


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "sync-release-version.py"
MANIFESTS = (
    Path(".claude-plugin/plugin.json"),
    Path(".codex-plugin/plugin.json"),
    Path("plugin.json"),
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
        env=isolated_git_environment(),
        capture_output=True,
        text=True,
        check=False,
    )


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("sync_release_version", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_public_sync_preserves_views_is_idempotent_and_rejects_deletion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retain one real Git/process owner for the synchronizer boundary."""

    repository = _create_repository(tmp_path)
    (repository.root / "pyproject.toml").write_text(
        '[project]\nname = "famulus-officina"\nversion = "2.3.4"\n',
        encoding="utf-8",
    )
    repository.git("add", "pyproject.toml")
    for manifest in MANIFESTS:
        staged = {
            "name": "famulus",
            "version": "0.1.0",
            "description": f"staged {manifest.parent.name}",
        }
        _write_json(repository.root / manifest, staged)
        repository.git("add", manifest.as_posix())
        working = dict(staged)
        working["description"] = f"unstaged {manifest.parent.name}"
        _write_json(repository.root / manifest, working)
    (repository.root / "unrelated.txt").write_text(
        "staged unrelated\n", encoding="utf-8"
    )
    repository.git("add", "unrelated.txt")
    unrelated_index_before = repository.git("show", ":unrelated.txt").stdout
    ambient_index = tmp_path / "ambient.index"
    monkeypatch.setenv("GIT_INDEX_FILE", str(ambient_index))

    first = _run(repository)

    assert first.returncode == 0, first.stdout + first.stderr
    assert first.stdout == "Synchronized release version 2.3.4\n"
    expected_working: dict[Path, bytes] = {}
    expected_index: dict[Path, bytes] = {}
    for manifest in MANIFESTS:
        staged = json.loads(repository.git("show", f":{manifest.as_posix()}").stdout)
        working = json.loads((repository.root / manifest).read_bytes())
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
        expected_working[manifest] = (repository.root / manifest).read_bytes()
        expected_index[manifest] = repository.git(
            "show", f":{manifest.as_posix()}"
        ).stdout
    assert repository.git("show", ":unrelated.txt").stdout == unrelated_index_before
    index_after_first = repository.git("ls-files", "--stage", "-z").stdout

    second = _run(repository)

    assert second.returncode == 0, second.stdout + second.stderr
    assert second.stdout == "Synchronized release version 2.3.4\n"
    assert {
        path: (repository.root / path).read_bytes() for path in MANIFESTS
    } == expected_working
    assert {
        path: repository.git("show", f":{path.as_posix()}").stdout
        for path in MANIFESTS
    } == expected_index
    assert repository.git("ls-files", "--stage", "-z").stdout == index_after_first
    assert os.environ["GIT_INDEX_FILE"] == str(ambient_index)
    assert not ambient_index.exists()

    repository.git("reset", "--hard", "HEAD")
    repository.git("rm", MANIFESTS[2].as_posix())
    surviving = repository.root / MANIFESTS[0]
    surviving_before = surviving.read_bytes()
    index_before_deletion_check = repository.git("ls-files", "--stage", "-z").stdout
    unrelated_before_deletion_check = repository.git("show", ":unrelated.txt").stdout

    deleted = _run(repository)

    assert deleted.returncode == 1
    assert deleted.stderr.startswith("error: ")
    assert "path 'plugin.json' does not exist" in deleted.stderr
    assert surviving.read_bytes() == surviving_before
    assert (
        repository.git("ls-files", "--stage", "-z").stdout
        == index_before_deletion_check
    )
    assert (
        repository.git("show", ":unrelated.txt").stdout
        == unrelated_before_deletion_check
    )


def test_manifest_output_rewrites_only_top_level_version_and_rejects_bad_json() -> None:
    module = _load_module()
    path = MANIFESTS[0]
    unusual = (
        b'{ "name":"famulus", "version" : "0.1.0", '
        b'"nested":{"version":"9.9.9"} }\n\n'
    )
    expected = (
        b'{ "name":"famulus", "version" : "1.2.3", '
        b'"nested":{"version":"9.9.9"} }\n\n'
    )

    assert module._manifest_output(unusual, path, "1.2.3") == expected
    assert module._manifest_output(expected, path, "1.2.3") == expected

    with pytest.raises(module.SynchronizationError) as malformed:
        module._manifest_output(b'{"version": }\n', path, "1.2.3")
    assert str(malformed.value) == (
        ".claude-plugin/plugin.json: malformed UTF-8 JSON: "
        "Expecting value: line 1 column 13 (char 12)"
    )

    with pytest.raises(module.SynchronizationError) as duplicate:
        module._manifest_output(
            b'{"version":"0.1.0","version":"0.1.0"}\n', path, "1.2.3"
        )
    assert str(duplicate.value) == "duplicate JSON key: version"


def test_canonical_version_rejects_malformed_toml_and_noncanonical_values() -> None:
    module = _load_module()

    with pytest.raises(module.SynchronizationError) as malformed:
        module._canonical_version(b"[project\n")
    assert str(malformed.value) == (
        "pyproject.toml: malformed UTF-8 TOML: Expected ']' at the end of a "
        "table declaration (at line 1, column 9)"
    )

    for version in ("1.2", "01.2.3", "1.02.3", "1.2.03", "1.2.3-rc1", " 1.2.3"):
        staged = f'[project]\nversion = "{version}"\n'.encode()
        with pytest.raises(module.SynchronizationError) as noncanonical:
            module._canonical_version(staged)
        assert str(noncanonical.value) == (
            "pyproject.toml: [project].version must be canonical MAJOR.MINOR.PATCH"
        )


def test_synchronize_prepares_every_input_before_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_module()
    root = tmp_path / "preparation"
    root.mkdir()
    for manifest in MANIFESTS:
        path = root / manifest
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'{"version":"0.1.0"}\n')
    (root / MANIFESTS[2]).write_bytes(b'{"version": }\n')
    before = {manifest: (root / manifest).read_bytes() for manifest in MANIFESTS}
    index_inputs = {
        Path("pyproject.toml"): b'[project]\nversion = "1.2.3"\n',
        MANIFESTS[0]: b'{"version":"0.1.0"}\n',
        MANIFESTS[1]: b'{"version":"0.1.0"}\n',
        MANIFESTS[2]: b'{"version":"0.1.0"}\n',
    }
    reads: list[Path] = []
    modes: list[Path] = []

    def index_bytes(_root: Path, path: Path) -> bytes:
        reads.append(path)
        return index_inputs[path]

    def index_mode(_root: Path, path: Path) -> str:
        modes.append(path)
        return "100644"

    monkeypatch.setattr(module, "_repository_root", lambda: root)
    monkeypatch.setattr(module, "_index_bytes", index_bytes)
    monkeypatch.setattr(module, "_index_mode", index_mode)
    monkeypatch.setattr(
        module,
        "_atomic_replace",
        lambda *_args: pytest.fail("replacement started before preparation completed"),
    )
    monkeypatch.setattr(
        module,
        "_git",
        lambda *_args, **_kwargs: pytest.fail("index update started during preparation"),
    )

    with pytest.raises(module.SynchronizationError) as malformed:
        module.synchronize()

    assert str(malformed.value) == (
        "plugin.json: malformed UTF-8 JSON: "
        "Expecting value: line 1 column 13 (char 12)"
    )
    assert reads == [Path("pyproject.toml"), *MANIFESTS]
    assert modes == list(MANIFESTS)
    assert {manifest: (root / manifest).read_bytes() for manifest in MANIFESTS} == before


def test_synchronize_rolls_back_real_atomic_replacements_without_git(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_module()
    rollback_root = tmp_path / "rollback"
    rollback_root.mkdir()
    rollback_before: dict[Path, bytes] = {}
    for manifest in MANIFESTS:
        path = rollback_root / manifest
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'{"version":"0.1.0"}\n')
        rollback_before[manifest] = path.read_bytes()
    real_replace = _load_module()._atomic_replace
    replacements: list[Path] = []
    failed = False

    def fail_third_once(path: Path, data: bytes) -> None:
        nonlocal failed
        replacements.append(path)
        if path == rollback_root / MANIFESTS[2] and not failed:
            failed = True
            raise OSError("injected third replacement failure")
        real_replace(path, data)

    rollback_index = {
        Path("pyproject.toml"): b'[project]\nversion = "1.2.3"\n',
        MANIFESTS[0]: rollback_before[MANIFESTS[0]],
        MANIFESTS[1]: rollback_before[MANIFESTS[1]],
        MANIFESTS[2]: rollback_before[MANIFESTS[2]],
    }
    monkeypatch.setattr(module, "_repository_root", lambda: rollback_root)
    monkeypatch.setattr(
        module, "_index_bytes", lambda _root, path: rollback_index[path]
    )
    monkeypatch.setattr(module, "_index_mode", lambda _root, _path: "100644")
    monkeypatch.setattr(module, "_temporary_index_active", lambda _root: False)
    monkeypatch.setattr(module, "_atomic_replace", fail_third_once)
    monkeypatch.setattr(
        module,
        "_git",
        lambda *_args, **_kwargs: pytest.fail("rollback used a Git child"),
    )

    with pytest.raises(OSError, match="^injected third replacement failure$"):
        module.synchronize()

    assert replacements == [
        rollback_root / MANIFESTS[0],
        rollback_root / MANIFESTS[1],
        rollback_root / MANIFESTS[2],
        rollback_root / MANIFESTS[1],
        rollback_root / MANIFESTS[0],
    ]
    assert {
        manifest: (rollback_root / manifest).read_bytes() for manifest in MANIFESTS
    } == rollback_before
