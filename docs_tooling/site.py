"""Assemble the public Famulus documentation surface for MkDocs.

The source repository contains working notes and implementation plans that are
not part of the public documentation website.  This module makes that boundary
explicit: it stages the repository ``README.md`` as the homepage and the
complete ``docs`` tree except the private subtrees under an ignored build
directory.
Links to files outside that surface remain useful by becoming links to their
GitHub source pages.

The assembler deliberately does not render Markdown or serve HTTP.  MkDocs owns
those standard presentation concerns; this module owns only Famulus-specific
publication policy and creation of the repository blueprint artifact.
"""

from __future__ import annotations

from collections.abc import Callable
import json
from pathlib import Path
import posixpath
import re
import shutil
from urllib.parse import quote


DEFAULT_REPOSITORY_URL = "https://github.com/MoeenNehzati/famulus"
DEFAULT_REPOSITORY_REF = "master"

# Checked-in graph specifications published as standalone pages, mapping page
# stem to its repository-relative specification and link label.  These are
# skill outputs, so a visitor sees what the producing skill actually generates.
PUBLISHED_GRAPHS: dict[str, tuple[Path, str]] = {
    "math-dependency": (
        Path(
            "skills/math-dependency-graph/assets/inference-from-random-restarts"
            "/results/extraction-latest.json"
        ),
        "Math dependency graph of a paper appendix",
    ),
}

# Working notes and implementation plans live under ``docs`` for the assistant
# to read, but they are not documentation. ``superpowers`` is also gitignored,
# so excluding it here keeps a local build showing exactly what the published
# site shows rather than whatever happens to be on this disk.
_PRIVATE_SUBTREES = frozenset({"plans", "superpowers"})

_MARKDOWN_LINK = re.compile(
    r"(?P<prefix>!?\[[^\]\n]*\]\()(?P<destination>[^)\n]+)(?P<suffix>\))"
)
_FENCE = re.compile(r"^\s*(```|~~~)")

GraphBuilder = Callable[..., list[Path]]


def sync_published_docs(
    repo_root: str | Path,
    output_dir: str | Path,
    *,
    repository_url: str = DEFAULT_REPOSITORY_URL,
    repository_ref: str = DEFAULT_REPOSITORY_REF,
) -> Path:
    """Synchronize public documentation files into ``output_dir``.

    Only paths owned by this synchronization pass are replaced.  In particular,
    ``graphs/blueprint`` is preserved so a MkDocs live-reload pass can refresh
    edited documentation without regenerating the comparatively large
    interactive repository graph.

    Args:
        repo_root: Famulus repository whose ``docs`` tree is authoritative.
        output_dir: Ignored MkDocs source directory to populate.
        repository_url: Public repository URL used for unpublished targets.
        repository_ref: Git ref used in generated GitHub source links.

    Returns:
        The resolved MkDocs source directory.

    Raises:
        FileNotFoundError: If the repository has no ``docs`` directory.
        ValueError: If staging would overwrite the repository or source docs.
    """

    root = Path(repo_root).resolve()
    docs_root = root / "docs"
    output = Path(output_dir).resolve()
    _validate_output_path(root, docs_root, output)
    if not docs_root.is_dir():
        raise FileNotFoundError(f"documentation directory does not exist: {docs_root}")

    published = _published_paths(root, docs_root)
    _clear_managed_site_sources(output)

    for source, destination_relative in published.items():
        destination = output / destination_relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.suffix.lower() == ".md":
            markdown = source.read_text(encoding="utf-8")
            destination.write_text(
                _rewrite_links(
                    markdown,
                    source=source,
                    destination=destination_relative,
                    repo_root=root,
                    published=published,
                    repository_url=repository_url.rstrip("/"),
                    repository_ref=repository_ref,
                ),
                encoding="utf-8",
            )
        else:
            shutil.copy2(source, destination)

    _write_graph_index(output / "graphs" / "index.md")
    return output


def assemble_site(
    repo_root: str | Path,
    output_dir: str | Path,
    *,
    build_graph: bool = True,
    graph_builder: GraphBuilder | None = None,
    repository_url: str = DEFAULT_REPOSITORY_URL,
    repository_ref: str = DEFAULT_REPOSITORY_REF,
) -> Path:
    """Create a fresh staged documentation source tree for MkDocs.

    The destination is removed before assembly so deleted source documents
    cannot survive into a later deployment.  The interactive graph is generated
    directly below the ignored destination and JSON output is disabled because
    the standalone HTML contains the complete viewer payload.
    """

    root = Path(repo_root).resolve()
    output = Path(output_dir).resolve()
    _validate_output_path(root, root / "docs", output)
    if output.exists():
        shutil.rmtree(output)

    sync_published_docs(
        root,
        output,
        repository_url=repository_url,
        repository_ref=repository_ref,
    )
    if build_graph:
        if graph_builder is None:
            from officina.visualization.from_blueprint.visualizer import (
                build_blueprint_graph,
            )

            graph_builder = build_blueprint_graph
        graph_builder(
            root,
            output_dir=output / "graphs" / "blueprint",
            name="repository",
            write_json=False,
        )
    _render_published_graphs(root, output / "graphs")
    return output


def _render_published_graphs(root: Path, destination: Path) -> None:
    """Render each checked-in graph specification as a standalone page."""

    from officina.visualization.artifacts import GraphArtifactWriter
    from officina.visualization.elk_html_renderer import ElkHtmlRenderer
    from officina.visualization.html_renderer.quick_guides.default import DEFAULT_QUICK_GUIDE

    writer = GraphArtifactWriter(ElkHtmlRenderer(quick_guide=DEFAULT_QUICK_GUIDE))
    for stem, (relative, _) in PUBLISHED_GRAPHS.items():
        source = root / relative
        # A repository without the specification simply publishes no graph;
        # a test that checks the manifest owns that failure instead.
        if source.is_file():
            writer.write(
                json.loads(source.read_text(encoding="utf-8")),
                output_dir=destination,
                stem=stem,
                write_payload=False,
            )


def _validate_output_path(root: Path, docs_root: Path, output: Path) -> None:
    """Reject destinations whose cleanup could remove authoritative content."""

    if output in {root, docs_root}:
        raise ValueError(f"refusing to stage documentation over source path: {output}")


def _published_paths(repo_root: Path, docs_root: Path) -> dict[Path, Path]:
    """Return the source-to-staged mapping for public documentation files."""

    root_readme = repo_root / "README.md"
    if not root_readme.is_file():
        raise FileNotFoundError(f"repository README does not exist: {root_readme}")

    published: dict[Path, Path] = {root_readme.resolve(): Path("index.md")}
    destinations: set[Path] = {Path("index.md")}
    for source in sorted(docs_root.rglob("*")):
        relative = source.relative_to(docs_root)
        if not source.is_file() or source.is_symlink():
            continue
        if relative.parts[0] in _PRIVATE_SUBTREES:
            continue
        destination = (
            Path("documentation/index.md")
            if relative == Path("README.md")
            else relative
        )
        if destination in destinations:
            raise ValueError(f"duplicate documentation destination: {destination}")
        published[source.resolve()] = destination
        destinations.add(destination)
    return published


def _clear_managed_site_sources(output: Path) -> None:
    """Remove stale staged docs while retaining previously built graphs.

    Only ``assemble_site`` renders graphs, so a live-reload pass through this
    function must leave them alone or nothing would rebuild them.
    """

    retained = {"blueprint"} | {f"{stem}.html" for stem in PUBLISHED_GRAPHS}
    output.mkdir(parents=True, exist_ok=True)
    for child in output.iterdir():
        if child.name == "graphs" and child.is_dir() and not child.is_symlink():
            for graph_child in child.iterdir():
                if graph_child.name in retained:
                    continue
                _remove_path(graph_child)
            continue
        _remove_path(child)

    (output / "graphs").mkdir(parents=True, exist_ok=True)


def _remove_path(path: Path) -> None:
    """Remove one staged file or directory without following symlinks."""

    if path.is_symlink() or not path.is_dir():
        path.unlink()
    else:
        shutil.rmtree(path)


def _write_graph_index(destination: Path) -> None:
    """Write the navigable page that owns the interactive graph links."""

    lines = [
        "# Graphs",
        "",
        "- [Interactive repository blueprint](blueprint/repository.html)",
    ]
    lines += [f"- [{label}]({stem}.html)" for stem, (_, label) in PUBLISHED_GRAPHS.items()]
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _rewrite_links(
    markdown: str,
    *,
    source: Path,
    destination: Path,
    repo_root: Path,
    published: dict[Path, Path],
    repository_url: str,
    repository_ref: str,
) -> str:
    """Rewrite relative Markdown links without touching fenced code examples."""

    rewritten: list[str] = []
    fence_marker: str | None = None
    for line in markdown.splitlines(keepends=True):
        fence = _FENCE.match(line)
        if fence:
            marker = fence.group(1)
            if fence_marker is None:
                fence_marker = marker
            elif marker == fence_marker:
                fence_marker = None
            rewritten.append(line)
            continue
        if fence_marker is not None:
            rewritten.append(line)
            continue
        rewritten.append(
            _MARKDOWN_LINK.sub(
                lambda match: _rewrite_link_match(
                    match,
                    source=source,
                    destination=destination,
                    repo_root=repo_root,
                    published=published,
                    repository_url=repository_url,
                    repository_ref=repository_ref,
                ),
                line,
            )
        )
    return "".join(rewritten)


def _rewrite_link_match(
    match: re.Match[str],
    *,
    source: Path,
    destination: Path,
    repo_root: Path,
    published: dict[Path, Path],
    repository_url: str,
    repository_ref: str,
) -> str:
    raw_destination = match.group("destination")
    target, separator, title = raw_destination.partition(" ")
    if _is_external_or_page_local(target):
        return match.group(0)

    path_text, anchor_separator, anchor = target.partition("#")
    if not path_text:
        return match.group(0)
    resolved = (source.parent / path_text).resolve()
    staged_target = published.get(resolved)
    if staged_target is not None:
        relative = posixpath.relpath(staged_target.as_posix(), destination.parent.as_posix())
        replacement = relative
    else:
        try:
            repository_relative = resolved.relative_to(repo_root)
        except ValueError:
            return match.group(0)
        route = "tree" if resolved.is_dir() else "blob"
        replacement = (
            f"{repository_url}/{route}/{quote(repository_ref, safe='')}/"
            f"{quote(repository_relative.as_posix(), safe='/')}"
        )
    if anchor_separator:
        replacement += f"#{anchor}"
    if separator:
        replacement += f" {title}"
    return f"{match.group('prefix')}{replacement}{match.group('suffix')}"


def _is_external_or_page_local(target: str) -> bool:
    """Return whether a target should pass through without repository lookup."""

    lowered = target.lower()
    return (
        target.startswith(("#", "/", "//"))
        or "://" in target
        or lowered.startswith(("mailto:", "tel:", "data:"))
    )


__all__ = ["assemble_site", "sync_published_docs"]
