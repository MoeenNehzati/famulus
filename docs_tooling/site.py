"""Assemble the bounded Famulus documentation surface for MkDocs.

The source repository contains working notes and implementation plans that are
not part of the public documentation website.  This module makes that boundary
explicit: it stages root-level ``docs/*.md`` files, contributor documentation,
and graph assets under an ignored build directory.  Links to files outside that
surface remain useful by becoming links to their GitHub source pages.

The assembler deliberately does not render Markdown or serve HTTP.  MkDocs owns
those standard presentation concerns; this module owns only Famulus-specific
publication policy and creation of the repository blueprint artifact.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import posixpath
import re
import shutil
from urllib.parse import quote


DEFAULT_REPOSITORY_URL = "https://github.com/MoeenNehzati/famulus"
DEFAULT_REPOSITORY_REF = "master"

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
    """Synchronize published Markdown and graph assets into ``output_dir``.

    Only paths owned by this synchronization pass are replaced.  In particular,
    ``graphs/blueprint`` is preserved so a MkDocs live-reload pass can refresh
    edited Markdown without regenerating the comparatively large interactive
    repository graph.

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

    published = _published_markdown_paths(docs_root)
    _clear_managed_site_sources(output)

    for source, destination_relative in published.items():
        destination = output / destination_relative
        destination.parent.mkdir(parents=True, exist_ok=True)
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

    graph_assets = _copy_graph_assets(docs_root / "graphs", output / "graphs")
    _write_graph_index(output / "graphs" / "index.md", graph_assets)
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
            from officina.common.visualization.from_blueprint import (
                build_blueprint_graph,
            )

            graph_builder = build_blueprint_graph
        graph_builder(
            root,
            output_dir=output / "graphs" / "blueprint",
            name="repository",
            write_json=False,
        )
    return output


def _validate_output_path(root: Path, docs_root: Path, output: Path) -> None:
    """Reject destinations whose cleanup could remove authoritative content."""

    if output in {root, docs_root}:
        raise ValueError(f"refusing to stage documentation over source path: {output}")


def _published_markdown_paths(docs_root: Path) -> dict[Path, Path]:
    """Return the exact source-to-staged mapping for public Markdown pages."""

    published: dict[Path, Path] = {}
    destinations: set[Path] = set()
    for source in sorted(docs_root.glob("*.md")):
        destination = Path("index.md") if source.name == "README.md" else Path(source.name)
        if destination in destinations:
            raise ValueError(f"duplicate documentation destination: {destination}")
        published[source.resolve()] = destination
        destinations.add(destination)

    contributor_root = docs_root / "contributors"
    if contributor_root.is_dir():
        for source in sorted(contributor_root.rglob("*.md")):
            destination = Path("contributors") / source.relative_to(contributor_root)
            published[source.resolve()] = destination
    return published


def _clear_managed_site_sources(output: Path) -> None:
    """Remove stale staged docs while retaining a previously built blueprint."""

    output.mkdir(parents=True, exist_ok=True)
    for markdown in output.glob("*.md"):
        markdown.unlink()
    contributors = output / "contributors"
    if contributors.exists():
        shutil.rmtree(contributors)

    graphs = output / "graphs"
    graphs.mkdir(parents=True, exist_ok=True)
    for child in graphs.iterdir():
        if child.name == "blueprint":
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def _copy_graph_assets(source_root: Path, destination_root: Path) -> list[Path]:
    """Copy non-Markdown curated graph assets and return their staged paths."""

    copied: list[Path] = []
    if not source_root.is_dir():
        return copied
    for source in sorted(path for path in source_root.rglob("*") if path.is_file()):
        if source.suffix.lower() == ".md":
            continue
        relative = source.relative_to(source_root)
        destination = destination_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        copied.append(relative)
    return copied


def _write_graph_index(destination: Path, assets: list[Path]) -> None:
    """Write the small navigable page that owns graph links in inferred nav."""

    lines = [
        "# Graphs",
        "",
        "- [Interactive repository blueprint](blueprint/repository.html)",
    ]
    for asset in assets:
        label = asset.stem.replace("-", " ").replace("_", " ").title()
        lines.append(f"- [{label}]({asset.as_posix()})")
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
