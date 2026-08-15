from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import yaml

from docs_tooling.site import assemble_site


REPO_ROOT = Path(__file__).resolve().parents[1]


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_assemble_site_publishes_only_the_bounded_docs_surface(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    output = repo / "_build" / "docs-site" / "source"
    _write(repo / "README.md", "# Repository\n")
    _write(
        repo / "docs" / "README.md",
        "# Documentation\n\n"
        "[Architecture](architecture.md)\n"
        "[Domain guide](domains/personal-assistance.md)\n"
        "[Repository README](../README.md)\n",
    )
    _write(repo / "docs" / "architecture.md", "# Architecture\n")
    _write(
        repo / "docs" / "contributors" / "README.md",
        "# Contributors\n\n"
        "[Architecture](../architecture.md)\n"
        "[Skill source](../../skills/demo/SKILL.md)\n",
    )
    _write(repo / "docs" / "contributors" / "nested" / "guide.md", "# Nested\n")
    _write(
        repo / "docs" / "domains" / "personal-assistance.md",
        "# Personal Assistance\n",
    )
    _write(repo / "docs" / "plans" / "private.md", "# Plan\n")
    _write(repo / "skills" / "demo" / "SKILL.md", "# Demo\n")

    def write_graph(
        repo_root: str | Path,
        *,
        output_dir: str | Path,
        name: str | None,
        write_json: bool,
    ) -> list[Path]:
        assert Path(repo_root) == repo
        assert name == "repository"
        assert write_json is False
        html = Path(output_dir) / "repository.html"
        _write(html, "<!doctype html><title>Blueprint</title>\n")
        return [html]

    result = assemble_site(repo, output, graph_builder=write_graph)

    assert result == output
    assert (output / "index.md").is_file()
    assert (output / "architecture.md").is_file()
    assert (output / "contributors" / "README.md").is_file()
    assert (output / "contributors" / "nested" / "guide.md").is_file()
    assert (output / "domains" / "personal-assistance.md").is_file()
    assert (output / "graphs" / "index.md").is_file()
    assert (output / "graphs" / "blueprint" / "repository.html").is_file()

    assert not (output / "README.md").exists()
    assert not (output / "plans").exists()


def test_assemble_site_preserves_site_links_and_rewrites_unpublished_targets(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    output = repo / "_build" / "docs-site" / "source"
    _write(repo / "README.md", "# Repository\n")
    _write(
        repo / "docs" / "README.md",
        "# Documentation\n\n"
        "[Architecture](architecture.md#scope)\n"
        "[Domain guide](domains/personal-assistance.md)\n"
        "[Repository README](../README.md)\n"
        "[External](https://example.com/)\n",
    )
    _write(repo / "docs" / "architecture.md", "# Architecture\n")
    _write(
        repo / "docs" / "domains" / "personal-assistance.md",
        "# Personal Assistance\n",
    )

    def write_graph(
        repo_root: str | Path,
        *,
        output_dir: str | Path,
        name: str | None,
        write_json: bool,
    ) -> list[Path]:
        html = Path(output_dir) / "repository.html"
        _write(html, "<!doctype html>\n")
        return [html]

    assemble_site(repo, output, graph_builder=write_graph)

    homepage = (output / "index.md").read_text(encoding="utf-8")
    assert "[Architecture](architecture.md#scope)" in homepage
    assert "[Domain guide](domains/personal-assistance.md)" in homepage
    assert (
        "[Repository README](https://github.com/MoeenNehzati/famulus/blob/master/README.md)"
    ) in homepage
    assert "[External](https://example.com/)" in homepage


def test_docs_site_cli_exposes_local_serve_and_static_build_commands() -> None:
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "docs-site.py"), "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "{serve,build}" in result.stdout


def test_mkdocs_hook_loads_by_file_path_without_repository_on_sys_path(
    tmp_path: Path,
) -> None:
    hook = REPO_ROOT / "docs_tooling" / "mkdocs_hooks.py"
    loader = (
        "import importlib.util; "
        f"spec=importlib.util.spec_from_file_location('famulus_hook', {str(hook)!r}); "
        "module=importlib.util.module_from_spec(spec); "
        "spec.loader.exec_module(module)"
    )
    result = subprocess.run(
        [sys.executable, "-c", loader],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_pages_workflow_builds_and_deploys_independently_of_repository_tests() -> None:
    workflow_path = REPO_ROOT / ".github" / "workflows" / "pages.yml"
    workflow = yaml.load(workflow_path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)

    assert workflow["on"]["push"]["branches"] == ["master"]
    assert "workflow_dispatch" in workflow["on"]
    assert workflow["permissions"] == {
        "contents": "read",
        "pages": "write",
        "id-token": "write",
    }

    build = workflow["jobs"]["build"]
    assert "needs" not in build
    build_steps = build["steps"]
    assert any(step.get("run") == "./scripts/docs-site.py build" for step in build_steps)
    assert any(
        step.get("uses") == "actions/upload-pages-artifact@v4"
        and step.get("with", {}).get("path") == "_build/docs-site/site"
        for step in build_steps
    )

    deploy = workflow["jobs"]["deploy"]
    assert deploy["needs"] == "build"
    assert any(
        step.get("uses") == "actions/deploy-pages@v4" for step in deploy["steps"]
    )
