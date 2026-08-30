"""Reject private runtime implementation references in skill-facing Markdown."""
from __future__ import annotations

import re
import sys
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import NamedTuple

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
for import_root in (REPO_ROOT, SRC_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from officina.blueprints.graph import (  # noqa: E402
    BlueprintGraphError,
    load_module_blueprint,
)

from validators.skill_runtime_files import (
    ALLOWED_RTX_SUFFIXES,
    EXEMPT_RTX_DIRNAMES,
    EXEMPT_RTX_FILENAMES,
    RTX_DIR_NAME,
    _registered_child_artifact,
)
from validators.skill_md_body import hand_authored_skill_body

_EXCLUDED_PARTS = {"tests", "assets", "_build", ".system", RTX_DIR_NAME}
REQUIRES_BLUEPRINT_GRAPH = True
BLUEPRINT_GRAPH_OPTIONAL = True
_WORD = r"A-Za-z0-9_"
_SUFFIX_ALT = "|".join(re.escape(s) for s in sorted(ALLOWED_RTX_SUFFIXES))
_OLD_RUNTIME_PATH_RE = re.compile(
    rf"(?<!/)scripts/[\w.-]+(?:{_SUFFIX_ALT})(?![{_WORD}])",
    re.IGNORECASE,
)
_PUBLIC_INTERFACE_NAME_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_MODULE_ID = r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*(?:\.[a-z_][a-z0-9_-]*)*"
_CANONICAL_INTERFACE_RE = re.compile(
    rf"\b{_MODULE_ID}\."
    r"(?:"
    r"interface\.[a-z][a-z0-9]*(?:-[a-z0-9]+)*"
    r"|source\.[a-z][a-z0-9]*(?:-[a-z0-9]+)*"
    r"\.interface\.[a-z][a-z0-9]*(?:-[a-z0-9]+)*"
    r")(?:@[1-9][0-9]*)?\b"
)


def _is_same_skill_public_interface(skill_name: str, interface_id: object) -> bool:
    """Return whether an interface ID is canonical and owned by this skill.

    Intent
    ------
    Admit only public interface identifiers whose namespace and suffix are canonical.

    Rationale
    ---------
    Malformed or foreign export keys must not hide private runtime names.

    Pseudocode
    ----------
    - reject non-string identifiers
    - require the current skill interface prefix
    - require a canonical kebab-case public suffix

    Wraps
    -----
    - none
    """
    if not isinstance(interface_id, str):
        return False
    module_id, marker, local_name = interface_id.rpartition(".interface.")
    return (
        marker == ".interface."
        and (module_id == skill_name or module_id.startswith(f"{skill_name}."))
        and _PUBLIC_INTERFACE_NAME_RE.fullmatch(local_name) is not None
    )


def _declared_public_interface_ids(
    repo_root: Path,
    skill_dir: Path,
) -> frozenset[str]:
    """Return same-skill exports from schema-validated root and runtime blueprints.

    Intent
    ------
    Identify public interface tokens that may legitimately appear in skill prose.

    Rationale
    ---------
    Standalone validation lacks a prepared graph but must reject unvalidated exports.

    Pseudocode
    ----------
    - load and validate the skill root and optional `_rtx` module blueprints
    - reject unavailable, malformed, or non-mapping exports
    - return canonical same-skill interface identifiers

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    officina.blueprints.graph.load_module_blueprint:
      why:
        constructs: "Builds the validated module declaration used for masking."
    """
    try:
        module_roots = [skill_dir]
        if (skill_dir / "_rtx" / "blueprint.yaml").is_file():
            module_roots.append(skill_dir / "_rtx")
        modules = tuple(
            load_module_blueprint(
                repo_root,
                module_root,
                expected_schema_version=6,
            )
            for module_root in module_roots
        )
    except (BlueprintGraphError, OSError, UnicodeError, ValueError):
        return frozenset()
    return frozenset(
        interface_id
        for module in modules
        for interface_id in module.declaration.get("exports", {})
        if _is_same_skill_public_interface(skill_dir.name, interface_id)
    )


def _graph_public_interface_ids_by_skill(
    graph: object,
    skill_names: frozenset[str],
) -> dict[str, frozenset[str]] | None:
    """Index same-skill IDs from a validated graph, or None for legacy fakes.

    Intent
    ------
    Reuse public exports already available in the consolidated repository graph.

    Rationale
    ---------
    Pooled validation must not reload module blueprints after graph preparation.

    Pseudocode
    ----------
    - return legacy sentinel when graph exports are unavailable
    - index exports once by their top-level owning skill
    - retain only canonical same-skill interface identifiers

    Wraps
    -----
    - none
    """
    exports = getattr(graph, "exports", None)
    if not isinstance(exports, Mapping):
        return None
    interface_ids: dict[str, set[str]] = {name: set() for name in skill_names}
    for interface_id, export in exports.items():
        module_node_id = getattr(export, "module_node_id", None)
        if not isinstance(module_node_id, str):
            continue
        skill_name = module_node_id.partition(".")[0]
        if skill_name not in skill_names:
            continue
        if _is_same_skill_public_interface(skill_name, interface_id):
            interface_ids[skill_name].add(interface_id)
    return {
        skill_name: frozenset(ids)
        for skill_name, ids in interface_ids.items()
    }


def _mask_declared_public_interfaces(
    line: str,
    interface_ids: frozenset[str],
) -> str:
    """Hide verified public tokens before normalized runtime-stem scanning.

    Intent
    ------
    Prevent a canonical public interface name from matching its private implementation stem.

    Rationale
    ---------
    Public and private names can share words while only the full public token is allowed.

    Pseudocode
    ----------
    - process longest identifiers first
    - replace boundary-delimited public tokens with equal-width spaces
    - return text retaining all adjacent nonpublic content

    Wraps
    -----
    - none
    """
    for interface_id in sorted(interface_ids, key=len, reverse=True):
        line = re.sub(
            rf"(?<![A-Za-z0-9_.-]){re.escape(interface_id)}(?![A-Za-z0-9_.-])",
            " " * len(interface_id),
            line,
        )
    return line

def _iter_skill_markdown(repo_root: Path, skill_dirs: tuple[Path, ...] | None = None):
    """Yield eligible skill Markdown paths in stable repository order.

    Intent
    ------
    Discover public Markdown content with repository-relative identities.

    Rationale
    ---------
    Runtime implementation directories and nonpublic content must remain excluded.

    Pseudocode
    ----------
    - for skill in sorted skill directories:
      - for path in sorted Markdown descendants:
        - if path is eligible:
          - return path and relative path

    Wraps
    -----
    - none
    """
    if skill_dirs is None:
        skills_root = repo_root / "skills"
        if not skills_root.is_dir():
            return
        skill_dirs = tuple(
            path
            for path in sorted(skills_root.iterdir())
            if path.is_dir() and path.name != ".system"
        )
    for skill_dir in skill_dirs:
        for md_path in sorted(skill_dir.rglob("*.md")):
            rel_path = PurePosixPath(md_path.relative_to(repo_root).as_posix())
            if any(part in _EXCLUDED_PARTS for part in rel_path.parts):
                continue
            yield md_path, rel_path


def _runtime_stems_for_skill(
    skill_dir: Path,
    graph: object | None,
) -> list[str]:
    """Return private runtime stems owned directly by one skill.

    Intent
    ------
    Inventory runtime names that public Markdown must not expose.

    Rationale
    ---------
    Registered child artifacts and conventional support files are separate nodes or exemptions.

    Pseudocode
    ----------
    - for path in sorted runtime descendants:
      - if path is an eligible direct runtime artifact:
        - set stems = stems plus directory name or file stem
    - return sorted stems

    Wraps
    -----
    - none
    """
    rtx_dir = skill_dir / RTX_DIR_NAME
    if not rtx_dir.is_dir():
        return []
    stems: set[str] = set()
    for path in sorted(rtx_dir.rglob("*")):
        if _registered_child_artifact(path, graph):
            continue
        if path.is_dir():
            if path.name in EXEMPT_RTX_DIRNAMES:
                continue
            stems.add(path.name)
            continue
        if not path.is_file() or path.suffix not in ALLOWED_RTX_SUFFIXES:
            continue
        if path.name in EXEMPT_RTX_FILENAMES:
            continue
        stems.add(path.stem)
    return sorted(stems)


def _stem_patterns(stem: str) -> list[re.Pattern[str]]:
    """Build normalized private-name matchers for one runtime stem.

    Intent
    ------
    Match private, public, spaced, and hyphenated forms of multiword names.

    Rationale
    ---------
    Public prose can leak an implementation name without using its exact filename spelling.

    Pseudocode
    ----------
    - set words = nonempty public stem components
    - if fewer than two words:
      - return no patterns
    - return four boundary-aware case-insensitive patterns

    Wraps
    -----
    - none
    """
    public_stem = stem.lstrip("_")
    words = [word for word in public_stem.split("_") if word]
    if len(words) < 2:
        return []
    underscore = re.escape(public_stem)
    private_underscore = re.escape(stem)
    spaced = r"\s+".join(re.escape(word) for word in words)
    hyphenated = "-".join(re.escape(word) for word in words)
    return [
        re.compile(rf"(?<![{_WORD}]){private_underscore}(?![{_WORD}])", re.IGNORECASE),
        re.compile(rf"(?<![{_WORD}]){underscore}(?![{_WORD}])", re.IGNORECASE),
        re.compile(rf"(?<![{_WORD}]){spaced}(?![{_WORD}])", re.IGNORECASE),
        re.compile(rf"(?<![{_WORD}]){hyphenated}(?![{_WORD}])", re.IGNORECASE),
    ]


def _public_markdown_text(path: Path, text: str) -> str:
    """Return hand-authored public text for runtime-leak scanning.

    Intent
    ------
    Exclude generated skill-document sections while retaining other Markdown verbatim.

    Rationale
    ---------
    Generated sections describe machinery and are validated through their own source.

    Pseudocode
    ----------
    - if path is not a skill entry document:
      - return original text
    - return hand-authored skill body

    Wraps
    -----
    - none
    """
    if path.name != "SKILL.md":
        return text
    return hand_authored_skill_body(text)


def _suffix_patterns_for_stem(stem: str) -> list[re.Pattern[str]]:
    """Build runtime-filename matchers for one private stem.

    Intent
    ------
    Match private and public stem spellings followed by an allowed runtime suffix.

    Rationale
    ---------
    Explicit runtime filenames reveal private implementation artifacts.

    Pseudocode
    ----------
    - set public = stem without leading underscores
    - return private and public suffix-aware patterns

    Wraps
    -----
    - none
    """
    public_stem = re.escape(stem.lstrip("_"))
    private_stem = re.escape(stem)
    return [
        re.compile(rf"(?<![{_WORD}]){private_stem}(?:{_SUFFIX_ALT})(?![{_WORD}])", re.IGNORECASE),
        re.compile(rf"(?<![{_WORD}]){public_stem}(?:{_SUFFIX_ALT})(?![{_WORD}])", re.IGNORECASE),
    ]


class _CombinedSkillPatterns(NamedTuple):
    """Combined runtime matchers and their stable diagnostic metadata."""

    suffix_pattern: re.Pattern[str] | None
    suffix_groups: dict[str, tuple[tuple[str, int], ...]]
    stem_pattern: re.Pattern[str] | None
    stem_groups: dict[str, tuple[tuple[int, str], ...]]
    stem_order: tuple[str, ...]


def _combined_patterns_for_stems(stems: list[str]) -> _CombinedSkillPatterns:
    """Compile one suffix scan and one stable-priority stem scan per skill."""
    suffix_sources: dict[str, str] = {}
    suffix_metadata: dict[str, list[tuple[str, int]]] = {}
    stem_sources: dict[str, list[tuple[int, str]]] = {}
    for stem_index, stem in enumerate(stems):
        for variant_index, pattern in enumerate(_suffix_patterns_for_stem(stem)):
            source_key = pattern.pattern.casefold()
            suffix_sources.setdefault(source_key, pattern.pattern)
            suffix_metadata.setdefault(source_key, []).append(
                (stem, variant_index)
            )
        for pattern in _stem_patterns(stem):
            stem_sources.setdefault(pattern.pattern, []).append((stem_index, stem))
    suffix_groups = {
        f"suffix_{index}": tuple(metadata)
        for index, metadata in enumerate(suffix_metadata.values())
    }
    suffix_alternatives = [
        f"(?P<{group}>{source})"
        for group, source in zip(
            suffix_groups, suffix_sources.values(), strict=True
        )
    ]
    stem_groups = {
        f"stem_{index}": tuple(metadata)
        for index, metadata in enumerate(stem_sources.values())
    }
    stem_alternatives = [
        f"(?P<{group}>{source})"
        for group, source in zip(stem_groups, stem_sources, strict=True)
    ]
    suffix_pattern = (
        re.compile("|".join(suffix_alternatives), re.IGNORECASE)
        if suffix_alternatives
        else None
    )
    stem_pattern = (
        re.compile(r"(?=(?:" + "|".join(stem_alternatives) + r"))", re.IGNORECASE)
        if stem_alternatives
        else None
    )
    return _CombinedSkillPatterns(
        suffix_pattern=suffix_pattern,
        suffix_groups=suffix_groups,
        stem_pattern=stem_pattern,
        stem_groups=stem_groups,
        stem_order=tuple(stems),
    )


def _suffix_findings(
    patterns: _CombinedSkillPatterns,
    line: str,
) -> list[tuple[str, str]]:
    """Return one token per stem in stem and private-before-public order."""
    if patterns.suffix_pattern is None:
        return []
    matches: dict[str, dict[int, str]] = {}
    for match in patterns.suffix_pattern.finditer(line):
        group = match.lastgroup
        if group is None:
            continue
        for stem, variant_index in patterns.suffix_groups[group]:
            matches.setdefault(stem, {}).setdefault(
                variant_index, match.group(group)
            )
    return [
        (stem, variants[min(variants)])
        for stem in patterns.stem_order
        if (variants := matches.get(stem))
    ]


def _first_stem_finding(
    patterns: _CombinedSkillPatterns,
    line: str,
) -> str | None:
    """Return the first matching stem in stable inventory order."""
    if patterns.stem_pattern is None:
        return None
    best: tuple[int, str] | None = None
    for match in patterns.stem_pattern.finditer(line):
        group = match.lastgroup
        if group is None:
            continue
        candidate = min(patterns.stem_groups[group])
        if best is None or candidate < best:
            best = candidate
    return best[1] if best is not None else None


def _validate(repo_root: Path, graph: object | None) -> list[str]:
    """Return private runtime references found in public skill Markdown.

    Intent
    ------
    Scan eligible Markdown with one lazily prepared pattern table per skill.

    Rationale
    ---------
    Pattern reuse removes file- and line-level preparation without changing finding order.

    Pseudocode
    ----------
    - set stems = runtime stems grouped by skill
    - for path in eligible skill Markdown:
      - if skill patterns are not prepared:
        - set patterns = matchers for skill runtime stems
      - for line in public text:
        - set findings = findings plus ordered runtime-reference matches
    - return findings

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._iter_skill_markdown:
      why:
        computes: "Provides public Markdown files in stable order."
    ._stem_patterns:
      why:
        computes: "Provides normalized private-name matchers."
    ._public_markdown_text:
      why:
        computes: "Provides the hand-authored text scanned for each document."

    InstantiationsFromRepo
    ----------------------
    ._runtime_stems_for_skill:
      why:
        constructs: "Builds each skill runtime-name inventory."
    ._suffix_patterns_for_stem:
      why:
        constructs: "Builds runtime-filename matchers."
    """
    errors: list[str] = []
    skills_root = repo_root / "skills"
    if not skills_root.is_dir():
        return errors

    skill_dirs = tuple(
        skill_dir
        for skill_dir in sorted(skills_root.iterdir())
        if skill_dir.is_dir() and skill_dir.name != ".system"
    )
    stems_by_skill = {
        skill_dir.name: _runtime_stems_for_skill(skill_dir, graph)
        for skill_dir in skill_dirs
    }
    graph_ids_by_skill = (
        _graph_public_interface_ids_by_skill(graph, frozenset(stems_by_skill))
        if graph is not None
        else None
    )
    public_interfaces_by_skill = {
        skill_dir.name: (
            graph_ids_by_skill[skill_dir.name]
            if graph_ids_by_skill is not None
            else _declared_public_interface_ids(repo_root, skill_dir)
        )
        for skill_dir in skill_dirs
    }
    patterns_by_skill: dict[str, _CombinedSkillPatterns] = {}

    for path, rel_path in _iter_skill_markdown(repo_root, skill_dirs):
        skill_name = rel_path.parts[1]
        if skill_name not in patterns_by_skill:
            patterns_by_skill[skill_name] = _combined_patterns_for_stems(
                stems_by_skill.get(skill_name, [])
            )
        patterns = patterns_by_skill[skill_name]
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        lines = _public_markdown_text(path, text).splitlines()
        for lineno, line in enumerate(lines, start=1):
            prose_without_interface_ids = _CANONICAL_INTERFACE_RE.sub("", line)
            stem_scan_line = _mask_declared_public_interfaces(
                line,
                public_interfaces_by_skill.get(skill_name, frozenset()),
            )
            if RTX_DIR_NAME in prose_without_interface_ids:
                errors.append(f"{rel_path}:{lineno}: skill-facing Markdown must not mention `{RTX_DIR_NAME}`")
            old_path = _OLD_RUNTIME_PATH_RE.search(line)
            if old_path:
                errors.append(
                    f"{rel_path}:{lineno}: skill-facing Markdown must not mention old runtime path "
                    f"`{old_path.group(0)}`"
                )
            for _stem, token in _suffix_findings(patterns, line):
                errors.append(
                    f"{rel_path}:{lineno}: skill-facing Markdown must not mention runtime file "
                    f"`{token}`"
                )
            stem = _first_stem_finding(patterns, stem_scan_line)
            if stem is not None:
                errors.append(
                    f"{rel_path}:{lineno}: skill-facing Markdown must not mention private runtime "
                    f"name `{stem}`"
                )

    return errors


def validate_with_graph(repo_root: Path, graph: object) -> list[str]:
    """Validate runtime references with a prepared repository graph.

    Intent
    ------
    Reuse graph ownership data supplied by the consolidated validator suite.

    Rationale
    ---------
    Suite execution should not reconstruct canonical repository topology.

    Pseudocode
    ----------
    - return runtime-reference findings using supplied graph

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._validate:
      why:
        constructs: "Builds runtime-reference findings."
    """
    return _validate(repo_root, graph)


def validate(repo_root: Path) -> list[str]:
    """Validate runtime references without graph ownership exclusions.

    Intent
    ------
    Preserve the standalone compatibility entry point.

    Rationale
    ---------
    Direct validation remains usable when no prepared graph is available.

    Pseudocode
    ----------
    - return runtime-reference findings without graph

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._validate:
      why:
        constructs: "Builds standalone runtime-reference findings."
    """
    return _validate(repo_root, None)


def main() -> int:
    """Run standalone runtime-reference validation and print findings.

    Intent
    ------
    Expose conventional command-line output and status.

    Rationale
    ---------
    Maintainer automation can invoke the validator outside pytest.

    Pseudocode
    ----------
    - set findings = repository runtime-reference validation
    - if findings exist:
      - return failure status
    - return success status

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .validate:
      why:
        constructs: "Builds findings printed by the command-line boundary."
    """
    repo_root = Path(__file__).resolve().parents[1]
    errors = validate(repo_root)
    if errors:
        print("Skill runtime Markdown reference violations found:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
