"""Renderer layer for docstring graph payloads."""

from __future__ import annotations

from ..elk_html_renderer import ElkHtmlRenderer


class DocstringRenderer(ElkHtmlRenderer):
    """Renderer extension for docstring dependency payloads."""

    @staticmethod
    def _to_dot_from_dependency_json(
        document: dict[str, object],
        title: str | None = None,
    ) -> str:
        """Render a compact DOT document from a visualization JSON payload."""
        entities = document.get("entities", []) if isinstance(document, dict) else []
        if not isinstance(entities, list):
            entities = []

        title = title or "docstring_dependency_graph"

        lines = [
            "digraph DocstringFlow {",
            "  graph [",
            '    rankdir="TB";',
            "    splines=ortho;",
            "    nodesep=0.65;",
            "    ranksep=0.75;",
            '    fontname="Arial";',
            "    fontsize=11;",
            '    style="rounded";',
            "  ];",
            "  node [shape=box, fontname=\"Arial\", fontsize=10, style=\"rounded,filled\"];",
            "  edge [fontname=\"Arial\", fontsize=9, arrowsize=0.85];",
            f'  label="{title}";',
            '  labelloc="t";',
            '  color="#cbd5e1";',
            "",
        ]

        style_map = {
            "call": ("dashed", "#2563eb"),
            "instantiation": ("dashed", "#7c3aed"),
            "wraps": ("dashed", "#0f766e"),
            "dispatch": ("dashed", "#be123c"),
            "documented-call": ("dashed", "#2563eb"),
            "instantiate": ("dashed", "#7c3aed"),
            "pipeline-call": ("dashed", "#ea580c"),
            "noninferable": ("dashed", "#92400e"),
            "pipeline-phase": ("solid", "#374151"),
            "phase-member": ("dotted", "#4b5563"),
            "reference": ("solid", "#0f172a"),
            "doc-graph": ("dashed", "#16a34a"),
            "doc-implementation": ("dashed", "#16a34a"),
            "inferred": ("dashed", "#64748b"),
        }
        edge_seen: set[tuple[str, str]] = set()

        for entity in sorted(entities, key=lambda value: str(value.get("id", ""))):
            if not isinstance(entity, dict):
                continue
            node_id = str(entity.get("id", "") or "").strip()
            if not node_id:
                continue
            lines.append(f'  "{node_id}" [label="{node_id}", fillcolor="#e2e8f0"];')

        for entity in sorted(entities, key=lambda value: str(value.get("id", ""))):
            if not isinstance(entity, dict):
                continue
            source = str(entity.get("id", "") or "").strip()
            if not source:
                continue
            raw_connects = entity.get("connects_to", [])
            if not isinstance(raw_connects, list):
                continue

            for edge in raw_connects:
                if not isinstance(edge, dict):
                    continue
                target = str(edge.get("to", "") or "").strip()
                if not target:
                    continue

                relation = str(edge.get("type", "inferred") or "inferred")
                style, color = style_map.get(relation, ("solid", "#0f172a"))
                edge_key = (source, target, relation)
                if edge_key in edge_seen:
                    continue
                edge_seen.add(edge_key)
                label = str(edge.get("label") or edge.get("edge_label") or relation)
                lines.append(
                    f'  "{source}" -> "{target}" [label="{label}", style="{style}", color="{color}", fontcolor="{color}"];'
                )

        lines.append("}")
        return "\n".join(lines) + "\n"

    def to_dot(
        self,
        document: dict[str, object],
        *,
        title: str | None = None,
    ) -> str:
        """Render DOT source from a docstring payload."""
        return self._to_dot_from_dependency_json(document, title=title)


__all__ = [
    "DocstringRenderer",
]
