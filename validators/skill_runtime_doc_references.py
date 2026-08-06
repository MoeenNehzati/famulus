"""Reject private runtime implementation references in skill-facing Markdown.

The validator derives private names from each skill's runtime tree and scans
eligible Markdown in repository order. Generated regions are stripped only from
``SKILL.md``; other eligible Markdown is scanned in full. A shared blueprint
graph excludes registered child artifacts from the private-runtime name set.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from validators.skill_runtime_files import (
    ALLOWED_RTX_SUFFIXES,
    EXEMPT_RTX_DIRNAMES,
    EXEMPT_RTX_FILENAMES,
    RTX_DIR_NAME,
    _registered_child_artifact,
)
from validators.skill_md_body import hand_authored_skill_body

_EXCLUDED_PARTS = {"tests", "assets", ".system", RTX_DIR_NAME}
REQUIRES_BLUEPRINT_GRAPH = True
BLUEPRINT_GRAPH_OPTIONAL = True
_WORD = r"A-Za-z0-9_"
_SUFFIX_ALT = "|".join(re.escape(s) for s in sorted(ALLOWED_RTX_SUFFIXES))
_OLD_RUNTIME_PATH_RE = re.compile(
    rf"(?<!/)scripts/[\w.-]+(?:{_SUFFIX_ALT})(?![{_WORD}])",
    re.IGNORECASE,
)

def _iter_skill_markdown(repo_root: Path):
    """Yield public skill Markdown paths in deterministic repository order.

    Intent
    ------
    Traverse each non-system skill and yield Markdown outside private runtime,
    test, asset, and system-owned subtrees together with its repository path.

    Rationale
    ---------
    Explicit subtree exclusions define the public-document boundary, and sorting
    both traversal levels stabilizes findings.

    Pseudocode
    ----------
    - if skills_root is not a directory:
      - return
    - for path in sorted immediate skill entries:
      - if path is not a directory or path name is .system:
        - continue
      - for md_path in sorted recursive Markdown paths:
        - set rel_path = md_path relative to repo_root
        - if rel_path contains tests, assets, .system, or _rtx:
          - continue
        - return generator item by yielding md_path and rel_path

    Wraps
    -----
    - none

    """
    skills_root = repo_root / "skills"
    if not skills_root.is_dir():
        return
    for path in sorted(skills_root.glob("*")):
        if not path.is_dir() or path.name == ".system":
            continue
        for md_path in sorted(path.rglob("*.md")):
            rel_path = md_path.relative_to(repo_root)
            if any(part in _EXCLUDED_PARTS for part in rel_path.parts):
                continue
            yield md_path, rel_path


def _runtime_stems_for_skill(
    skill_dir: Path,
    graph: object | None,
) -> list[str]:
    """Return private runtime stems that public prose must not expose.

    Intent
    ------
    Collect directory names and eligible Python stems beneath one ``_rtx`` tree,
    excluding registered child artifacts and fixed runtime-layout exemptions.

    Rationale
    ---------
    The graph check prevents child-owned support files from becoming private-name
    evidence; fixed directory and filename exemptions apply independently.

    Pseudocode
    ----------
    - if runtime directory is absent:
      - return empty stem list
    - for path in sorted recursive runtime paths:
      - if @._registered_child_artifact(path, graph):
        - continue
      - if path is a directory:
        - if path name is not exempt:
          - set stems = stems plus path name
        - continue
      - if path is not an allowed runtime file or its filename is exempt:
        - continue
      - set stems = stems plus path stem
    - return sorted stems

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._registered_child_artifact:
      why:
        computes: "Tests each runtime path against graph-owned child artifacts before name collection."

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
    """Build normalized private-name patterns for one multiword runtime stem.

    Intent
    ------
    Recognize private, publicized, whitespace-separated, and hyphenated spellings
    of a runtime name without matching inside a larger identifier.

    Rationale
    ---------
    All four renderings share case-insensitive identifier boundaries so substrings
    inside larger identifiers remain unreported.

    Pseudocode
    ----------
    - set words = nonempty underscore-separated public stem words
    - if words has fewer than two entries:
      - return empty pattern list
    - set underscore = escaped public stem
    - set private_underscore = escaped original stem
    - set spaced = escaped words joined by regex whitespace
    - set hyphenated = escaped words joined by hyphens
    - return four case-insensitive identifier-boundary patterns

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
    """Select the public text subject to runtime-reference scanning.

    Intent
    ------
    Remove frontmatter plus generated contract and interface blocks from
    ``SKILL.md`` while preserving every character of other Markdown documents.

    Rationale
    ---------
    Generated gateway regions legitimately name machine interfaces; the filename
    guard prevents that special filtering from altering other Markdown.

    Pseudocode
    ----------
    - if path name is not SKILL.md:
      - return original text
    - return .hand_authored_skill_body(text)

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .hand_authored_skill_body:
      why:
        transforms: "Builds the filtered SKILL.md text returned to the line scanner."
    """
    if path.name != "SKILL.md":
        return text
    return hand_authored_skill_body(text)


def _suffix_patterns_for_stem(stem: str) -> list[re.Pattern[str]]:
    """Build runtime-filename patterns for private and public stem spellings.

    Intent
    ------
    Match an allowed runtime suffix appended to either the original private stem
    or its leading-underscore-free public spelling.

    Rationale
    ---------
    Filename checks remain separate from normalized-name checks because their
    diagnostics quote the matched suffix-qualified token.

    Pseudocode
    ----------
    - set public_stem = escaped stem without leading underscores
    - set private_stem = escaped original stem
    - return private and public stems plus allowed suffixes inside identifier boundaries

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


def _validate(repo_root: Path, graph: object | None) -> list[str]:
    """Scan public skill Markdown and return ordered runtime-reference findings.

    Intent
    ------
    Resolve each skill's private runtime vocabulary, inspect public text line by
    line, and report directory tokens, legacy paths, filenames, and normalized names.

    Rationale
    ---------
    Stems are computed once per skill. Sorted traversal plus the fixed four-check
    sequence makes findings stable. Invalid UTF-8 documents are skipped.

    Pseudocode
    ----------
    - set stems_by_skill = skill names mapped to ._runtime_stems_for_skill(skill_dir, graph)
    - for path in ._iter_skill_markdown(repo_root):
      - set text = path read as UTF-8
      - if path decode raises UnicodeDecodeError:
        - continue
      - lines = ._public_markdown_text(path, text).splitlines()
      - for line in lines:
        - if _rtx occurs in line:
          - set errors = errors plus _rtx diagnostic
        - if old scripts runtime path matches line:
          - set errors = errors plus old-path diagnostic
        - if a suffix-qualified runtime filename matches line:
          - set errors = errors plus file diagnostic
        - if a normalized private runtime name matches line:
          - set errors = errors plus name diagnostic
    - return errors

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._runtime_stems_for_skill:
      why:
        constructs: "Builds each skill's ordered private-runtime vocabulary stored in the lookup mapping."
    ._iter_skill_markdown:
      why:
        constructs: "Builds the ordered document stream consumed by the validation scan."
    ._stem_patterns:
      why:
        constructs: "Builds normalized private-name patterns iterated for each scanned document."
    ._suffix_patterns_for_stem:
      why:
        constructs: "Builds suffix-qualified filename patterns iterated for each runtime stem."
    ._public_markdown_text:
      why:
        transforms: "Builds the text whose lines are scanned, including SKILL.md generated-region removal."

    Raises
    ------
    OSError
        The direct ``path.read_text`` call cannot open or read selected Markdown.
    """
    errors: list[str] = []
    skills_root = repo_root / "skills"
    if not skills_root.is_dir():
        return errors

    stems_by_skill = {
        skill_dir.name: _runtime_stems_for_skill(skill_dir, graph)
        for skill_dir in sorted(skills_root.iterdir())
        if skill_dir.is_dir() and skill_dir.name != ".system"
    }

    for path, rel_path in _iter_skill_markdown(repo_root):
        skill_name = rel_path.parts[1]
        stem_patterns = [
            (stem, pattern)
            for stem in stems_by_skill.get(skill_name, [])
            for pattern in _stem_patterns(stem)
        ]
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        lines = _public_markdown_text(path, text).splitlines()
        for lineno, line in enumerate(lines, start=1):
            if RTX_DIR_NAME in line:
                errors.append(f"{rel_path}:{lineno}: skill-facing Markdown must not mention `{RTX_DIR_NAME}`")
            old_path = _OLD_RUNTIME_PATH_RE.search(line)
            if old_path:
                errors.append(
                    f"{rel_path}:{lineno}: skill-facing Markdown must not mention old runtime path "
                    f"`{old_path.group(0)}`"
                )
            for stem in stems_by_skill.get(skill_name, []):
                for suffix_pattern in _suffix_patterns_for_stem(stem):
                    suffix_match = suffix_pattern.search(line)
                    if suffix_match:
                        errors.append(
                            f"{rel_path}:{lineno}: skill-facing Markdown must not mention runtime file "
                            f"`{suffix_match.group(0)}`"
                        )
                        break
            for stem, pattern in stem_patterns:
                if pattern.search(line):
                    errors.append(
                        f"{rel_path}:{lineno}: skill-facing Markdown must not mention private runtime "
                        f"name `{stem}`"
                    )
                    break

    return errors


def validate_with_graph(repo_root: Path, graph: object) -> list[str]:
    """Validate runtime references using the runner's shared blueprint graph.

    Intent
    ------
    Preserve registered child-artifact exclusions while applying the common scan.

    Rationale
    ---------
    Forwarding the runner's isolated graph enables the registered-child-artifact
    exclusion without changing the shared scan or result protocol.

    Pseudocode
    ----------
    - return shared validation errors with graph context

    Wraps
    -----
    _validate -> preprocess: forwards repository root and graph unchanged; postprocess: returns the ordered finding list unchanged; fixed_arguments: none
    """
    return _validate(repo_root, graph)


def validate(repo_root: Path) -> list[str]:
    """Validate runtime references without registered-child-artifact exclusion.

    Intent
    ------
    Run the shared scan with no graph while retaining fixed runtime exemptions.

    Rationale
    ---------
    A null graph disables only graph-derived child-artifact recognition; exempt
    directory names, filenames, and unsupported suffix handling remain unchanged.

    Pseudocode
    ----------
    - return shared validation errors without graph context

    Wraps
    -----
    _validate -> preprocess: forwards the repository root unchanged; postprocess: returns the ordered finding list unchanged; fixed_arguments: graph is none
    """
    return _validate(repo_root, None)


def main() -> int:
    """Run the graphless validator CLI and render findings to standard error.

    Intent
    ------
    Validate the containing repository, print a heading and bullet for every
    finding, and return the conventional clean-or-violations process status.

    Rationale
    ---------
    Keeping rendering outside the list-returning validator protocol lets the root
    runner consume structured findings while direct execution remains useful.

    Pseudocode
    ----------
    - set repo_root = parent repository directory
    - errors = .validate(repo_root)
    - if errors:
      - return 1 after printing the heading and each bullet-prefixed error to sys.stderr
    - return 0

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .validate:
      why:
        constructs: "Builds the ordered finding list whose presence controls rendering and exit status."
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
