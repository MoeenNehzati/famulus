"""Resolve graph-declared optional browser dependencies through trusted assets."""

from __future__ import annotations

import html
import json
import re
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any


_VENDOR_DIRECTORY = Path(__file__).parent / "vendor"


@lru_cache(maxsize=1)
def _mathjax_runtime() -> str:
    """Load the pinned offline MathJax runtime and make it script-safe."""
    runtime = (_VENDOR_DIRECTORY / "mathjax-3.2.2-tex-svg.js").read_text(
        encoding="utf-8"
    )
    return re.sub(r"</script", lambda _match: r"<\/script", runtime, flags=re.IGNORECASE)


def _script_json(value: object) -> str:
    """Serialize configuration without permitting an embedded script terminator."""
    return json.dumps(value, indent=8).replace("</", "<\\/")


def _mathjax_head(dependency: Mapping[str, Any]) -> str:
    """Return the trusted MathJax 3 TeX-to-SVG loader and graph configuration."""
    version = dependency.get("version")
    if version != "3":
        raise ValueError(f"unsupported MathJax renderer dependency version: {version!r}")
    configuration = dependency.get("configuration", {})
    if not isinstance(configuration, Mapping):
        raise ValueError("MathJax renderer dependency configuration must be an object.")
    macros = configuration.get("macros", {})
    if not isinstance(macros, Mapping):
        raise ValueError("MathJax renderer dependency macros must be an object.")
    mathjax_configuration = {
        "tex": {
            "macros": dict(macros),
            "inlineMath": [["$", "$"], ["\\(", "\\)"]],
            "displayMath": [["$$", "$$"], ["\\[", "\\]"]],
        },
        "svg": {"fontCache": "global"},
    }
    return (
        "  <script>\n"
        f"    window.MathJax = {_script_json(mathjax_configuration)};\n"
        "  </script>\n"
        f"  <script>{_mathjax_runtime()}</script>"
    )


def render_dependency_head(document: Mapping[str, Any]) -> str:
    """Render the declared dependency stack using only registered trusted loaders."""
    dependencies = document.get("renderer_dependencies", [])
    if not isinstance(dependencies, list):
        raise ValueError("renderer_dependencies must be a list.")
    rendered: list[str] = []
    seen: set[str] = set()
    for index, dependency in enumerate(dependencies):
        if not isinstance(dependency, Mapping):
            raise ValueError(f"renderer_dependencies[{index}] must be an object.")
        dependency_id = dependency.get("id")
        if not isinstance(dependency_id, str) or not dependency_id:
            raise ValueError(f"renderer_dependencies[{index}].id must be a string.")
        if dependency_id in seen:
            raise ValueError(f"duplicate renderer dependency: {dependency_id}")
        seen.add(dependency_id)
        if dependency_id == "mathjax":
            rendered.append(_mathjax_head(dependency))
            continue
        raise ValueError(f"unknown renderer dependency: {html.escape(dependency_id)}")
    return "\n".join(rendered)


__all__ = ["render_dependency_head"]
