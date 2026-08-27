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


# MathJax implements a subset of TeX, so a command it cannot resolve renders as
# literal text with no error from any other layer. MathJax itself is the only
# reliable oracle for which commands those are, so record what it reports and
# show the reader, rather than maintaining a list of commands to expect.
_UNRESOLVED_TEX_REPORTER = """    window.__unresolvedTeX = {};
    window.MathJax.tex.formatError = function (jax, err) {
      var match = /Undefined control sequence\\s+\\\\?([A-Za-z@]+)/.exec(err.message || "");
      if (match) { window.__unresolvedTeX[match[1]] = true; }
      return jax.formatError(err);
    };
    window.MathJax.startup = Object.assign(window.MathJax.startup || {}, {
      pageReady: function () {
        return window.MathJax.startup.defaultPageReady().then(function () {
          var names = Object.keys(window.__unresolvedTeX);
          if (!names.length) { return; }
          var listed = names.map(function (n) { return "\\\\" + n; }).join(", ");
          console.warn("Unresolved TeX commands (rendered as literal text): " + listed);
          var banner = document.createElement("div");
          banner.setAttribute("data-unresolved-tex", names.join(","));
          banner.style.cssText = "position:fixed;left:12px;bottom:12px;z-index:9999;max-width:46ch;" +
            "padding:8px 10px;border:1px solid #b45309;border-radius:6px;background:#fffbeb;" +
            "color:#7c2d12;font:12px/1.45 system-ui,sans-serif;box-shadow:0 1px 4px rgba(0,0,0,.15)";
          banner.textContent = names.length + " TeX command" + (names.length > 1 ? "s" : "") +
            " could not be rendered: " + listed;
          document.body.appendChild(banner);
        });
      }
    });
"""

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
        f"{_UNRESOLVED_TEX_REPORTER}"
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
