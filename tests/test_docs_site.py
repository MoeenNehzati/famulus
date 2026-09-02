from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from jsonschema import Draft7Validator
import yaml

from docs_tooling import site
from docs_tooling.site import PUBLISHED_GRAPHS, assemble_site
from officina.visualization.artifacts import GraphArtifactWriter


REPO_ROOT = Path(__file__).resolve().parents[1]
MATH_DEPENDENCY_GRAPH = REPO_ROOT / PUBLISHED_GRAPHS["math-dependency"][0]
MATH_DEPENDENCY_SCHEMA = (
    REPO_ROOT / "src/officina/visualization/graph_specification.schema.json"
)
MATH_DEPENDENCY_SEMANTIC_SHA256 = (
    "b734dabce7977c4160466bd5aff4638fa18f3c01c5806711e88b683bfc62032b"
)


def _task5_math_dependency_graph() -> Path:
    override = os.environ.get("FAMULUS_MATH_DEPENDENCY_GRAPH")
    return Path(override) if override else MATH_DEPENDENCY_GRAPH


def _without_task5_macro_changes(payload: dict) -> dict:
    normalized = copy.deepcopy(payload)
    normalized.get("metadata", {}).pop("macro_gap", None)
    for dependency in normalized.get("renderer_dependencies", []):
        if dependency.get("id") == "mathjax":
            dependency.get("configuration", {}).pop("macros", None)
    return normalized


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


def test_assemble_site_publishes_every_declared_graph(tmp_path: Path) -> None:
    """Each manifest entry names a real specification and reaches the site."""

    output = tmp_path / "source"
    assemble_site(REPO_ROOT, output, build_graph=False)
    index = (output / "graphs" / "index.md").read_text(encoding="utf-8")
    for stem, (relative, label) in PUBLISHED_GRAPHS.items():
        assert (REPO_ROOT / relative).is_file(), f"missing specification: {relative}"
        assert (output / "graphs" / f"{stem}.html").stat().st_size > 0
        assert f"- [{label}]({stem}.html)" in index


def test_published_math_dependency_graph_is_scoped_self_contained_candidate() -> None:
    candidate = _task5_math_dependency_graph()
    payload = json.loads(candidate.read_text(encoding="utf-8"))
    schema = json.loads(MATH_DEPENDENCY_SCHEMA.read_text(encoding="utf-8"))
    Draft7Validator(schema).validate(payload)

    mathjax = [
        dependency
        for dependency in payload["renderer_dependencies"]
        if dependency.get("id") == "mathjax"
    ]
    assert len(mathjax) == 1
    macros = mathjax[0]["configuration"]["macros"]
    assert macros
    assert len(json.dumps(macros, separators=(",", ":")).encode("utf-8")) <= 4096
    assert candidate.stat().st_size <= 77_454 + 8192
    assert "macro_gap" not in payload.get("metadata", {})

    semantic_payload = json.dumps(
        _without_task5_macro_changes(payload),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert hashlib.sha256(semantic_payload).hexdigest() == (
        MATH_DEPENDENCY_SEMANTIC_SHA256
    )


def test_docs_publisher_consumes_embedded_math_macros_unchanged(
    tmp_path: Path, monkeypatch
) -> None:
    candidate = _task5_math_dependency_graph()
    expected = json.loads(candidate.read_text(encoding="utf-8"))
    repo = tmp_path / "repo"
    _write(repo / "README.md", "# Repository\n")
    _write(repo / "docs" / "README.md", "# Documentation\n")
    graph_relative = Path("fixtures/math-dependency.json")
    graph_path = repo / graph_relative
    graph_path.parent.mkdir(parents=True)
    graph_path.write_bytes(candidate.read_bytes())

    monkeypatch.setattr(
        site,
        "PUBLISHED_GRAPHS",
        {"math-dependency": (graph_relative, "Math dependency graph")},
    )
    captured = []
    original_write = GraphArtifactWriter.write

    def capture_write(self, payload, **kwargs):
        captured.append(copy.deepcopy(payload))
        return original_write(self, payload, **kwargs)

    monkeypatch.setattr(GraphArtifactWriter, "write", capture_write)
    output = tmp_path / "source"
    site.assemble_site(repo, output, build_graph=False)

    rendered = output / "graphs" / "math-dependency.html"
    assert captured == [expected]
    expected_macros = captured[0]["renderer_dependencies"][0]["configuration"]["macros"]
    assert expected_macros
    rendered_text = rendered.read_text(encoding="utf-8")
    embedded_config = re.search(
        r"window\.MathJax = (\{.*?\});\n\s*\(function", rendered_text, re.DOTALL
    )
    assert embedded_config is not None
    assert json.loads(embedded_config.group(1))["tex"]["macros"] == expected_macros
