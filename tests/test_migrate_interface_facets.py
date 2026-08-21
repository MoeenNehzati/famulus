from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "migrate_interface_facets.py"


def _run(repo_root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--repo-root", str(repo_root), *arguments],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )


def test_migration_adds_only_missing_interface_facets_and_is_idempotent(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "demo"
    repo_root.mkdir()
    (repo_root / "README.md").write_text("Demo.\n", encoding="utf-8")
    (repo_root / "worker.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo_root / "shared.py").write_text("VALUE = 2\n", encoding="utf-8")
    (repo_root / "blueprint.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 6,
                "node_type": "module",
                "id": "demo",
                "version": 1,
                "gateway": {"path": "README.md", "language": "Markdown"},
                "content": [r"README\.md", r"worker\.py", r"shared\.py"],
                "authority": {"owns_filesystem": []},
                "sources": {
                    "demo.source.worker": {
                        "blueprint": {
                            "base": "module-root",
                            "path": "blueprints/worker.yaml",
                        }
                    }
                },
                "children": {},
                "namespace_exports": {},
                "exports": {},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    blueprint_root = repo_root / "blueprints"
    blueprint_root.mkdir()
    path = blueprint_root / "worker.yaml"
    source_text = yaml.safe_dump(
            {
                "schema_version": 6,
                "node_type": "behavioral_source",
                "id": "demo.source.worker",
                "version": 1,
                "source_module": "demo",
                "gateway": {"path": "worker.py", "language": "Python"},
                "content": ["worker\\.py", "shared\\.py"],
                "dependencies": [],
                "uses_interfaces": [
                    {
                        "interface": "provider.source.api.interface.run",
                        "version": 1,
                    }
                ],
                "interfaces": {
                    "demo.source.worker.interface.run": {
                        "version": 1,
                        "contract": {"consumes": [], "produces": []},
                    },
                    "demo.source.worker.interface.inspect": {
                        "version": 1,
                        "content": ["worker\\.py"],
                        "contract": {"consumes": [], "produces": []},
                    },
                    "demo.source.worker.interface.authored": {
                        "version": 1,
                        "content": ["shared\\.py"],
                        "uses_interfaces": [],
                        "contract": {"consumes": [], "produces": []},
                    },
                },
            },
            sort_keys=False,
        )
    source_text = source_text.replace(
        "id: demo.source.worker\n",
        "# preserve this authored comment\nid: demo.source.worker\n",
    ).replace(
        "source_module: demo\n",
        "source_module: demo  # preserve inline comment\n",
    )
    path.write_text(source_text, encoding="utf-8")

    check = _run(repo_root)
    assert check.returncode == 1
    assert "blueprints/worker.yaml" in check.stdout

    write = _run(repo_root, "--write")
    assert write.returncode == 0, write.stderr
    migrated_text = path.read_text(encoding="utf-8")
    assert "# preserve this authored comment" in migrated_text
    assert "source_module: demo  # preserve inline comment" in migrated_text
    declaration = yaml.safe_load(path.read_text(encoding="utf-8"))
    interfaces = declaration["interfaces"]
    assert interfaces["demo.source.worker.interface.run"]["content"] == [
        "worker\\.py",
        "shared\\.py",
    ]
    assert interfaces["demo.source.worker.interface.run"]["uses_interfaces"] == [
        {"interface": "provider.source.api.interface.run", "version": 1}
    ]
    assert interfaces["demo.source.worker.interface.inspect"]["content"] == [
        "worker\\.py"
    ]
    assert interfaces["demo.source.worker.interface.inspect"]["uses_interfaces"] == [
        {"interface": "provider.source.api.interface.run", "version": 1}
    ]
    assert interfaces["demo.source.worker.interface.authored"]["content"] == [
        "shared\\.py"
    ]
    assert interfaces["demo.source.worker.interface.authored"]["uses_interfaces"] == []

    first_bytes = path.read_bytes()
    second_write = _run(repo_root, "--write")
    assert second_write.returncode == 0, second_write.stderr
    assert path.read_bytes() == first_bytes
    assert _run(repo_root).returncode == 0


def test_migration_fails_closed_on_inventory_errors(tmp_path: Path) -> None:
    (tmp_path / "blueprint.yaml").write_text(
        "schema_version: 6\nnode_type: module\nid: demo\n",
        encoding="utf-8",
    )
    blueprint_root = tmp_path / "blueprints"
    blueprint_root.mkdir()
    (blueprint_root / "broken.yaml").write_text(
        "schema_version: [\n",
        encoding="utf-8",
    )

    result = _run(tmp_path)

    assert result.returncode == 2
    assert "blueprints/broken.yaml" in result.stderr
