"""Resolve graph-declared optional browser dependencies through trusted assets."""

from __future__ import annotations

import html
import json
import math
import re
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any


_VENDOR_DIRECTORY = Path(__file__).parent / "vendor"


@lru_cache(maxsize=1)
def _mathjax_runtime() -> str:
    """Load the pinned offline MathJax runtime and make it script-safe."""
    runtime = (_VENDOR_DIRECTORY / "mathjax-3.2.2-tex-svg-full.js").read_text(
        encoding="utf-8"
    )
    return re.sub(r"</script", lambda _match: r"<\/script", runtime, flags=re.IGNORECASE)


def _script_json(value: object) -> str:
    """Serialize configuration without permitting an embedded script terminator."""
    return json.dumps(value, indent=8).replace("</", "<\\/")


def _json_integer(value: object) -> int | None:
    """Normalize one Draft-07 integer-compatible JSON number.

    Intent
    ------
    Match schema integer semantics before adapting a macro argument count.

    Rationale
    ---------
    Draft-07 accepts finite integral JSON numbers such as `2.0`, while booleans,
    fractions, and nonfinite values are not integers.

    Pseudocode
    ----------
    - if value is boolean:
      - return none
    - if value is integer:
      - return value
    - if value is a finite integral float:
      - return value converted to integer
    - return none

    Wraps
    -----
    - none
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    return None


def normalize_mathjax_macros(macros: Mapping[str, object]) -> dict[str, object]:
    """Convert schema-supported macro encodings to MathJax-native form.

    Intent
    ------
    Adapt canonical and legacy JSON macro tuples at the final browser boundary.

    Rationale
    ---------
    MathJax expects replacement text before argument count, while schema v2 also
    accepts the historical integer-first order for existing payloads.

    Pseudocode
    ----------
    - set normalized = empty macro mapping
    - for macro_name in macro definitions:
      - if macro value is schema-supported:
        - set normalized_value = replacement-first macro value
      - else:
        - raise invalid macro definition error
    - return normalized

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._json_integer:
      why:
        constructs: "Builds normalized argument counts for supported tuple orders."
    """
    normalized: dict[str, object] = {}
    for name, value in macros.items():
        if isinstance(value, str):
            normalized[name] = value
            continue
        if not isinstance(value, list) or len(value) not in {2, 3}:
            raise ValueError(
                f"MathJax macro {name!r} is not a schema-supported string or tuple."
            )
        first, second = value[:2]
        first_count = _json_integer(first)
        second_count = _json_integer(second)
        if isinstance(first, str) and second_count is not None:
            replacement, argument_count = first, second_count
        elif first_count is not None and isinstance(second, str):
            replacement, argument_count = second, first_count
        else:
            raise ValueError(
                f"MathJax macro {name!r} is not a schema-supported tuple."
            )
        if not 0 <= argument_count <= 9:
            raise ValueError(
                f"MathJax macro {name!r} is not a schema-supported tuple."
            )
        native: list[object] = [replacement, argument_count]
        if len(value) == 3:
            if not isinstance(value[2], str):
                raise ValueError(
                    f"MathJax macro {name!r} is not a schema-supported optional-default tuple."
                )
            native.append(value[2])
        normalized[name] = native
    return normalized


# MathJax itself is the reliable oracle for which control sequences its pinned
# runtime cannot resolve. Disable the `noundefined` fallback that turns unknown
# commands into ordinary text, then record the runtime's real parse diagnostics
# without maintaining a command-name catalog.
_UNRESOLVED_TEX_REPORTER = """    (function () {
      var unresolved = {};
      window.__unresolvedTeX = unresolved;

      function syncUnresolvedBanner() {
        if (!document.body) { return; }
        var names = Object.keys(unresolved).sort();
        var banner = document.querySelector("[data-unresolved-tex]");
        if (!names.length) {
          if (banner) { banner.remove(); }
          return;
        }
        var listed = names.map(function (name) { return "\\\\" + name; }).join(", ");
        console.warn("Unresolved TeX commands: " + listed);
        if (!banner) {
          banner = document.createElement("div");
          banner.style.cssText = "position:fixed;left:12px;bottom:12px;z-index:9999;max-width:46ch;" +
            "padding:8px 10px;border:1px solid #b45309;border-radius:6px;background:#fffbeb;" +
            "color:#7c2d12;font:12px/1.45 system-ui,sans-serif;box-shadow:0 1px 4px rgba(0,0,0,.15)";
          document.body.appendChild(banner);
        }
        banner.setAttribute("data-unresolved-tex", names.join(","));
        banner.textContent = names.length + " TeX command" + (names.length > 1 ? "s" : "") +
          " could not be rendered: " + listed;
      }

      window.MathJax.tex.formatError = function (jax, err) {
        var match = /Undefined control sequence\\s+\\\\?([A-Za-z@]+)/.exec(err.message || "");
        if (match) {
          unresolved[match[1]] = true;
          Promise.resolve().then(syncUnresolvedBanner);
        }
        return jax.formatError(err);
      };
      window.MathJax.startup = Object.assign(window.MathJax.startup || {}, {
        pageReady: function () {
          return window.MathJax.startup.defaultPageReady().then(syncUnresolvedBanner);
        }
      });
    })();
"""

def _mathjax_head(dependency: Mapping[str, Any]) -> str:
    """Return the trusted MathJax 3 TeX-to-SVG loader and graph configuration.

    InstantiationsFromRepo
    ----------------------
    .normalize_mathjax_macros:
      why:
        constructs: "Builds the MathJax-native macro map serialized into the browser configuration."
    """
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
            "macros": normalize_mathjax_macros(macros),
            "packages": {"[-]": ["noundefined"]},
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


__all__ = ["normalize_mathjax_macros", "render_dependency_head"]
