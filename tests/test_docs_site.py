from __future__ import annotations

from pathlib import Path
import json
import subprocess
import sys

import yaml

from docs_tooling.site import PUBLISHED_GRAPHS, assemble_site, sync_published_docs


REPO_ROOT = Path(__file__).resolve().parents[1]


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_assemble_site_publishes_docs_tree_except_plans(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    output = repo / "_build" / "docs-site" / "source"
    _write(
        repo / "README.md",
        "# Repository\n\n"
        "[Architecture](docs/architecture.md)\n"
        "[Private plan](docs/plans/private.md)\n",
    )
    _write(
        repo / "docs" / "README.md",
        "# Documentation\n\n"
        "[Architecture](architecture.md)\n"
        "[Domain guide](domains/personal-assistance.md)\n"
        "[Officina](officina/architecture.md)\n"
        "[Dependency graph](demo/dependency-graph.html)\n"
        "[Private plan](plans/private.md)\n"
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
    _write(repo / "docs" / "officina" / "architecture.md", "# Officina\n")
    _write(
        repo / "docs" / "demo" / "dependency-graph.html",
        "<!doctype html><title>Dependency graph</title>\n",
    )
    _write(repo / "docs" / "plans" / "private.md", "# Plan\n")
    _write(repo / "docs" / "plans" / "assets" / "private.json", "{}\n")
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
    assert (output / "index.md").read_text(encoding="utf-8").startswith(
        "# Repository"
    )
    assert (output / "documentation" / "index.md").read_text(
        encoding="utf-8"
    ).startswith("# Documentation")
    assert (output / "architecture.md").is_file()
    assert (output / "contributors" / "README.md").is_file()
    assert (output / "contributors" / "nested" / "guide.md").is_file()
    assert (output / "domains" / "personal-assistance.md").is_file()
    assert (output / "officina" / "architecture.md").is_file()
    assert (output / "demo" / "dependency-graph.html").read_text(
        encoding="utf-8"
    ) == "<!doctype html><title>Dependency graph</title>\n"
    assert (output / "graphs" / "index.md").is_file()
    assert (output / "graphs" / "blueprint" / "repository.html").is_file()

    assert not (output / "README.md").exists()
    assert not (output / "plans").exists()

    homepage = (output / "index.md").read_text(encoding="utf-8")
    assert "[Architecture](architecture.md)" in homepage
    assert (
        "[Private plan](https://github.com/MoeenNehzati/famulus/blob/master/"
        "docs/plans/private.md)"
    ) in homepage

    documentation_index = (output / "documentation" / "index.md").read_text(
        encoding="utf-8"
    )
    assert "[Officina](../officina/architecture.md)" in documentation_index
    assert (
        "[Dependency graph](../demo/dependency-graph.html)" in documentation_index
    )


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

    documentation_index = (output / "documentation" / "index.md").read_text(
        encoding="utf-8"
    )
    assert "[Architecture](../architecture.md#scope)" in documentation_index
    assert "[Domain guide](../domains/personal-assistance.md)" in documentation_index
    assert "[Repository README](../index.md)" in documentation_index
    assert "[External](https://example.com/)" in documentation_index


def test_assemble_site_resolves_default_graph_builder_from_visualizer(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = tmp_path / "repo"
    output = repo / "_build" / "docs-site" / "source"
    _write(repo / "README.md", "# Repository\n")
    _write(repo / "docs" / "README.md", "# Documentation\n")

    from officina.visualization.from_blueprint import visualizer

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

    monkeypatch.setattr(visualizer, "build_blueprint_graph", write_graph)

    assemble_site(repo, output)

    assert (output / "graphs" / "blueprint" / "repository.html").is_file()


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


def test_published_graph_specifications_exist_and_are_renderable() -> None:
    """Every manifest entry names a real specification the renderer accepts."""

    assert PUBLISHED_GRAPHS, "the graph manifest should publish at least one graph"
    for stem, relative in PUBLISHED_GRAPHS.items():
        specification = REPO_ROOT / relative
        assert specification.is_file(), f"missing published graph for {stem}: {relative}"
        payload = json.loads(specification.read_text(encoding="utf-8"))
        assert payload.get("entities"), f"published graph {stem} declares no entities"


def test_assemble_site_renders_every_published_graph(tmp_path: Path) -> None:
    """A real-repository build emits one standalone page per manifest entry."""

    output = tmp_path / "source"
    assemble_site(REPO_ROOT, output, build_graph=False)
    index = (output / "graphs" / "index.md").read_text(encoding="utf-8")
    for stem in PUBLISHED_GRAPHS:
        page = output / "graphs" / f"{stem}.html"
        assert page.is_file(), f"published graph page was not rendered: {stem}"
        assert page.stat().st_size > 0
        assert f"({stem}.html)" in index, f"graph index does not link {stem}"


def test_sync_published_docs_retains_rendered_graph_pages(tmp_path: Path) -> None:
    """A live-reload synchronization keeps graphs it cannot itself rebuild."""

    output = tmp_path / "source"
    assemble_site(REPO_ROOT, output, build_graph=False)
    stem = next(iter(PUBLISHED_GRAPHS))
    page = output / "graphs" / f"{stem}.html"
    stamp = page.stat().st_mtime_ns

    sync_published_docs(REPO_ROOT, output)

    assert page.is_file(), "synchronization deleted a rendered graph page"
    assert page.stat().st_mtime_ns == stamp, "synchronization rewrote the page"
