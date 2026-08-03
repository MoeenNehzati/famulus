"""Orchestration for docstring graph extraction and rendering."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
from urllib.parse import quote
from typing import Any, Iterable

from ..base_visualizer import BaseVisualizer, GraphSourceResolutionError
from .json_extractor import (
    DEFAULT_FORMATS,
    DocstringJsonExtractor,
    default_out_dir,
    gather_modules,
    gather_modules_in_directory,
)
from .renderer import DocstringRenderer


class DocstringVisualizer(BaseVisualizer):
    """Canonical orchestrator for docstring-driven graph visualization."""

    def __init__(
        self,
        *,
        schema: dict[str, Any] | str | Path | None = None,
        strict: bool = True,
        repo_roots: tuple[Path, ...] | None = None,
        extractor: DocstringJsonExtractor | None = None,
        renderer: DocstringRenderer | None = None,
    ) -> None:
        super().__init__(
            extractor=extractor or DocstringJsonExtractor(),
            renderer=renderer or DocstringRenderer(),
            schema=schema,
            strict=strict,
            repo_roots=repo_roots,
        )

    def generate_graph_for_module(
        self,
        module_path: Path,
        out_dir: Path,
        out_name: str,
        formats: Iterable[str],
        emit_dependency_json: bool = False,
        dependency_json_path: Path | None = None,
        *,
        infer_local_edges: bool = False,
    ) -> list[Path]:
        """Generate one module artifact set."""
        source = self.resolve_source(module_path)
        dependency_payload = self.build_payload(
            source,
            options={"infer_local_edges": infer_local_edges},
        )
        dot = self.renderer.to_dot(dependency_payload, title=module_path.as_posix())

        produced: list[Path] = []
        out_dir.mkdir(parents=True, exist_ok=True)
        dot_path = out_dir / f"{out_name}.dot"
        dot_path.write_text(dot, encoding="utf-8")

        for fmt in formats:
            if fmt == "dot":
                produced.append(dot_path)
                continue
            if fmt == "html":
                html_path = self.render_payload(
                    source,
                    dependency_payload,
                    output_dir=out_dir,
                    output_name=out_name,
                    render_html=True,
                    write_payload=False,
                ).html_path
                if html_path is not None:
                    produced.append(html_path)
                continue
            if fmt not in {"svg", "png"}:
                continue
            file_path = out_dir / f"{out_name}.{fmt}"
            subprocess.run(["dot", f"-T{fmt}", str(dot_path), "-o", str(file_path)], check=True)
            produced.append(file_path)

        if emit_dependency_json:
            json_path = (
                dependency_json_path
                if dependency_json_path is not None
                else out_dir / f"{out_name}.dependency.json"
            )
            artifact = self.artifacts.write(
                dependency_payload,
                output_dir=out_dir,
                stem=out_name,
                write_payload=True,
                write_presentation=False,
                payload_target=json_path,
            )
            if artifact.payload is not None:
                produced.append(artifact.payload)

        return produced

    def render_module_artifacts(
        self,
        target: str | Path,
        out_dir: Path | None = None,
        include_tests: bool = False,
        name: str | None = None,
        formats: Iterable[str] = DEFAULT_FORMATS,
        emit_dependency_json: bool = False,
        dependency_json_out: str | Path | None = None,
        infer_local_edges: bool = False,
        serve: bool = True,
        serve_host: str = "127.0.0.1",
        serve_port: int = 8765,
    ) -> list[Path]:
        """Render module visuals from docstring graph metadata."""
        target_text = str(target).strip()
        if not target_text:
            raise ValueError("target is required")

        target_path = Path(target_text).expanduser()
        target_is_directory = target_path.exists() and target_path.is_dir()

        if not target_path.exists():
            try:
                source = self.resolve_source(target_text)
            except GraphSourceResolutionError as exc:
                raise FileNotFoundError(f"target not found: {target_text}") from exc
            target_path = source.resolved_path
            target_is_directory = False

        if target_is_directory:
            modules = gather_modules_in_directory(target_path, include_tests=include_tests)
        elif target_path.is_file():
            modules = gather_modules(target_path)
        else:
            modules = []

        if not modules:
            raise FileNotFoundError(f"no python modules found in: {target_path}")

        resolved_out_dir = out_dir.resolve() if out_dir is not None else default_out_dir(target_path)

        if dependency_json_out:
            if len(modules) != 1:
                raise ValueError("--dependency-json-out requires a single module target.")
            json_out_path = Path(dependency_json_out).resolve()
        else:
            json_out_path = None

        produced_total: list[Path] = []
        for module in modules:
            if len(modules) == 1:
                output_name = name or f"{module.stem}_flowchart"
            else:
                output_name = f"{module.stem}_{name}" if name else f"{module.stem}_flowchart"
            produced_total.extend(
                self.generate_graph_for_module(
                    module,
                    resolved_out_dir,
                    output_name,
                    formats,
                    emit_dependency_json=emit_dependency_json,
                    dependency_json_path=json_out_path,
                    infer_local_edges=infer_local_edges,
                )
            )

        if serve:
            html_paths = [path for path in produced_total if path.suffix == ".html"]
            if html_paths:
                server = self.serve(html_paths[0].parent, serve_host, serve_port)
                print(f"serving graph artifacts from {server.directory} at {server.url}", flush=True)
                for html_path in html_paths:
                    rel = html_path.resolve().relative_to(server.directory)
                    print(f"wrote html: {server.url}{quote(rel.as_posix())}", flush=True)
            else:
                print(
                    "serve requested but no HTML artifact was generated (add html to --formats).",
                    flush=True,
                )

        return produced_total

    def build_docstring_graph(
        self,
        target: str | Path,
        *,
        out_dir: str | Path | None = None,
        include_tests: bool = False,
        name: str | None = None,
        formats: Iterable[str] | None = None,
        emit_dependency_json: bool = False,
        dependency_json_out: str | Path | None = None,
        infer_local_edges: bool = False,
        serve: bool = True,
        serve_host: str = "127.0.0.1",
        serve_port: int = 8765,
    ) -> list[Path]:
        """Generate graph artifacts for docstring metadata."""
        return self.render_module_artifacts(
            target=target,
            out_dir=Path(out_dir).resolve() if out_dir is not None else None,
            include_tests=include_tests,
            name=name,
            formats=list(formats) if formats is not None else DEFAULT_FORMATS,
            emit_dependency_json=emit_dependency_json,
            dependency_json_out=dependency_json_out,
            infer_local_edges=infer_local_edges,
            serve=serve,
            serve_host=serve_host,
            serve_port=serve_port,
        )


def render_module_artifacts(
    target: str | Path,
    out_dir: Path | None = None,
    include_tests: bool = False,
    name: str | None = None,
    formats: Iterable[str] = DEFAULT_FORMATS,
    emit_dependency_json: bool = False,
    dependency_json_out: str | Path | None = None,
    infer_local_edges: bool = False,
    serve: bool = True,
    serve_host: str = "127.0.0.1",
    serve_port: int = 8765,
) -> list[Path]:
    """Render docstring graph artifacts through the public orchestration facade.

    The module-level function preserves the stable API used by repository tools;
    ``DocstringVisualizer`` remains the owner of source resolution, extraction,
    artifact emission, and optional serving behavior.
    """
    return DocstringVisualizer().render_module_artifacts(
        target=target,
        out_dir=out_dir,
        include_tests=include_tests,
        name=name,
        formats=formats,
        emit_dependency_json=emit_dependency_json,
        dependency_json_out=dependency_json_out,
        infer_local_edges=infer_local_edges,
        serve=serve,
        serve_host=serve_host,
        serve_port=serve_port,
    )


def build_docstring_graph(
    target: str | Path,
    *,
    out_dir: str | Path | None = None,
    include_tests: bool = False,
    name: str | None = None,
    formats: Iterable[str] | None = None,
    emit_dependency_json: bool = False,
    dependency_json_out: str | Path | None = None,
    infer_local_edges: bool = False,
    serve: bool = True,
    serve_host: str = "127.0.0.1",
    serve_port: int = 8765,
) -> list[Path]:
    """Generate graph artifacts for docstring metadata."""
    return DocstringVisualizer().build_docstring_graph(
        target=target,
        out_dir=out_dir,
        include_tests=include_tests,
        name=name,
        formats=list(formats) if formats is not None else DEFAULT_FORMATS,
        emit_dependency_json=emit_dependency_json,
        dependency_json_out=dependency_json_out,
        infer_local_edges=infer_local_edges,
        serve=serve,
        serve_host=serve_host,
        serve_port=serve_port,
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "target",
        nargs="?",
        help="Python module file or directory to render.",
    )
    parser.add_argument(
        "--out-dir",
        default=None,
        help="Directory for generated flowchart artifacts.",
    )
    parser.add_argument(
        "--include-tests",
        action="store_true",
        help="Include test_*.py files when rendering directory.",
    )
    parser.add_argument(
        "--name",
        default=None,
        help="Output base filename for a single module. For directories, this value is ignored unless one module is selected.",
    )
    parser.add_argument(
        "--formats",
        nargs="+",
        default=list(DEFAULT_FORMATS),
        help="Output formats (include dot for source).",
    )
    parser.add_argument(
        "--dependency-json",
        action="store_true",
        help="Emit dependency JSON payload compatible with shared visualizer.",
    )
    parser.add_argument(
        "--dependency-json-out",
        default=None,
        help="Optional dependency JSON path (single-module mode only).",
    )
    infer_group = parser.add_mutually_exclusive_group()
    infer_group.add_argument(
        "--infer-edges",
        dest="infer_edges",
        action="store_true",
        help="Infer AST-local call edges from source (off by default).",
    )
    infer_group.add_argument(
        "--no-infer-edges",
        dest="infer_edges",
        action="store_false",
        help="Only show edges declared in docstring metadata.",
    )
    parser.set_defaults(infer_edges=False)
    parser.add_argument(
        "--html",
        action="store_true",
        help="Also emit interactive HTML (only if formats includes html).",
    )

    serve_group = parser.add_mutually_exclusive_group()
    serve_group.add_argument(
        "--serve",
        dest="serve",
        action="store_true",
        help="Start a local background server for generated HTML artifacts (default true).",
    )
    serve_group.add_argument(
        "--no-serve",
        dest="serve",
        action="store_false",
        help="Do not start a local background server.",
    )
    parser.set_defaults(serve=True)
    parser.add_argument("--serve-host", default="127.0.0.1", help="Server host for local artifact serving.")
    parser.add_argument("--serve-port", type=int, default=8765, help="Server port for local artifact serving.")
    args = parser.parse_args(argv)

    target = args.target
    if not target:
        parser.error("target is required")

    if args.html and "html" not in args.formats:
        args.formats.append("html")

    produced_total = DocstringVisualizer().render_module_artifacts(
        target=target,
        out_dir=Path(args.out_dir).resolve() if args.out_dir is not None else None,
        include_tests=args.include_tests,
        name=args.name,
        formats=args.formats,
        emit_dependency_json=args.dependency_json,
        infer_local_edges=args.infer_edges,
        dependency_json_out=args.dependency_json_out,
        serve=args.serve,
        serve_host=args.serve_host,
        serve_port=args.serve_port,
    )

    for path in produced_total:
        print(f"wrote: {path}")
    return 0


__all__ = [
    "DocstringVisualizer",
    "build_docstring_graph",
    "main",
]


if __name__ == "__main__":  # pragma: no cover - runtime dispatch
    raise SystemExit(main())
