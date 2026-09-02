#!/usr/bin/env python3
"""Finalize semantic extraction into one validated, detachable graph artifact."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Iterable

import jsonschema

from officina.runtime.python_machine_interface import PythonArgvMachineInterface

try:
    from ._tex_macro_reader import (
        MacroDefinition,
        MacroValue,
        _extract_renderable_macro_definitions,
        macro_body_text,
    )
except ImportError:  # pragma: no cover - direct script execution
    from _tex_macro_reader import (  # type: ignore[no-redef]
        MacroDefinition,
        MacroValue,
        _extract_renderable_macro_definitions,
        macro_body_text,
    )


_RTX_DIR = Path(__file__).resolve().parent
_PRESENTATION_BASE_PATH = _RTX_DIR.parent / "resources" / "graph-base.json"
_SCHEMA_PATH = _RTX_DIR.parents[2] / "src" / "officina" / "visualization" / "graph_specification.schema.json"
_PRESENTATION_BASE = json.loads(_PRESENTATION_BASE_PATH.read_text(encoding="utf-8"))
_GRAPH_SCHEMA = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
_GRAPH_VALIDATOR = jsonschema.Draft7Validator(_GRAPH_SCHEMA)

LABEL_REFERENCE_RE = re.compile(r"\\(ref|eqref|cref|Cref|autoref)\{([^{}]+)\}")
_NON_RENDERED_PATH_KEYS = {"source_entrypoint", "source_file"}


def apply_label_numbering(
    doc: dict,
    labels: dict[str, dict],
) -> tuple[dict, int]:
    """Return a copy whose labelled entities carry TeX-resolved numbers.

    Intent
    ------
    Add compiled TeX numbers only to entities that do not already carry a reference.

    Rationale
    ---------
    Copying the draft keeps presentation enrichment pure and preserves explicit refs.

    Pseudocode
    ----------
    - set numbered_graph = copy of draft graph
    - for entity in numbered_graph entities:
      - if entity has no ref and its label has a compiled ref:
        - set entity_ref = compiled ref
    - return numbered_graph and replacement count

    Wraps
    -----
    - none
    """
    result = deepcopy(doc)
    numbered = 0
    for entity in result.get("entities", []):
        if entity.get("ref"):
            continue
        entry = labels.get(str(entity.get("tex_label") or ""))
        if entry and entry.get("ref"):
            entity["ref"] = entry["ref"]
            numbered += 1
    return result, numbered


def resolve_label_references(
    doc: dict,
    labels: dict[str, dict],
) -> tuple[dict, int]:
    """Return a copy with graph-visible TeX label references resolved.

    Intent
    ------
    Substitute known compiled label numbers through the entity payload.

    Rationale
    ---------
    Unresolved source labels stay visible while known references match the document.

    Pseudocode
    ----------
    - set resolved_graph = copy of draft graph
    - for entity_text in resolved_graph entities:
      - set entity_text = known label substitutions
    - return resolved_graph and substitution count

    Wraps
    -----
    - none
    """
    replaced = 0

    def substitute(text: str) -> str:
        """Substitute every resolvable label command in one string.

        Intent
        ------
        Apply the label-command pattern while retaining unresolved commands.

        Rationale
        ---------
        One string-level helper keeps recursive traversal independent of TeX syntax.

        Pseudocode
        ----------
        - set substituted_text = label pattern substitutions in text
        - return substituted_text

        Wraps
        -----
        - none
        """
        nonlocal replaced

        def swap(match: re.Match[str]) -> str:
            """Resolve one matched label command when its compiled number exists.

            Intent
            ------
            Convert one label match into its displayed number.

            Rationale
            ---------
            Missing entries must preserve their original source spelling.

            Pseudocode
            ----------
            - if matched label has no compiled ref:
              - return original command
            - return formatted compiled ref

            Wraps
            -----
            - none
            """
            nonlocal replaced
            entry = labels.get(match.group(2))
            if not entry or not entry.get("ref"):
                return match.group(0)
            replaced += 1
            number = str(entry["ref"])
            return f"({number})" if match.group(1) == "eqref" else number

        return LABEL_REFERENCE_RE.sub(swap, text)

    def walk(node: object) -> object:
        """Recursively transform strings while preserving container shapes.

        Intent
        ------
        Traverse the graph entity subtree and route each string to substitution.

        Rationale
        ---------
        Entity text can appear at arbitrary list and object nesting depths.

        Pseudocode
        ----------
        - if node is text:
          - return substituted text
        - if node is a container:
          - return recursively transformed container
        - return node

        Wraps
        -----
        - none
        """
        if isinstance(node, str):
            return substitute(node)
        if isinstance(node, list):
            return [walk(item) for item in node]
        if isinstance(node, dict):
            return {key: walk(value) for key, value in node.items()}
        return node

    result = deepcopy(doc)
    result["entities"] = walk(result.get("entities", []))
    return result, replaced


def apply_presentation_base(doc: dict) -> dict:
    """Return a copy with the skill's closed edge presentation catalog merged.

    Intent
    ------
    Enrich a draft with the canonical graph presentation and edge vocabulary.

    Rationale
    ---------
    A closed vocabulary prevents silently unstyled dependency relations.

    Pseudocode
    ----------
    - set presented_graph = copy of draft graph
    - if any edge type is outside the presentation catalog:
      - raise actionable vocabulary error
    - set presentation_fields = missing canonical base defaults
    - set presented_graph = presented_graph with presentation_fields
    - return presented_graph

    Wraps
    -----
    - none
    """
    result = deepcopy(doc)
    base = deepcopy(_PRESENTATION_BASE)
    allowed = {category["id"] for category in base["edge_categories"]}
    offenders: dict[str, int] = {}
    for entity in result.get("entities", []):
        for edge in entity.get("connects_to", []) or []:
            edge_type = str(edge.get("type", ""))
            if edge_type not in allowed:
                offenders[edge_type] = offenders.get(edge_type, 0) + 1
    if offenders:
        listed = ", ".join(
            f"{name!r} ({count})" for name, count in sorted(offenders.items())
        )
        raise ValueError(
            f"Edge types outside the graph vocabulary {sorted(allowed)}: {listed}. "
            "Record dependency character in edge description or metadata, not a new type."
        )

    if "categories" in base:
        result.setdefault("categories", base["categories"])
    result.setdefault("edge_categories", base["edge_categories"])
    ui = result.setdefault("ui", {})
    ui.setdefault("edge_styles", base["ui"]["edge_styles"])
    if "edge_presentation" in base["ui"]:
        ui.setdefault("edge_presentation", base["ui"]["edge_presentation"])
    if "relation_semantics" in base:
        result.setdefault("relation_semantics", base["relation_semantics"])
    return result


def _graph_visible_strings(payload: dict) -> Iterable[str]:
    """Yield strings that may be rendered as graph-visible TeX.

    Intent
    ------
    Expose renderable text while excluding dependency metadata and source paths.

    Rationale
    ---------
    Macro reachability should follow displayed content, not configuration strings.

    Pseudocode
    ----------
    - set visible_strings = recursively selected render-visible payload strings
    - return visible_strings

    Wraps
    -----
    - none
    """

    def walk(node: object) -> Iterable[str]:
        """Walk one payload subtree and yield eligible strings.

        Intent
        ------
        Preserve recursive traversal across graph lists and objects.

        Rationale
        ---------
        Renderable strings can be nested below multiple schema containers.

    Pseudocode
    ----------
    - set descendant_strings = eligible strings beneath node
    - return descendant_strings

        Wraps
        -----
        - none
        """
        if isinstance(node, str):
            yield node
        elif isinstance(node, list):
            for item in node:
                yield from walk(item)
        elif isinstance(node, dict):
            for key, value in node.items():
                if key == "renderer_dependencies" or key in _NON_RENDERED_PATH_KEYS:
                    continue
                yield from walk(value)

    yield from walk(payload)


def _normalize_macro_value(value: object, *, context: str) -> MacroValue:
    """Normalize legacy and native macro tuples to MathJax-native order.

    Intent
    ------
    Canonicalize a macro value and reject malformed arity or optional defaults.

    Rationale
    ---------
    Semantic comparison requires equivalent legacy and native tuples to match.

    Pseudocode
    ----------
    - if macro value is text:
      - return macro value
    - if macro tuple has a supported ordering and arity:
      - return replacement-first tuple
    - raise contextual macro-definition error

    Wraps
    -----
    - none
    """
    if isinstance(value, str):
        return value
    if not isinstance(value, list) or len(value) not in {2, 3}:
        raise ValueError(
            f"Invalid MathJax macro definition at {context}: expected a string or "
            "a two/three-item parameter tuple."
        )
    first, second = value[:2]
    if isinstance(first, str) and isinstance(second, int) and not isinstance(second, bool):
        replacement, argc = first, second
    elif isinstance(first, int) and not isinstance(first, bool) and isinstance(second, str):
        replacement, argc = second, first
    else:
        raise ValueError(
            f"Invalid MathJax macro tuple at {context}: expected [replacement, argc] "
            "or legacy [argc, replacement]."
        )
    if not 0 <= argc <= 9:
        raise ValueError(f"Invalid MathJax macro arity at {context}: {argc!r}")
    normalized: list[object] = [replacement, argc]
    if len(value) == 3:
        if not isinstance(value[2], str):
            raise ValueError(
                f"Invalid MathJax optional default at {context}: expected a string."
            )
        normalized.append(value[2])
    return normalized


def _merge_mathjax_macros(
    payload: dict,
    *,
    extracted: dict[str, MacroDefinition],
    draft_path: Path,
) -> dict:
    """Merge macros once, with semantic tuple comparison and source diagnostics.

    Intent
    ------
    Produce exactly one MathJax dependency containing canonical macro definitions.

    Rationale
    ---------
    Local merge logic must diagnose duplicate dependencies and genuine conflicts.

    InstantiationsFromRepo
    ----------------------
      ._normalize_macro_value:
        why:
          transforms: "Canonicalizes embedded and extracted definitions for comparison."

    Pseudocode
    ----------
    - set merged_graph = copy of draft graph
    - if multiple MathJax dependencies exist:
      - raise duplicate dependency error
    - normalized_macros = _normalize_macro_value(embedded and extracted definitions)
    - if a normalized definition conflicts:
      - raise source-located conflict error
    - set mathjax_macros = normalized_macros
    - set merged_graph = merged_graph with mathjax_macros
    - return merged_graph

    Wraps
    -----
    - none
    """
    result = deepcopy(payload)
    dependencies = result.setdefault("renderer_dependencies", [])
    if not isinstance(dependencies, list):
        raise ValueError(
            f"renderer_dependencies in {draft_path} must be a list before macro merge."
        )
    matches = [
        index
        for index, dependency in enumerate(dependencies)
        if isinstance(dependency, dict) and dependency.get("id") == "mathjax"
    ]
    if len(matches) > 1:
        indexed = ", ".join(f"renderer_dependencies[{index}]" for index in matches)
        raise ValueError(
            f"Duplicate MathJax renderer dependencies in {draft_path}: {indexed}. "
            "Keep exactly one id: mathjax entry."
        )
    if matches:
        mathjax = dependencies[matches[0]]
    else:
        mathjax = {"id": "mathjax", "version": "3", "configuration": {}}
        dependencies.append(mathjax)

    mathjax.setdefault("version", "3")
    configuration = mathjax.setdefault("configuration", {})
    if not isinstance(configuration, dict):
        raise ValueError(
            f"MathJax renderer dependency configuration in {draft_path} must be an object."
        )
    embedded = configuration.get("macros", {})
    if not isinstance(embedded, dict):
        raise ValueError(f"MathJax macros in {draft_path} must be an object.")

    merged = {
        name: _normalize_macro_value(
            value,
            context=f"{draft_path}: renderer dependency macro \\{name}",
        )
        for name, value in embedded.items()
    }
    for name, definition in extracted.items():
        normalized = _normalize_macro_value(
            definition.value,
            context=f"{definition.location}: extracted macro \\{name}",
        )
        if name in merged and merged[name] != normalized:
            raise ValueError(
                f"MathJax macro conflict for \\{name}: embedded definition in "
                f"{draft_path} is {merged[name]!r}, but extracted definition at "
                f"{definition.location} is {normalized!r}."
            )
        merged[name] = normalized

    configuration.update({"input": "tex", "output": "svg", "macros": merged})
    return result


def _validate_payload(payload: dict, *, draft_path: Path) -> None:
    """Validate the finalized graph and report the first stable schema error.

    Intent
    ------
    Enforce the existing draft-07 graph schema before any output replacement.

    Rationale
    ---------
    Stable paths and messages make invalid semantic drafts actionable.

    Pseudocode
    ----------
    - set validation_errors = schema errors sorted by payload path
    - if validation_errors exist:
      - raise first contextual schema error

    Wraps
    -----
    - none
    """
    errors = sorted(
        _GRAPH_VALIDATOR.iter_errors(payload),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if not errors:
        return
    error = errors[0]
    location = ".".join(str(part) for part in error.absolute_path) or "<root>"
    raise ValueError(
        f"Finalized graph from {draft_path} failed draft-07 schema validation at "
        f"{location}: {error.message}"
    )


def _atomic_write_json(payload: dict, output_path: Path) -> None:
    """Atomically replace one canonical JSON artifact through a sibling file.

    Intent
    ------
    Serialize validated payload bytes and replace the destination atomically.

    Rationale
    ---------
    A sibling temporary file keeps replacement on the destination filesystem.

    Pseudocode
    ----------
    - set temporary_path = new sibling temporary file
    - set synced_temporary = canonical JSON written and synced at temporary_path
    - set output_path = atomic replacement from synced_temporary
    - if temporary_path remains:
      - set cleanup_status = temporary sibling removed

    Wraps
    -----
    - none
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output_path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _read_json_object(path: Path, *, description: str) -> dict:
    """Read one required JSON object with contextual diagnostics.

    Intent
    ------
    Load a draft or label map only when it is a readable JSON object.

    Rationale
    ---------
    Early input validation separates file errors from final schema errors.

    Pseudocode
    ----------
    - if path is not a file:
      - raise missing input error
    - set parsed_object = decoded JSON at path
    - if parsed_object is not an object:
      - raise input shape error
    - return parsed_object

    Wraps
    -----
    - none
    """
    if not path.is_file():
        raise ValueError(f"{description} not found: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {description} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{description} must contain a JSON object: {path}")
    return value


def finalize_extraction(
    *,
    draft_path: Path,
    tex_entrypoint: Path,
    output_path: Path,
    label_map_path: Path | None = None,
) -> None:
    """Write a validated, self-contained canonical graph JSON artifact.

    Intent
    ------
    Finalize one semantic draft using only explicit TeX, label, and output inputs.

    Rationale
    ---------
    One boundary owns presentation, macro merge, validation, and atomic publication.

    CallsFromRepo
    -------------
      ._atomic_write_json:
        why:
          writes: "Publishes the validated canonical graph atomically."
      ._validate_payload:
        why:
          validates: "Checks the completed graph against the existing schema."

    InstantiationsFromRepo
    ----------------------
      ._merge_mathjax_macros:
        why:
          transforms: "Produces the canonical single-dependency macro payload."
      ._graph_visible_strings:
        why:
          transforms: "Selects graph text that determines the reachable macro closure."
      ._read_json_object:
        why:
          constructs: "Loads required draft and optional label-map objects."
      .apply_label_numbering:
        why:
          transforms: "Produces a copy with compiled entity numbers."
      .apply_presentation_base:
        why:
          transforms: "Produces a copy with canonical presentation metadata."
      .resolve_label_references:
        why:
          transforms: "Produces a copy with compiled references in visible text."

    Pseudocode
    ----------
    - draft_payload = _read_json_object(draft_path)
    - label_map = _read_json_object(label_map_path)
    - graph_strings = _graph_visible_strings(draft_payload)
    - set embedded_bodies = renderer macro bodies
    - set extracted_macros = closure from graph_strings and embedded_bodies
    - finalized_payload = _merge_mathjax_macros(draft_payload, extracted_macros)
    - finalized_payload = resolve_label_references(finalized_payload, label_map)
    - finalized_payload = apply_label_numbering(finalized_payload, label_map)
    - finalized_payload = apply_presentation_base(finalized_payload)
    - @._validate_payload(finalized_payload)
    - @._atomic_write_json(finalized_payload, output_path)

    Wraps
    -----
    - none
    """
    draft_path = Path(draft_path).resolve()
    tex_entrypoint = Path(tex_entrypoint).resolve()
    output_path = Path(output_path).resolve()
    payload = _read_json_object(draft_path, description="Semantic draft")
    labels: dict = {}
    if label_map_path is not None:
        labels = _read_json_object(
            Path(label_map_path).resolve(),
            description="TeX label map",
        )

    embedded_macros: dict[str, object] = {}
    dependencies = payload.get("renderer_dependencies", [])
    if isinstance(dependencies, list):
        for dependency in dependencies:
            if not isinstance(dependency, dict) or dependency.get("id") != "mathjax":
                continue
            configuration = dependency.get("configuration", {})
            if not isinstance(configuration, dict):
                continue
            candidate_macros = configuration.get("macros", {})
            if isinstance(candidate_macros, dict):
                embedded_macros.update(candidate_macros)
    graph_text = list(_graph_visible_strings(payload))
    graph_text.extend(macro_body_text(value) for value in embedded_macros.values())
    extracted = _extract_renderable_macro_definitions(
        tex_entrypoint=tex_entrypoint,
        graph_text=graph_text,
        known_commands=embedded_macros,
    )
    finalized = _merge_mathjax_macros(
        payload,
        extracted=extracted,
        draft_path=draft_path,
    )
    finalized, _ = resolve_label_references(finalized, labels)
    finalized, _ = apply_label_numbering(finalized, labels)
    finalized = apply_presentation_base(finalized)
    _validate_payload(finalized, draft_path=draft_path)
    _atomic_write_json(finalized, output_path)


def main(argv: list[str] | None = None) -> int:
    """Parse explicit finalizer arguments and report the written artifact.

    Intent
    ------
    Expose finalization through a stable explicit-path command contract.

    Rationale
    ---------
    Machine callers need structured output without implicit source discovery.

    CallsFromRepo
    -------------
      .finalize_extraction:
        why:
          orchestrates: "Runs canonical finalization for the parsed explicit paths."

    Pseudocode
    ----------
    - set parsed_paths = command arguments
    - @.finalize_extraction(parsed_paths)
    - set report = output path and merged macro count
    - return success

    Wraps
    -----
    - none
    """
    parser = argparse.ArgumentParser(
        description="Finalize semantic graph extraction into canonical JSON."
    )
    parser.add_argument("--draft", required=True, help="Semantic draft graph JSON")
    parser.add_argument(
        "--tex-entrypoint",
        required=True,
        help="Root TeX entrypoint for reachable macro extraction",
    )
    parser.add_argument("--label-map", help="Optional resolved TeX label JSON")
    parser.add_argument("--output", required=True, help="Canonical graph JSON destination")
    args = parser.parse_args(argv)

    output_path = Path(args.output).resolve()
    finalize_extraction(
        draft_path=Path(args.draft),
        tex_entrypoint=Path(args.tex_entrypoint),
        output_path=output_path,
        label_map_path=Path(args.label_map) if args.label_map else None,
    )
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    macros = next(
        dependency["configuration"]["macros"]
        for dependency in payload["renderer_dependencies"]
        if dependency.get("id") == "mathjax"
    )
    print(
        json.dumps(
            {
                "draft": str(Path(args.draft).resolve()),
                "tex_entrypoint": str(Path(args.tex_entrypoint).resolve()),
                "label_map": str(Path(args.label_map).resolve()) if args.label_map else None,
                "output": str(output_path),
                "macros": len(macros),
            },
            indent=2,
        )
    )
    return 0


class Interface(PythonArgvMachineInterface):
    """Expose canonical extraction finalization to the interface runner.

    Intent
    ------
    Bind the registered interface to the finalizer argv contract.

    Rationale
    ---------
    A small adapter preserves one implementation for direct and dispatched use.

    Pseudocode
    ----------
    - set prog = `extraction_finalizer.py`
    - return interface

    Wraps
    -----
    - none
    """

    prog = "extraction_finalizer.py"

    def run(self, argv: list[str]) -> int:
        """Delegate interface arguments to the finalizer CLI.

        Intent
        ------
        Preserve the CLI argument interpretation and exit status.

        Rationale
        ---------
        The registered boundary should not duplicate finalization logic.

        Pseudocode
        ----------
        - return @.main(argv)

        Wraps
        -----
        main -> preprocess: pass argv unchanged; postprocess: return status unchanged; fixed_arguments: none
        """
        return main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
