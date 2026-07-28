#!/usr/bin/env python3
"""Render docstring-aware module flowcharts.

The renderer consumes parsed docstring metadata and infers additional call edges
from AST structure.
"""

from __future__ import annotations

import argparse
import ast
import json
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Iterable

from officina.common.docstring.docstring_parser import (
    PipelineSpec,
    FunctionSpec,
    parse_function_graphs,
    parse_pipeline,
)


DEFAULT_FORMATS = ("dot", "svg", "png")

_ROLE_BUCKET_RULES = [
    ("orchestrator", ("orchestr", "pipeline", "dispatcher", "driver", "coordinator", "schedule", "manager")),
    ("validator", ("validate", "validator", "verification", "check", "guard", "assert", "enforce")),
    ("io", ("read", "write", "load", "save", "fetch", "persist", "export", "import", "stream", "serialize")),
    ("transformer", ("transform", "convert", "normalize", "parse", "encode", "decode", "build", "render", "compile", "resolve")),
    ("api", ("call_", "request", "response", "endpoint", "dispatch", "invoke", "execute", "route")),
]


def _infer_role_bucket(role: str | None, node_id: str, node_type: str) -> str:
    """Infer a compact role bucket from call metadata."""
    haystack = f"{role or ''} {node_id}".lower()
    for bucket, keywords in _ROLE_BUCKET_RULES:
        if any(token in haystack for token in keywords):
            return bucket
    if node_type == "class":
        return "class"
    if node_id.endswith(".__init__"):
        return "lifecycle"
    return "utility"


def _infer_tier(in_degree: int, out_degree: int) -> str:
    """Classify node position based on in/out degree."""
    if in_degree == 0 and out_degree == 0:
        return "orphan"
    if in_degree == 0:
        return "entry"
    if out_degree == 0:
        return "leaf"
    return "core"


def to_dot(
    module_path: str,
    pipeline: PipelineSpec,
    function_specs: dict[str, FunctionSpec],
    inferred_edges: dict[str, set[str]],
    class_nodes: set[str] | None = None,
) -> str:
    """Render one DOT document from module graph metadata."""
    class_nodes = class_nodes or set()
    defined_nodes = set(inferred_edges)
    defined_nodes.update(class_nodes)
    for source, targets in inferred_edges.items():
        defined_nodes.update(targets)

    for source, spec in function_specs.items():
        defined_nodes.add(source)
        for source_edge, target_edge in spec.noninferable_calls:
            defined_nodes.add(source_edge)
            defined_nodes.add(target_edge)

    for targets in pipeline.phase_members.values():
        defined_nodes.update(targets)

    phase_lookup: dict[str, str] = {}
    for phase in pipeline.phases:
        for node in pipeline.phase_members.get(phase, []):
            phase_lookup[node] = phase

    for node, spec in function_specs.items():
        if spec.phase:
            phase_lookup[node] = spec.phase

    palette = [
        "#e9d5ff",
        "#bfdbfe",
        "#fef08a",
        "#d9f99d",
        "#a7f3d0",
        "#bae6fd",
        "#c7d2fe",
        "#fda4af",
    ]

    phase_order = pipeline.phases or sorted({value for value in phase_lookup.values() if value})
    phase_colors = {
        phase: palette[index % len(palette)] for index, phase in enumerate(phase_order)
    }
    if "orphan" not in phase_colors:
        phase_colors["orphan"] = "#f8fafc"

    all_edges: dict[tuple[str, str], str] = {}
    for source, targets in inferred_edges.items():
        for target in sorted(targets):
            if source in defined_nodes and target in defined_nodes:
                _add_edge(all_edges, source, target, "inferred")

    for source_spec in function_specs.values():
        for source, target in source_spec.noninferable_calls:
            if source in defined_nodes and target in defined_nodes:
                _add_edge(all_edges, source, target, "noninferable")

    for class_name in sorted(class_nodes):
        class_prefix = f"{class_name}."
        for method_id in sorted(function_specs):
            if method_id.startswith(class_prefix) and method_id.rsplit(".", 1)[0] == class_name:
                if class_name in defined_nodes and method_id in defined_nodes:
                    _add_edge(all_edges, class_name, method_id, "contains")

    for source, target in pipeline.noninferable_calls:
        if source in defined_nodes and target in defined_nodes:
            if (target, source) in all_edges and all_edges.get((target, source), "") in {
                "inferred",
                "noninferable",
            }:
                continue
            _add_edge(all_edges, source, target, "noninferable")

    nodes_by_phase: dict[str, list[str]] = {}
    for node in sorted(defined_nodes):
        phase = phase_lookup.get(node, "orphan")
        nodes_by_phase.setdefault(phase, []).append(node)

    lines = [
        "digraph DocstringFlow {",
        "  graph [",
        '    rankdir="TB";',
        "    splines=ortho;",
        "    nodesep=0.65;",
        "    ranksep=0.75;",
        '    fontname="Arial";',
        "    fontsize=11;",
        "  ];",
        "  node [",
        '    shape=box;',
        '    fontname="Arial";',
        "    fontsize=10;",
        '    style="rounded,filled";',
        "  ];",
        "  edge [fontname=\"Arial\", fontsize=9, arrowsize=0.85];",
        "",
        f'  label="{module_path}";',
        '  labelloc="t";',
        '  fontcolor="#334155";',
        '  color="#cbd5e1";',
        "",
    ]

    for phase in phase_order + ["orphan"]:
        nodes = nodes_by_phase.get(phase, [])
        if not nodes:
            continue
        fill = phase_colors.get(phase, "#e2e8f0")
        lines.append(f"  subgraph cluster_{phase} {{")
        lines.append(f'    label="{phase}";')
        lines.append('    style="filled,rounded";')
        lines.append(f'    color="{fill}";')
        lines.append(f'    bgcolor="{fill}30";')
        lines.append('    fontname="Arial";')
        for node in nodes:
            lines.append(
                f'    "{node}" [label="{node}", fillcolor="{fill}",'
                ' margin="0.12,0.06"];'
            )
        lines.append("  }")
        lines.append("")

    for (source, target), relation in sorted(all_edges.items()):
        if relation == "contains":
            lines.append(
                f'  "{source}" -> "{target}" [style=dotted,'
                ' color="#334155", fontcolor="#334155"];'
            )
        elif relation == "noninferable":
            lines.append(
                f'  "{source}" -> "{target}" [style=dashed,'
                ' color="#b45309", fontcolor="#b45309"];'
            )
        else:
            lines.append(f'  "{source}" -> "{target}";')

    lines.append("}")
    return "\n".join(lines) + "\n"


def to_dependency_json(
    module_path: Path,
    pipeline: PipelineSpec,
    function_specs: dict[str, FunctionSpec],
    inferred_edges: dict[str, set[str]],
    defined_nodes: set[str] | None = None,
    class_nodes: set[str] | None = None,
) -> dict[str, object]:
    """Build a canonical dependency JSON payload for shared interactive renderers."""
    if defined_nodes is None:
        defined_nodes = set(function_specs)
    else:
        defined_nodes = set(defined_nodes)

    class_nodes = class_nodes or set()

    defined_nodes.update(class_nodes)

    for source, targets in inferred_edges.items():
        defined_nodes.add(source)
        defined_nodes.update(targets)

    for source, spec in function_specs.items():
        defined_nodes.add(source)
        for source_edge, target_edge in spec.noninferable_calls:
            defined_nodes.add(source_edge)
            defined_nodes.add(target_edge)

    for source, target in pipeline.noninferable_calls:
        defined_nodes.add(source)
        defined_nodes.add(target)

    for targets in pipeline.phase_members.values():
        defined_nodes.update(targets)

    phase_lookup: dict[str, str] = {}
    for phase in pipeline.phases:
        for node in pipeline.phase_members.get(phase, []):
            phase_lookup[node] = phase

    for node, spec in function_specs.items():
        if spec.phase:
            phase_lookup[node] = spec.phase

    all_edges: dict[tuple[str, str], str] = {}
    for source, targets in inferred_edges.items():
        for target in sorted(targets):
            if source in defined_nodes and target in defined_nodes:
                _add_edge(all_edges, source, target, "inferred")

    for source_spec in function_specs.values():
        for source, target in source_spec.noninferable_calls:
            if source in defined_nodes and target in defined_nodes:
                _add_edge(all_edges, source, target, "noninferable")

    for class_name in sorted(class_nodes):
        class_prefix = f"{class_name}."
        for method_id in sorted(function_specs):
            if method_id.startswith(class_prefix) and method_id.rsplit(".", 1)[0] == class_name:
                if class_name in defined_nodes and method_id in defined_nodes:
                    _add_edge(all_edges, class_name, method_id, "contains")

    for source, target in pipeline.noninferable_calls:
        if source in defined_nodes and target in defined_nodes:
            if (target, source) in all_edges and all_edges.get((target, source), "") in {
                "inferred",
                "noninferable",
            }:
                continue
            _add_edge(all_edges, source, target, "noninferable")

    in_degree = {node_id: 0 for node_id in sorted(defined_nodes)}
    out_degree = {node_id: 0 for node_id in sorted(defined_nodes)}
    for (source, target), _ in all_edges.items():
        if source in in_degree and target in out_degree:
            in_degree[target] += 1
            out_degree[source] += 1

    entities: list[dict[str, object]] = []

    def _spec_blurb(callable_id: str) -> str:
        candidate = function_specs.get(callable_id)
        if not candidate:
            return ""
        if candidate.summary:
            return candidate.summary.strip()
        if candidate.role:
            return candidate.role.strip()
        return ""

    for position, node_id in enumerate(sorted(defined_nodes), start=1):
        spec = function_specs.get(node_id)
        node_phase = phase_lookup.get(node_id)
        node_type = "function"
        if node_id in class_nodes:
            node_type = "class"
        elif "." in node_id:
            node_type = "method"
        role_bucket = _infer_role_bucket(spec.role if spec else None, node_id, node_type)
        in_count = in_degree.get(node_id, 0)
        out_count = out_degree.get(node_id, 0)
        tier = _infer_tier(in_count, out_count)
        role_summary = (spec.role or "").strip() if spec else ""
        if not role_summary:
            role_summary = "Docstring role not documented."
        container = ""
        if node_type == "method":
            maybe_container = node_id.rsplit(".", 1)[0]
            if maybe_container in class_nodes:
                container = maybe_container
        depends_on = []
        for (source, target), relation in sorted(all_edges.items()):
            if source != node_id:
                continue
            target_blurb = _spec_blurb(target)
            source_blurb = _spec_blurb(source)
            if relation == "inferred":
                if target_blurb:
                    relation_description = f"calls {target}: {target_blurb}"
                else:
                    relation_description = "AST call from function body."
            elif relation == "contains":
                if target_blurb:
                    relation_description = f"contains {target}: {target_blurb}"
                else:
                    relation_description = "Contains-method relationship between class and callable."
            else:
                if source_blurb and source_blurb != target_blurb and target_blurb:
                    relation_description = (
                        "documented explicit call to "
                        f"{target}: {target_blurb}"
                    )
                else:
                    relation_description = (
                        "Explicit non-inferable call (documented in Graph/NonInferableCalls)."
                    )
            edge_label = (
                "calls"
                if relation == "inferred"
                else "contains"
                if relation == "contains"
                else "documented-call"
            )
            depends_on.append(
                {
                    "id": target,
                    "use_type": relation,
                    "confidence": "Verified"
                    if relation in {"inferred", "contains"}
                    else "Likely",
                    "evidence": relation_description,
                    "edge_label": edge_label,
                    "description": (
                        f"{source} -> {target}: {relation} ({relation_description}) "
                        f"{'(phase '+node_phase+')' if node_phase else ''}".strip()
                    ),
                }
            )

        doc_summary = ""
        doc_sections = {}
        signature = ""
        description = (spec.role or "").strip() if spec else ""
        if spec:
            doc_summary = (spec.summary or "").strip()
            if spec.sections:
                doc_sections = {key: value for key, value in spec.sections.items() if value}
            signature = (spec.signature or "").strip()
        if doc_summary:
            description = doc_summary
        elif not description:
            description = "Docstring-aware callable."
            if node_phase:
                description = f"Docstring-aware callable in phase {node_phase}."

        entities.append(
            {
                "id": node_id,
                "type": node_type,
                "role_bucket": role_bucket,
                "role_summary": role_summary,
                "in_degree": in_count,
                "out_degree": out_count,
                "tier": tier,
                "container": container,
                "short_title": node_id,
                "signature": signature,
                "ref": "",
                "position": position,
                "depends_on": depends_on,
                "title": node_id,
                "description": description,
                "documentation": {
                    "summary": doc_summary,
                    "sections": doc_sections,
                },
                "defined": f"{module_path}:{node_phase}" if node_phase else str(module_path),
                "active_in": node_phase or "",
                "source": "explicit",
            }
        )

    return {
        "document": {
            "title": module_path.as_posix(),
            "source_file": module_path.name,
        },
        "entities": entities,
    }


def write_dependency_json(document: dict[str, object], target_path: Path) -> Path:
    """Write dependency graph JSON in canonical form."""
    target_path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return target_path


def collect_defined_callables(tree: ast.AST) -> tuple[set[str], set[str]]:
    """Collect defined top-level callables and class names."""
    names: set[str] = set()
    class_names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.ClassDef):
            names.add(node.name)
            class_names.add(node.name)
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    names.add(f"{node.name}.{child.name}")
    return names, class_names


def _call_target(
    node: ast.AST,
    defined: set[str],
    in_class: str | None,
) -> str | None:
    """Resolve a call expression to a known callable target."""
    if isinstance(node, ast.Name):
        return node.id if node.id in defined else None

    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        left = node.value.id
        candidate = f"{left}.{node.attr}"
        if candidate in defined:
            return candidate
        if left == "self" and in_class is not None:
            method = f"{in_class}.{node.attr}"
            if method in defined:
                return method
        if node.attr in defined:
            return node.attr

    return None


def infer_call_edges(tree: ast.AST, defined: set[str]) -> dict[str, set[str]]:
    """Infer intra-module call edges by AST walking."""
    edges: dict[str, set[str]] = defaultdict(set)

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            caller = node.name
            for call in ast.walk(node):
                if not isinstance(call, ast.Call):
                    continue
                target = _call_target(call.func, defined, None)
                if target is not None:
                    edges[caller].add(target)
        elif isinstance(node, ast.ClassDef):
            class_name = node.name
            for child in node.body:
                if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                caller = f"{class_name}.{child.name}"
                for call in ast.walk(child):
                    if not isinstance(call, ast.Call):
                        continue
                    target = _call_target(call.func, defined, class_name)
                    if target is not None:
                        edges[caller].add(target)

    return edges


def _add_edge(
    edges: dict[tuple[str, str], str],
    source: str,
    target: str,
    relation: str,
) -> None:
    """Insert or upgrade a graph edge relation."""
    current = edges.get((source, target))
    if current == "noninferable" or relation == "noninferable":
        edges[(source, target)] = "noninferable"
    elif current is None:
        edges[(source, target)] = relation


def parse_module(module_path: Path) -> tuple[
    PipelineSpec,
    dict[str, FunctionSpec],
    dict[str, set[str]],
    set[str],
    set[str],
]:
    """Parse a module end-to-end into pipeline and graph inputs."""
    source = module_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    module_doc = ast.get_docstring(tree) or ""
    pipeline = parse_pipeline(module_doc)
    function_specs = parse_function_graphs(tree)
    defined, class_nodes = collect_defined_callables(tree)
    inferred = infer_call_edges(tree, defined)
    return pipeline, function_specs, inferred, defined, class_nodes


def generate_graph_for_module(
    module_path: Path,
    out_dir: Path,
    out_name: str,
    formats: Iterable[str],
    emit_dependency_json: bool = False,
    dependency_json_path: Path | None = None,
) -> list[Path]:
    """Generate flowchart artifacts for one module."""
    pipeline, function_specs, inferred, defined_nodes, class_nodes = parse_module(module_path)
    dot = to_dot(
        module_path.as_posix(),
        pipeline,
        function_specs,
        inferred,
        class_nodes=class_nodes,
    )
    produced: list[Path] = []

    out_dir.mkdir(parents=True, exist_ok=True)
    dot_path = out_dir / f"{out_name}.dot"
    dot_path.write_text(dot, encoding="utf-8")

    for fmt in formats:
        if fmt == "dot":
            produced.append(dot_path)
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
        document = to_dependency_json(
            module_path,
            pipeline,
            function_specs,
            inferred,
            defined_nodes=defined_nodes,
            class_nodes=class_nodes,
        )
        write_dependency_json(document, json_path)
        produced.append(json_path)

    return produced


def render_module_artifacts(
    target: str | Path,
    out_dir: Path | None = None,
    include_tests: bool = False,
    name: str | None = None,
    formats: Iterable[str] = DEFAULT_FORMATS,
    emit_dependency_json: bool = False,
    dependency_json_out: str | Path | None = None,
) -> list[Path]:
    """Render module visual artifacts into a shared dependency-aware output set."""
    target_path = Path(target).resolve()
    if not target_path.exists():
        raise FileNotFoundError(f"target not found: {target_path}")

    if target_path.is_file():
        modules = gather_modules(target_path)
    else:
        modules = gather_modules_in_directory(target_path, include_tests=include_tests)

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
            generate_graph_for_module(
                module,
                resolved_out_dir,
                output_name,
                formats,
                emit_dependency_json=emit_dependency_json,
                dependency_json_path=json_out_path,
            )
        )

    return produced_total


def gather_modules(target: Path) -> list[Path]:
    """Return a module list from a direct file target."""
    return [target]


def gather_modules_in_directory(target: Path, include_tests: bool) -> list[Path]:
    """Collect Python modules from a directory tree."""
    modules: list[Path] = []
    for path in sorted(target.rglob("*.py")):
        if path.name == "__init__.py":
            continue
        if path.name.startswith("."):
            continue
        if "graphs" in path.parts:
            continue
        if not include_tests and path.name.startswith("test_"):
            continue
        if ".git" in path.parts:
            continue
        modules.append(path)
    return modules


def default_out_dir(target: Path) -> Path:
    """Return the default output directory for generated artifacts."""
    if target.is_file():
        return target.parent / "graphs"
    return target / "graphs"


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for the shared graph renderer."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "target",
        nargs="?",
        help="Python module file or directory to render.",
    )
    parser.add_argument(
        "--module",
        dest="legacy_module",
        help="Backward-compatible alias for target module.",
    )
    parser.add_argument(
        "--out-dir",
        default=None,
        help="Directory for generated flowchart artifacts.",
    )
    parser.add_argument(
        "--include-tests",
        action="store_true",
        help="Include test_*.py files when rendering a directory.",
    )
    parser.add_argument(
        "--name",
        default=None,
        help="Output base filename for a single module. For directories, this value is ignored unless only one module is selected.",
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
        help="Emit dependency JSON payload compatible with math-dependency-graph renderer.",
    )
    parser.add_argument(
        "--dependency-json-out",
        default=None,
        help="Optional dependency JSON output path (single-module mode only).",
    )
    args = parser.parse_args(argv)

    target = args.target or args.legacy_module
    if not target:
        parser.error("Either target or --module must be provided")

    produced_total = render_module_artifacts(
        target=target,
        out_dir=Path(args.out_dir).resolve() if args.out_dir is not None else None,
        include_tests=args.include_tests,
        name=args.name,
        formats=args.formats,
        emit_dependency_json=args.dependency_json,
        dependency_json_out=args.dependency_json_out,
    )

    for path in produced_total:
        print(f"wrote: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
