"""Reject direct cross-skill script-path reach-through for blueprint skills."""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import yaml

RUNTIME_SUFFIXES = {".py", ".sh"}
_SYS_PATH_TOKEN = re.compile(r"\bsys\s*\.\s*path\b")
_LANGUAGE_FIELD = re.compile(
    r"(?m)(?:^[ \t]*|[{,][ \t]*)[\"']?language[\"']?[ \t]*:[ \t]*"
    r"(?P<value>[^\r\n#,}]*)"
)


def _is_text_runtime_file(path: Path) -> bool:
    """Return whether a path is a readable runtime-text candidate.

    Intent
    ------
    Restrict boundary scanning to regular Python, shell, and `_cx` runtime files.

    Rationale
    ---------
    Skill documentation and data files cannot execute cross-skill path reach-through,
    so excluding them keeps the validator focused and avoids unnecessary reads.

    Pseudocode
    ----------
    - set is_runtime_file = path is regular and has a runtime suffix or `_cx` ancestor
    - return is_runtime_file

    Wraps
    -----
    - none
    """
    return path.is_file() and (path.suffix in RUNTIME_SUFFIXES or "_cx" in path.parts)


def _could_declare_python_gateway(source: str) -> bool:
    """Return whether blueprint text can declare a Python gateway.

    Intent
    ------
    Skip YAML construction when the source cannot produce the Python language enum.

    Rationale
    ---------
    Canonical declarations spell ``Python`` directly. Ambiguous language scalars
    remain in scope so YAML escapes and aliases retain their prior behavior.

    Pseudocode
    ----------
    - if source contains the canonical Python token:
      - return true
    - if source contains an escape candidate:
      - return true
    - if source contains no language key token:
      - return false
    - set language_fields = lexically recognizable language values
    - if no language field has a recognizable simple form:
      - return true
    - return whether any language value is empty, aliased, or tagged

    Wraps
    -----
    - none
    """
    if "Python" in source:
        return True
    if "\\" in source:
        return True
    if "language" not in source:
        return False
    values = [
        match.group("value").strip()
        for match in _LANGUAGE_FIELD.finditer(source)
    ]
    if not values:
        return True
    return any(
        not value
        or value.startswith(("*", "&", "!", "|", ">"))
        for value in values
    )


def _compile_direct_runtime_patterns(
    skill_names: list[str],
) -> tuple[re.Pattern[str], ...]:
    """Compile repository-wide matchers for direct private-runtime paths.

    Intent
    ------
    Prepare the three supported cross-skill path forms once for a validation scan.

    Rationale
    ---------
    One alternation over escaped skill names replaces per-line, per-skill pattern
    reconstruction while retaining exact skill-name capture for ordered findings.

    Pseudocode
    ----------
    - set escaped_names = skill names ordered by descending length then name
    - set target_pattern = named capture over escaped_names
    - set direct_patterns = compiled relative and repository-root path forms
    - return direct_patterns

    Wraps
    -----
    - none
    """
    alternatives = "|".join(
        re.escape(name)
        for name in sorted(skill_names, key=lambda name: (-len(name), name))
    )
    target = rf"(?P<skill>{alternatives})"
    return (
        re.compile(rf"(?:^|[^A-Za-z0-9_-])(?:\.\./)+{target}/_(?:rtx|cx)/"),
        re.compile(rf"(?:^|[^A-Za-z0-9_-])skills/{target}/_(?:rtx|cx)/"),
        re.compile(rf"/skills/{target}/_(?:rtx|cx)/"),
    )


def _mutates_sys_path(node: ast.AST) -> bool:
    """Return whether a syntax node mutates ``sys.path``.

    Intent
    ------
    Recognize insertion, appending, extension, and rebinding of the import path.

    Rationale
    ---------
    Every mutation form has the same effect under the gateway, which compares
    the path before and after executing a module and rejects any difference.

    Pseudocode
    ----------
    - set pending = node
    - while pending is not empty:
      - set current = next pending entry
      - if current defines a function or class:
        - continue
      - if current is a call on the import path:
        - return true
      - if current assigns the import path:
        - return true
      - set pending = pending plus child nodes of current
    - return false

    Wraps
    -----
    - none
    """
    # Deliberately does not descend into function or class bodies. The gateway
    # compares sys.path before and after executing a module, so only statements
    # that run at import time can violate it; a helper that adjusts the path
    # when called is a different question and not this rule's business.
    def _import_time_nodes(root: ast.AST):
        for child in ast.iter_child_nodes(root):
            if isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                continue
            yield child
            yield from _import_time_nodes(child)

    for descendant in [node, *_import_time_nodes(node)]:
        if isinstance(descendant, ast.Call):
            func = descendant.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr in {"insert", "append", "extend"}
                and isinstance(func.value, ast.Attribute)
                and func.value.attr == "path"
                and isinstance(func.value.value, ast.Name)
                and func.value.value.id == "sys"
            ):
                return True
        if isinstance(descendant, (ast.Assign, ast.AugAssign)):
            targets = (
                descendant.targets
                if isinstance(descendant, ast.Assign)
                else [descendant.target]
            )
            for target in targets:
                value = target.value if isinstance(target, ast.Subscript) else target
                if (
                    isinstance(value, ast.Attribute)
                    and value.attr == "path"
                    and isinstance(value.value, ast.Name)
                    and value.value.id == "sys"
                ):
                    return True
    return False


def _guards_on_package(node: ast.AST) -> bool:
    """Return whether a conditional tests ``__package__``.

    Intent
    ------
    Distinguish a standalone-mode guard from an unconditional mutation.

    Rationale
    ---------
    A file invoked both as a script and through the gateway legitimately needs
    its own directory on the path in the script case only. Guarding on
    ``__package__`` expresses exactly that, and is false under the gateway.

    Pseudocode
    ----------
    - if node is not a conditional:
      - return false
    - for descendant in conditional test:
      - if descendant names the package attribute:
        - return true
    - return false

    Wraps
    -----
    - none
    """
    if not isinstance(node, ast.If):
        return False
    return any(
        isinstance(descendant, ast.Name) and descendant.id == "__package__"
        for descendant in ast.walk(node.test)
    )


def _gateway_paths(repo_root: Path) -> set[Path]:
    """Return absolute paths of every declared Python gateway file.

    Intent
    ------
    Identify the modules the dispatcher loads through its confined loader.

    Rationale
    ---------
    The prohibition applies to gateway-reachable modules only. Ordinary scripts
    and test files are never loaded that way, so the same construct is harmless
    in them and flagging it would produce findings with no correct remedy.

    Pseudocode
    ----------
    - for blueprint_path in skill blueprint documents:
      - if blueprint text has no Python gateway token:
        - continue
      - set document = parsed blueprint mapping
      - if document declares a Python gateway:
        - set findings = findings plus resolved gateway path
    - return findings

    Wraps
    -----
    - none
    """
    paths: set[Path] = set()
    skills_root = repo_root / "skills"
    if not skills_root.exists():
        return paths
    for blueprint_path in skills_root.glob("*/**/blueprints/*.yaml"):
        try:
            source = blueprint_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if not _could_declare_python_gateway(source):
            continue
        try:
            document = yaml.safe_load(source)
        except yaml.YAMLError:
            continue
        if not isinstance(document, dict):
            continue
        gateway = document.get("gateway")
        if not isinstance(gateway, dict) or gateway.get("language") != "Python":
            continue
        declared = gateway.get("path")
        if not isinstance(declared, str):
            continue
        module_root = blueprint_path.parent.parent
        paths.add((module_root / declared).resolve())
    return paths


def validate_gateway_sys_path(repo_root: Path) -> list[str]:
    """Return unguarded import-path mutations in gateway modules.

    Intent
    ------
    Reject module-scope ``sys.path`` mutation in dispatcher-reachable modules
    unless it is guarded on ``__package__``.

    Rationale
    ---------
    The gateway snapshots ``sys.path`` around every module execution and raises
    ImportError when it differs, so an unconditional insert makes the interface
    permanently unreachable. It also silently prefers the working tree's
    officina over the pinned release the rest of the system runs. A guarded
    insert is correct and stays legal, because the guard is false under the
    gateway.

    Pseudocode
    ----------
    - for path in declared gateway modules:
      - if source has no whitespace-tolerant import-path token:
        - continue
      - set tree = parsed module
      - for statement in module body:
        - if statement mutates the import path and is not package-guarded:
          - set findings = findings plus formatted location finding
    - return findings

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._gateway_paths:
      why:
        computes: "Selects the modules the dispatcher loads."
    ._mutates_sys_path:
      why:
        computes: "Recognizes the prohibited construct."
    ._guards_on_package:
      why:
        computes: "Exempts the standalone-mode guard."
    """
    errors: list[str] = []
    for path in sorted(_gateway_paths(repo_root)):
        if not path.is_file():
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        # Keep the common token tolerant of legal spacing. Ambiguous source
        # containing both names still reaches the AST so explicit line
        # continuations and comments cannot hide a prior diagnostic.
        if not _SYS_PATH_TOKEN.search(source) and (
            "sys" not in source or "path" not in source
        ):
            continue
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            continue
        for statement in tree.body:
            if isinstance(
                statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                continue
            if not _mutates_sys_path(statement):
                continue
            if _guards_on_package(statement):
                continue
            rel = path.relative_to(repo_root).as_posix()
            errors.append(
                f"{rel}:{statement.lineno}: unguarded module-scope sys.path "
                f"mutation in a dispatcher-reachable gateway; guard it on "
                f"__package__ or remove it"
            )
    return errors


def validate(repo_root: Path) -> list[str]:
    """Return direct cross-skill private-runtime path findings.

    Intent
    ------
    Scan blueprint skills for executable text that reaches through another skill's
    `_rtx` or `_cx` boundary instead of using a declared interface.

    Rationale
    ---------
    Repository-wide matcher preparation keeps the scan proportional to runtime text
    rather than multiplying every line by every other skill and every path form.

    Pseudocode
    ----------
    - if skills root is absent:
      - return no findings
    - set direct_patterns = compiled boundary matchers for skill_names
    - for blueprint_path in blueprint skills:
      - for path in runtime files beneath blueprint_path parent:
        - set is_runtime_file = runtime eligibility of path
        - if is_runtime_file:
          - set violations = captured direct paths and guarded sys path mentions
          - set findings = findings plus first alphabetical violation per line
    - return findings

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._is_text_runtime_file:
      why:
        computes: "Selects regular executable-text candidates beneath each blueprint skill."

    InstantiationsFromRepo
    ----------------------
    ._compile_direct_runtime_patterns:
      why:
        constructs: "Builds the three matchers reused throughout the repository scan."
    """
    errors: list[str] = []
    skills_root = repo_root / "skills"
    if not skills_root.is_dir():
        return errors

    skill_names = sorted(path.name for path in skills_root.iterdir() if path.is_dir())
    blueprint_skills = sorted(skills_root.glob("*/blueprint.yaml"))
    direct_patterns = _compile_direct_runtime_patterns(skill_names)

    for blueprint_path in blueprint_skills:
        skill_dir = blueprint_path.parent
        skill_name = skill_dir.name
        other_skills = [name for name in skill_names if name != skill_name]
        script_files = [path for path in skill_dir.rglob("*") if _is_text_runtime_file(path)]

        for path in script_files:
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except UnicodeDecodeError:
                continue

            for lineno, line in enumerate(lines, start=1):
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                # Both prohibited direct-path forms contain a private runtime
                # directory token. Most runtime source lines contain neither.
                if "_rtx" not in line and "_cx" not in line:
                    continue

                direct_targets = {
                    match.group("skill")
                    for pattern in direct_patterns
                    for match in pattern.finditer(line)
                    if match.group("skill") != skill_name
                }
                sys_path_targets = (
                    {
                        other_skill
                        for other_skill in other_skills
                        if other_skill in line
                    }
                    if "skills" in line
                    and "_rtx" in line
                    and "sys.path.insert" in line
                    else set()
                )
                for other_skill in other_skills:
                    if other_skill in direct_targets:
                        rel = path.relative_to(repo_root).as_posix()
                        errors.append(
                            f"{rel}:{lineno}: direct cross-skill runtime path to "
                            f"{other_skill} is forbidden"
                        )
                        break

                    if other_skill in sys_path_targets:
                        rel = path.relative_to(repo_root).as_posix()
                        errors.append(
                            f"{rel}:{lineno}: cross-skill sys.path insertion to "
                            f"{other_skill} is forbidden"
                        )
                        break

    errors.extend(validate_gateway_sys_path(repo_root))
    return errors


def main() -> int:
    """Run boundary validation from the repository-oriented command line.

    Intent
    ------
    Convert repository boundary findings into the validator's stderr and exit-code
    protocol for direct script invocation.

    Rationale
    ---------
    Keeping CLI rendering outside `validate` preserves its deterministic list-return
    contract for pytest and consolidated repository-check callers.

    Pseudocode
    ----------
    - set errors = repository boundary findings
    - if errors are present:
      - return failure status
    - return success status

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .validate:
      why:
        constructs: "Builds the ordered findings rendered by the standalone command."
    """
    errors = validate(Path(__file__).resolve().parents[2])
    if errors:
        print("error: invalid cross-skill boundary usage.", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
