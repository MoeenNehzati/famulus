"""Validate that shared content contains no platform-specific references.

A file whose own filename names a host or operating system (case-insensitive
substring match, e.g. ``codex_parser.py`` or ``windows.py``) is allowed to
mention that platform's forbidden terms -- the filename itself is the visible
signal that this one file is intentionally platform-specific, while every
other shared file must stay generic. Filename checks are case-insensitive, and
content checks use each platform group's own pattern policy (for example,
``CLAUDE_HOME`` is treated the same as ``claude_home`` or ``Claude Code``).
``__init__.py`` is always exempt too: it is the conventional aggregation seam
that statically imports platform-specific files and re-exports a generic
collection (e.g. ``parsers = [...]``) for everything else to consume without
naming any platform itself.

This lets a module hold real per-platform logic without a blanket per-skill
exemption: put platform-specific parts in a file named after that platform
(plus the __init__.py that wires them together), keep everything else
(SKILL.md, first-party shared packages, and any generically-named script) free
of platform references. Exact generic paths listed below are binding cross-host
orchestration contracts and are narrowly allowed; adjacent generic paths remain
subject to the normal checks. Validated blueprint files are descriptive graph
artifacts rather than module content; once the canonical graph validates them,
this text guard scans their owned files instead of their declaration metadata.
Frozen version-4 blueprint fixtures retain the line-level checks below.
Generated, marker-bounded interface projections in ``SKILL.md`` are excluded;
frontmatter and hand-authored skill text remain subject to this prose policy.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_ROOT = _REPO_ROOT / "src"
for import_root in (_REPO_ROOT, _SRC_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from officina.blueprints.graph import (  # noqa: E402
    BlueprintGraphError,
    load_repository_blueprint_graph,
)
from officina.blueprints.inventory import BlueprintInventoryError  # noqa: E402
from validators.skill_md_body import (  # noqa: E402
    strip_generated_blueprint_blocks,
)

_PLATFORM_GROUPS: dict[str, tuple[set[str], re.Pattern[str]]] = {
    "claude": ({"claude"}, re.compile(r"(?i:(\.claude|claude))")),
    "codex": ({"codex"}, re.compile(r"(?i:(\.codex|codex))")),
    "linux": ({"linux"}, re.compile(r"(?i:\b(linux)\b)")),
    "osx": ({"osx", "macos", "darwin"}, re.compile(r"(?i:\b(osx|macos|darwin)\b)")),
    "windows": ({"windows", "win32"}, re.compile(r"\b(Windows|win32)\b")),
}

_ALWAYS_EXEMPT_FILENAMES = {"__init__.py"}

_CHECK_ROOTS = ["skills", "references", "agents", "CLAUDE.md", "src/officina"]
_EXCLUDED_PARTS = {"tests", "validators", ".git", ".claude-plugin", ".codex-plugin"}
_EXCLUDED_PATHS = {
    Path("references/node-standards"),
    Path("skills/install-assistant-tools"),
    Path("skills/latex-workshop"),
    Path("skills/recurring-tasks"),
}
_PLATFORM_METADATA_TOOLING_PATHS = {
    Path("skills/skill-maker/_rtx/_blueprint_syncer.py"),
    Path("src/officina/install/runtime_lock.py"),
}
_BINDING_CROSS_HOST_ORCHESTRATION_PATHS = {
    Path("skills/relocate-nodes/_rtx/_relocation_engine.py"),
    Path("src/officina/install/assistant_access.py"),
    Path("src/officina/install/context.py"),
    Path("src/officina/install/development_activation.py"),
    Path("src/officina/install/doctor.py"),
    Path("src/officina/install/runtime_pointer.py"),
    Path("src/officina/install/resolvers/launch.py"),
    Path("src/officina/launchers/agent.py"),
    Path("src/officina/recurring/runtime.py"),
    Path("src/officina/recurring/healthcheck.py"),
    Path("src/officina/recurring/jobs.py"),
    Path("src/officina/recurring/executor.py"),
    Path("src/officina/recurring/native.py"),
    Path("src/officina/recurring/state.py"),
    Path("src/officina/recurring/default_jobs.yaml"),
    Path("src/officina/configuration/schema.json"),
}
_MILESTONE_COMPATIBILITY_RUNTIME_PATHS = {
    Path("skills/milestone-logging/_rtx/_milestone_writer.py"),
    Path("skills/milestone-logging/_rtx/_agent_timeline.py"),
}
_HOST_PATTERN = re.compile(r"(?i:(\.claude|claude|\.codex|codex))")
_PLATFORM_METADATA_LINE_RE = re.compile(
    r"^\s*(?:#\s*)?[\"']?(?:linux|macos|windows)[\"']?\s*:\s*(?:true|false|\{)"
)
_PEP508_PLATFORM_MARKER_LINE_RE = re.compile(
    r";.*\bsys_platform\s*==\s*['\"](?:linux|darwin|win32)['\"]"
)
_METADATA_PLATFORM_KEYS = "platform-keys"
_METADATA_PEP508_MARKER = "pep508-marker"
_METADATA_ALL_NON_HOST_LINES = "all-non-host-lines"
_UNCLASSIFIED_METADATA = object()
REQUIRES_BLUEPRINT_GRAPH = True
BLUEPRINT_GRAPH_OPTIONAL = True


def _platform_metadata_exemption_kind(rel_path: Path) -> str | None:
    """Classify the metadata exemption available to one authored file.

    Intent
    ------
    Precompute path-dependent metadata eligibility once per file.

    Rationale
    ---------
    Ordinary shared files need no metadata regex or repeated path comparisons.

    Pseudocode
    ----------
    - return the narrow line matcher kind for structured metadata files
    - return the all-non-host-lines kind for schema/tooling definitions
    - return none for ordinary files

    Wraps
    -----
    - none
    """
    if rel_path.parts[:2] == ("references", "blueprint-schema"):
        return _METADATA_ALL_NON_HOST_LINES
    if rel_path.name.endswith("blueprint.yaml"):
        return _METADATA_PLATFORM_KEYS
    if rel_path == Path("references/blueprint-schema/runtime_dependencies.json"):
        return _METADATA_PLATFORM_KEYS
    if rel_path == Path("references/runtime/requirements-core.lock"):
        return _METADATA_PEP508_MARKER
    if rel_path in _PLATFORM_METADATA_TOOLING_PATHS:
        return _METADATA_ALL_NON_HOST_LINES
    return None


def _is_allowed_platform_metadata_line(
    rel_path: Path,
    line: str,
    *,
    exemption_kind: str | None | object = _UNCLASSIFIED_METADATA,
) -> bool:
    """Return whether one platform metadata line is allowed.

    Intent
    ------
    Exempt explicit operating-system metadata while retaining host-name checks.

    Rationale
    ---------
    Descriptive compatibility metadata is not platform-specific implementation content.

    Pseudocode
    ----------
    - classify the file when no prepared classification was supplied
    - if the file has no metadata exemption or line contains a host name:
      - return false
    - return whether the line matches the prepared exemption kind

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._platform_metadata_exemption_kind:
      why:
        computes: "Classifies callers that do not supply prepared eligibility."
    """
    if exemption_kind is _UNCLASSIFIED_METADATA:
        exemption_kind = _platform_metadata_exemption_kind(rel_path)
    if exemption_kind is None or _HOST_PATTERN.search(line):
        return False
    if exemption_kind == _METADATA_PLATFORM_KEYS:
        return _PLATFORM_METADATA_LINE_RE.search(line) is not None
    if exemption_kind == _METADATA_PEP508_MARKER:
        return _PEP508_PLATFORM_MARKER_LINE_RE.search(line) is not None
    return exemption_kind == _METADATA_ALL_NON_HOST_LINES


def _forbidden_pattern_for(path: Path) -> re.Pattern[str] | None:
    """Build the forbidden-term pattern applicable to one file.

    Intent
    ------
    Exempt platforms named by the filename and exempt package aggregators.

    Rationale
    ---------
    Explicitly platform-named modules may contain that platform's implementation.

    Pseudocode
    ----------
    - return none for always-exempt filenames
    - set patterns = platform matchers not exempted by filename
    - return joined patterns

    Wraps
    -----
    - none
    """
    name_lower = path.name.lower()
    if name_lower in _ALWAYS_EXEMPT_FILENAMES:
        return None
    active = [
        pattern
        for aliases, pattern in _PLATFORM_GROUPS.values()
        if not any(alias in name_lower for alias in aliases)
    ]
    if not active:
        return None
    return re.compile("|".join(p.pattern for p in active))


def _validated_blueprint_paths(graph: object) -> frozenset[Path]:
    """Return resolved blueprint paths represented by a prepared graph.

    Intent
    ------
    Identify declaration files whose content the canonical graph already validates.

    Rationale
    ---------
    Platform checks should inspect owned content rather than repeat declaration checks.

    Pseudocode
    ----------
    - set paths = resolved blueprint path for each graph node
    - return frozen paths

    Wraps
    -----
    - none
    """
    return frozenset(
        node.blueprint_path.resolve()
        for node in graph.nodes.values()
    )


def _canonical_blueprint_paths(repo_root: Path) -> frozenset[Path]:
    """Load paths validated by the canonical repository blueprint graph.

    Intent
    ------
    Prepare the blueprint exclusions used by standalone validation.

    Rationale
    ---------
    A graph failure must preserve the validator's historical fallback to no exclusions.

    Pseudocode
    ----------
    - set graph = repository graph loaded with schema root
    - return its resolved blueprint paths, or an empty set on supported failures

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .officina.blueprints.graph.load_repository_blueprint_graph:
      why:
        constructs: "Builds the canonical repository graph used for exclusions."
    ._validated_blueprint_paths:
      why:
        constructs: "Builds the immutable resolved exclusion set."
    """

    schema_root = repo_root / "references" / "blueprint-schema"
    try:
        graph = load_repository_blueprint_graph(
            repo_root,
            schema_root=schema_root,
        )
    except (
        BlueprintGraphError,
        BlueprintInventoryError,
        OSError,
        UnicodeError,
    ):
        return frozenset()
    return _validated_blueprint_paths(graph)


def _git_ignored_paths(repo_root: Path) -> frozenset[Path]:
    """Return the repository-relative paths git is configured to ignore.

    Intent
    ------
    Separate authored repository content from files that merely sit in the tree.

    Rationale
    ---------
    The scan walks the working tree, which also holds what git deliberately
    does not track: runtime logs, caches, and state the skills write about
    themselves. That content is not authored and not reviewable, so validating
    it reports failures nobody can fix by editing the repository. A triage log
    failed this check because an email it had processed mentioned an operating
    system, which made arbitrary incoming mail able to break the repository's
    own checks. Files that are untracked but not ignored stay in scope: a new
    source file must pass before it is added.

    Deliberately duplicated from validators/skill_runtime_files.py rather than
    imported. This validator is copied into a bare temporary directory and run
    on its own by tests/validate_platform_neutral.py, where no sibling
    validator exists to import; a shared helper makes it fail to load there.

    Pseudocode
    ----------
    - ask git for ignored, untracked files
    - on any git failure, return an empty set so the scan is unchanged

    Wraps
    -----
    - git ls-files
    """
    try:
        result = subprocess.run(
            [
                "git", "-C", str(repo_root), "ls-files",
                "--others", "--ignored", "--exclude-standard", "-z",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return frozenset()
    if result.returncode != 0:
        return frozenset()
    return frozenset(Path(entry) for entry in result.stdout.split("\0") if entry)


def _iter_files(repo_root: Path, *, excluded_blueprints: frozenset[Path]):
    """Yield each scanned file together with its prepared relative path.

    Intent
    ------
    Discover eligible shared files and compute repository-relative identity once.

    Rationale
    ---------
    Reusing the relative path avoids repeating path preparation for every content line.

    Pseudocode
    ----------
    - for root in configured roots:
      - for path in root traversal order:
        - if path is eligible:
          - return path and relative path

    Wraps
    -----
    - none
    """
    ignored = _git_ignored_paths(repo_root)
    for root_name in _CHECK_ROOTS:
        root = repo_root / root_name
        if root.is_file():
            if root.resolve() not in excluded_blueprints:
                yield root, root.relative_to(repo_root)
            continue
        if not root.is_dir():
            continue
        for child in root.rglob("*"):
            if not child.is_file():
                continue
            rel_path = child.relative_to(repo_root)
            if rel_path in ignored:
                continue
            if any(part in _EXCLUDED_PARTS for part in rel_path.parts):
                continue
            if any(rel_path == ep or ep in rel_path.parents for ep in _EXCLUDED_PATHS):
                continue
            if child.resolve() in excluded_blueprints:
                continue
            yield child, rel_path


def _validate(
    repo_root: Path,
    excluded_blueprints: frozenset[Path],
) -> list[str]:
    """Return platform-specific references found in shared content.

    Intent
    ------
    Scan eligible text lines and format every forbidden reference in traversal order.

    Rationale
    ---------
    Shared content must remain host-neutral outside explicit exemptions.

    Pseudocode
    ----------
    - for path in eligible files:
      - set pattern = file-specific matcher
      - remove the generated interface block from skill instructions
      - for line in decodable file lines:
        - if line contains a non-exempt match:
          - return formatted finding

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._iter_files:
      why:
        computes: "Provides eligible files and their reusable relative paths."
    ._is_allowed_platform_metadata_line:
      why:
        computes: "Recognizes allowed descriptive platform metadata."
    ._platform_metadata_exemption_kind:
      why:
        computes: "Preclassifies whether each file can contain exempt metadata."
    .validators.skill_md_body.strip_generated_blueprint_blocks:
      why:
        computes: "Projects SKILL.md to text owned by authors while preserving line numbers."

    InstantiationsFromRepo
    ----------------------
    ._forbidden_pattern_for:
      why:
        constructs: "Builds each file-specific forbidden-term matcher."
    """
    repo_root = repo_root.resolve()
    errors: list[str] = []
    for path, rel in _iter_files(repo_root, excluded_blueprints=excluded_blueprints):
        if (
            rel in _BINDING_CROSS_HOST_ORCHESTRATION_PATHS
            or rel in _MILESTONE_COMPATIBILITY_RUNTIME_PATHS
        ):
            continue
        pattern = _forbidden_pattern_for(path)
        if pattern is None:
            continue
        metadata_exemption = _platform_metadata_exemption_kind(rel)
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if rel.parts[:1] == ("skills",) and rel.name == "SKILL.md":
            text = strip_generated_blueprint_blocks(text)
        for lineno, line in enumerate(text.splitlines(), start=1):
            if metadata_exemption is not None and _is_allowed_platform_metadata_line(
                rel,
                line,
                exemption_kind=metadata_exemption,
            ):
                continue
            if pattern.search(line):
                errors.append(f"{rel.as_posix()}:{lineno}: {line.strip()}")
    return errors


def validate_with_graph(repo_root: Path, graph: object) -> list[str]:
    """Validate shared content using one prepared repository graph.

    Intent
    ------
    Reuse suite graph preparation while preserving platform scan behavior.

    Rationale
    ---------
    Suite execution should not rebuild repository topology per validator.

    Pseudocode
    ----------
    - set exclusions = validated paths from graph
    - return repository validation findings with exclusions

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._validated_blueprint_paths:
      why:
        computes: "Provides exclusions from the supplied graph."

    InstantiationsFromRepo
    ----------------------
    ._validate:
      why:
        constructs: "Builds the platform-neutral findings."
    """
    return _validate(repo_root, _validated_blueprint_paths(graph))


def validate(repo_root: Path) -> list[str]:
    """Validate shared content through standalone graph preparation.

    Intent
    ------
    Preserve the public direct-call entry point for platform-neutral validation.

    Rationale
    ---------
    Direct callers lack a suite-prepared graph and require compatible preparation.

    Pseudocode
    ----------
    - set exclusions = canonical blueprint paths
    - return repository validation findings with exclusions

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._canonical_blueprint_paths:
      why:
        computes: "Provides exclusions for a standalone call."

    InstantiationsFromRepo
    ----------------------
    ._validate:
      why:
        constructs: "Builds the platform-neutral findings."
    """
    return _validate(repo_root, _canonical_blueprint_paths(repo_root))


def main() -> int:
    """Run standalone platform-neutral validation and print findings.

    Intent
    ------
    Provide the validator's command-line compatibility entry point.

    Rationale
    ---------
    Existing automation relies on conventional zero and nonzero exit status.

    Pseudocode
    ----------
    - set findings = repository platform-neutral validation
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
        constructs: "Builds the findings printed by the command-line boundary."
    """
    repo_root = Path(__file__).resolve().parents[1]
    errors = validate(repo_root)
    if errors:
        print("Platform-specific references found in shared content:")
        for error in errors:
            print(f"- {error}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
