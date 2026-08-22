"""Validate that production TOML filenames cross only the TOML IO boundary."""

from __future__ import annotations

import ast
from pathlib import Path

from officina.common.python_source_cache import PythonSourceCache


_CHECK_ROOTS = ["skills", "src", "script_dispatcher", "llmhooks"]
_SKIP_PARTS = {"tests", "validators", "__pycache__", ".git", ".claude-plugin", ".codex-plugin", "logs"}
_ALLOWED_REL = Path("src/officina/common/toml_io.py")
_ALLOWED_COMMON_TOML_DIR = Path("src/officina/common")
_DIRECT_PATH_IO = {
    "open", "read_bytes", "read_text", "replace", "unlink", "write_bytes", "write_text"
}


def _iter_python_files(repo_root: Path):
    """Yield production Python files subject to the TOML boundary.

    Intent
    ------
    Traverse configured roots while excluding tests, validators, caches, and approved helpers.

    Rationale
    ---------
    Boundary findings should cover production callers without flagging the boundary itself.

    Pseudocode
    ----------
    - for root_name in configured roots:
      - for path in root Python files:
        - if path is eligible:
          - return path to the iterator

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._is_common_toml_helper:
      why:
        computes: "Excludes approved common TOML helper modules during traversal."
    """
    for root_name in _CHECK_ROOTS:
        root = repo_root / root_name
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            rel_path = path.relative_to(repo_root)
            if rel_path == _ALLOWED_REL or _is_common_toml_helper(rel_path):
                continue
            if any(part in _SKIP_PARTS for part in rel_path.parts):
                continue
            yield path


def _is_common_toml_helper(rel_path: Path) -> bool:
    """Return whether a path names an approved common TOML helper.

    Intent
    ------
    Recognize Python helpers colocated with the canonical TOML IO module.

    Rationale
    ---------
    Common boundary implementation files may legitimately contain TOML filenames.

    Pseudocode
    ----------
    - return whether the path is a Python TOML helper in the approved directory

    Wraps
    -----
    - none
    """
    return (
        rel_path.parent == _ALLOWED_COMMON_TOML_DIR
        and "toml" in rel_path.name
        and rel_path.suffix == ".py"
    )


def _contains_toml_literal(node: ast.AST) -> bool:
    """Return whether an AST node visibly contains a TOML filename fragment.

    Intent
    ------
    Detect TOML text in string constants and literal portions of f-strings.

    Rationale
    ---------
    Visible filename fragments define the syntax governed by this boundary.

    Pseudocode
    ----------
    - if node is a string constant:
      - return whether it contains the TOML suffix
    - if node is an f-string:
      - return whether any literal component contains the TOML suffix
    - return false

    Wraps
    -----
    - none
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return ".toml" in node.value
    if isinstance(node, ast.JoinedStr):
        return any(
            isinstance(value, ast.Constant)
            and isinstance(value.value, str)
            and ".toml" in value.value
            for value in node.values
        )
    return False


def _is_docstring(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    """Return whether a string node belongs to an owner docstring expression.

    Intent
    ------
    Distinguish documentation text from executable filename literals.

    Rationale
    ---------
    Documentation may describe TOML files without crossing the runtime IO boundary.

    Pseudocode
    ----------
    - set expr = enclosing expression for the string node
    - return whether the expression is the owner's first body statement

    Wraps
    -----
    - none
    """
    expr = parents.get(node)
    if isinstance(expr, ast.JoinedStr):
        expr = parents.get(expr)
    if not isinstance(expr, ast.Expr):
        return False
    owner = parents.get(expr)
    return isinstance(owner, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and owner.body and owner.body[0] is expr


def _is_toml_io_open_call(call: ast.Call) -> bool:
    """Return whether a call directly invokes ``toml_io.open``.

    Intent
    ------
    Recognize the approved boundary call by its syntactic receiver and method.

    Rationale
    ---------
    Only direct boundary calls may own production TOML filename literals.

    Pseudocode
    ----------
    - return whether the callee is the ``open`` attribute of ``toml_io``

    Wraps
    -----
    - none
    """
    func = call.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "open"
        and isinstance(func.value, ast.Name)
        and func.value.id == "toml_io"
    )


def _is_direct_open_filename_arg(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    """Return whether a node is the direct filename argument to ``toml_io.open``.

    Intent
    ------
    Accept positional and named filename arguments without following assignments.

    Rationale
    ---------
    The boundary requires filenames to remain visible at the approved call site.

    Pseudocode
    ----------
    - set direct = enclosing f-string or input node
    - if parent is not a TOML IO call:
      - return false
    - return whether node is its second positional or named filename argument

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._is_toml_io_open_call:
      why:
        computes: "Restricts accepted arguments to direct calls through the approved boundary."
    """
    direct = parents[node] if isinstance(parents.get(node), ast.JoinedStr) else node
    parent = parents.get(direct)
    if not isinstance(parent, ast.Call) or not _is_toml_io_open_call(parent):
        return False
    if len(parent.args) >= 2 and parent.args[1] is direct:
        return True
    return any(
        keyword.arg in {"name", "filename"} and keyword.value is direct
        for keyword in parent.keywords
    )


def _open_filename_arg(call: ast.Call) -> ast.AST | None:
    """Return the filename argument supplied to a ``toml_io.open`` call.

    Intent
    ------
    Read the supported positional or named filename argument shape.

    Rationale
    ---------
    Validation needs the exact syntax node for visibility and line reporting.

    Pseudocode
    ----------
    - return the second positional argument when present
    - for keyword in call keywords:
      - if keyword names the filename:
        - return keyword value
    - return none

    Wraps
    -----
    - none
    """
    if len(call.args) >= 2:
        return call.args[1]
    for keyword in call.keywords:
        if keyword.arg in {"name", "filename"}:
            return keyword.value
    return None


def _is_visible_toml_filename(node: ast.AST) -> bool:
    """Return whether a filename argument visibly identifies a TOML file.

    Intent
    ------
    Accept literal TOML filenames and f-strings with a literal TOML component.

    Rationale
    ---------
    Visible filenames make boundary ownership mechanically auditable.

    Pseudocode
    ----------
    - if node is a string constant:
      - return whether it ends with the TOML suffix
    - if node is an f-string:
      - return whether a literal component contains the TOML suffix
    - return false

    Wraps
    -----
    - none
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.endswith(".toml")
    if isinstance(node, ast.JoinedStr):
        return any(
            isinstance(value, ast.Constant)
            and isinstance(value.value, str)
            and ".toml" in value.value
            for value in node.values
        )
    return False


def _validate_file(
    path: Path,
    rel_path: Path,
    source_cache: PythonSourceCache,
) -> list[str]:
    """Return TOML boundary findings for one parsed Python file.

    Intent
    ------
    Check boundary calls and reject TOML literals outside their direct filename argument.

    Rationale
    ---------
    Per-file analysis preserves exact paths, source lines, and syntax-error diagnostics.

    Pseudocode
    ----------
    - set tree = parsed file from the shared source cache
    - set parents = child-to-parent links in tree
    - set findings = malformed boundary-call findings
    - set findings = findings plus misplaced TOML-literal findings
    - return findings

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._contains_toml_literal:
      why:
        computes: "Identifies AST nodes containing visible TOML filename fragments."

    ._is_direct_open_filename_arg:
      why:
        computes: "Accepts literals placed directly in an approved filename argument."

    ._is_visible_toml_filename:
      why:
        computes: "Checks whether each boundary filename argument remains mechanically visible."

    ._is_docstring:
      why:
        computes: "Exempts documentation strings from executable filename rules."

    ._is_toml_io_open_call:
      why:
        computes: "Selects approved boundary calls for argument validation."

    InstantiationsFromRepo
    ----------------------
    ._open_filename_arg:
      why:
        constructs: "Provides the filename syntax node inspected and reported by this validator."
    """
    try:
        _source, tree = source_cache.read_parse(path)
    except SyntaxError as exc:
        return [f"{rel_path}:{exc.lineno}: failed to parse Python: {exc.msg}"]

    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    boundary_objects: set[str] = set()
    escaped_paths: set[str] = set()
    safe_streams: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if (
                    isinstance(item.context_expr, ast.Call)
                    and _is_toml_io_open_call(item.context_expr)
                    and isinstance(item.optional_vars, ast.Name)
                ):
                    safe_streams.add(item.optional_vars.id)
        if not isinstance(node, ast.Assign) or len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        target = node.targets[0].id
        value = node.value
        if isinstance(value, ast.Call) and _is_toml_io_open_call(value):
            boundary_objects.add(target)
        if (
            isinstance(value, ast.Attribute)
            and value.attr == "path"
            and (
                (isinstance(value.value, ast.Call) and _is_toml_io_open_call(value.value))
                or (isinstance(value.value, ast.Name) and value.value.id in boundary_objects)
            )
        ):
            escaped_paths.add(target)

    errors: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if (
                isinstance(node.func.value, ast.Name)
                and node.func.value.id in escaped_paths
                and node.func.attr in _DIRECT_PATH_IO
            ):
                errors.append(
                    f"{rel_path}:{node.lineno}: escaped toml_io path must not perform direct file IO"
                )
            if (
                isinstance(node.func.value, ast.Name)
                and node.func.value.id == "tomllib"
                and node.func.attr in {"load", "loads"}
            ):
                safe = bool(
                    node.func.attr == "loads"
                    and node.args
                    and isinstance(node.args[0], ast.Call)
                    and isinstance(node.args[0].func, ast.Attribute)
                    and node.args[0].func.attr == "read"
                    and isinstance(node.args[0].func.value, ast.Name)
                    and node.args[0].func.value.id in safe_streams
                )
                if not safe:
                    errors.append(
                        f"{rel_path}:{node.lineno}: direct tomllib parsing must remain inside the shared TOML boundary"
                    )
            if (
                isinstance(node.func.value, ast.Name)
                and node.func.value.id == "re"
                and node.func.attr == "sub"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and "=" in node.args[0].value
            ):
                errors.append(
                    f"{rel_path}:{node.lineno}: direct TOML structure rewriting must remain inside the shared TOML boundary"
                )
        if isinstance(node, ast.Call) and _is_toml_io_open_call(node):
            filename_arg = _open_filename_arg(node)
            if filename_arg is None:
                errors.append(
                    f"{rel_path}:{node.lineno}: toml_io.open(...) requires a visible "
                    "literal or f-string TOML filename argument"
                )
            elif not _is_visible_toml_filename(filename_arg):
                errors.append(
                    f"{rel_path}:{getattr(filename_arg, 'lineno', node.lineno)}: "
                    "toml_io.open(...) filename must be a literal or f-string ending in .toml"
                )
        if isinstance(node, ast.Constant) and isinstance(parents.get(node), ast.JoinedStr):
            continue
        if not _contains_toml_literal(node):
            continue
        if _is_docstring(node, parents):
            continue
        if _is_direct_open_filename_arg(node, parents):
            continue
        errors.append(
            f"{rel_path}:{getattr(node, 'lineno', 1)}: TOML filenames may only appear "
            "as the direct filename argument to toml_io.open(...)"
        )
    return errors


def _validate(repo_root: Path, source_cache: PythonSourceCache) -> list[str]:
    """Return repository findings using a prepared Python source cache.

    Intent
    ------
    Apply per-file TOML boundary analysis across all eligible production files.

    Rationale
    ---------
    Cache injection lets the pytest suite reuse parsing without changing findings.

    Pseudocode
    ----------
    - for path in eligible Python files:
      - set findings = findings plus cache-backed file findings
    - return findings

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._iter_python_files:
      why:
        computes: "Supplies the production Python files scanned by the repository validator."

    InstantiationsFromRepo
    ----------------------
    ._validate_file:
      why:
        constructs: "Builds the ordered boundary findings contributed by each file."
    """
    errors: list[str] = []
    for path in _iter_python_files(repo_root):
        errors.extend(
            _validate_file(
                path,
                path.relative_to(repo_root),
                source_cache,
            )
        )
    return errors


def validate(repo_root: Path) -> list[str]:
    """Return repository findings through a fresh standalone source cache.

    Intent
    ------
    Preserve the public validator entry point for direct callers.

    Rationale
    ---------
    Standalone validation cannot depend on pytest fixture preparation.

    Pseudocode
    ----------
    - set source_cache = repository-scoped Python source cache
    - return cache-backed repository findings

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    officina.common.python_source_cache.PythonSourceCache:
      why:
        computes: "Provides standalone source reads and AST parsing for the repository."

    InstantiationsFromRepo
    ----------------------
    ._validate:
      why:
        constructs: "Builds the complete standalone finding list from the fresh cache."
    """
    return _validate(repo_root, PythonSourceCache(repo_root))


def test_toml_io_boundary(
    repo_root: Path,
    python_source_cache: PythonSourceCache,
) -> list[str]:
    """Return TOML boundary findings for the repository-check pytest item.

    Intent
    ------
    Consume the session-scoped Python source cache supplied by pytest.

    Rationale
    ---------
    Shared parsing removes repeated preparation while preserving validator semantics.

    Pseudocode
    ----------
    - return repository findings using the injected source cache

    Wraps
    -----
    - ._validate -> preprocess: forwards shared fixtures; postprocess: returns findings unchanged; fixed_arguments: none
    """
    return _validate(repo_root, python_source_cache)
