"""Close deterministic relocation artifacts in an isolated shadow repository.

The coordinator materializes the already-projected ``ChangeSet`` in a temporary
tree, delegates generated artifacts to the canonical blueprint synchronizer,
validates the canonical graph there, and reconciles only its allowed outputs.
It never writes the repository supplied by the change set.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
import stat
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Literal

from officina.blueprints.graph import load_repository_blueprint_graph

if TYPE_CHECKING:
    from ._relocation_engine import BlueprintSynchronizer, ChangeSet, RelocationManifest


_BASIS_PATH = "references/certification-policy/certification-basis-roots.json"
_SCHEMA_PREFIX = "references/blueprint-schema/"
_MODULE_SCHEMA_PATH = "references/blueprint-schema/module.schema.json"
_SHADOW_EXCLUDED_PARTS = {
    ".agents",
    ".certificates",
    "." + "co" + "dex",
    ".git",
    ".mypy_cache",
    ".pooled-reviews",
    ".pytest_cache",
    ".venv",
    ".worktrees",
    "__pycache__",
    "_build",
    "build",
    "node_modules",
}
_GENERATED_BLOCKS = (
    ("<!-- BEGIN BLUEPRINT CONTRACT -->", "<!-- END BLUEPRINT CONTRACT -->"),
    ("<!-- BEGIN BLUEPRINT INTERFACES -->", "<!-- END BLUEPRINT INTERFACES -->"),
    ("<!-- BEGIN BLUEPRINT USED INTERFACES -->", "<!-- END BLUEPRINT USED INTERFACES -->"),
)


class MechanicalClosureError(RuntimeError):
    """Signal an actionable failure while closing the projected shadow tree."""


@dataclass(frozen=True)
class MechanicalClosureResult:
    """Record deterministic paths and successful actions produced by closure.

    Paths identify exact in-memory change-set entries. Validation strings name
    completed canonical actions; they are not claims about validators or
    certification, which relocation deliberately does not invoke.
    """

    certification_basis_changes: tuple[str, ...] = ()
    generated_artifact_changes: tuple[str, ...] = ()
    validation_results: tuple[str, ...] = ()


def _is_excluded(relative: str) -> bool:
    """Return whether a repository-relative path is intentionally absent from shadow.

    The shadow contains only ordinary project inputs needed by the synchronizer
    and graph loader; transient repositories, caches, environments, build
    output, certificate records, and pooled reviews are never materialized.
    """

    return any(part in _SHADOW_EXCLUDED_PARTS for part in PurePosixPath(relative).parts)


def _mode_for_projected_path(changes: ChangeSet, relative: str) -> int:
    """Resolve one regular-file mode for shadow materialization.

    Existing source modes come from the real file unless the projected write
    explicitly overrides them. New regular files default to ``100644``. The
    mode is copied exactly, including the normal Git modes ``100644`` and
    ``100755``, so reconciliation cannot silently change executable bits.
    """

    source = changes.root / relative
    mode = changes.write_modes.get(
        relative,
        stat.S_IMODE(source.stat().st_mode) if source.is_file() else 0o644,
    )
    return mode


def _materialize_internal_symlink(
    changes: ChangeSet,
    source: Path,
    target: Path,
    relative: str,
) -> None:
    """Preserve one safe relative link whose resolved target is projected.

    The copied link text keeps the shadow's topology faithful to the repository.
    Absolute, escaping, cyclic, and dangling links are rejected because their
    resolved target cannot be proved to be an existing projected repository path.
    """

    try:
        link_text = source.readlink()
        if link_text.is_absolute():
            raise ValueError("absolute link")
        resolved = (source.parent / link_text).resolve(strict=False)
        target_relative = resolved.relative_to(changes.root).as_posix()
    except (OSError, RuntimeError, ValueError):
        raise MechanicalClosureError(f"unsafe shadow symlink: {relative}") from None
    if target_relative not in changes.projected_files():
        raise MechanicalClosureError(f"unsafe shadow symlink: {relative}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(link_text)


def _materialize_projection(changes: ChangeSet, shadow_root: Path) -> None:
    """Write included projected files and safe symlinks into an isolated shadow root.

    Regular files preserve projected bytes and modes. Repository-internal
    relative symlinks preserve their link text only when their resolved target
    is an existing projected path; all other symlinks fail with their exact path.
    """

    for relative in sorted(changes.projected_files()):
        if _is_excluded(relative):
            continue
        source = changes.root / relative
        target = shadow_root / relative
        if source.is_symlink():
            if relative in changes.writes:
                raise MechanicalClosureError(
                    f"cannot materialize projected write to symlink: {relative}"
                )
            _materialize_internal_symlink(changes, source, target, relative)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(changes.read_bytes(relative))
        target.chmod(_mode_for_projected_path(changes, relative))


def _snapshot(
    shadow_root: Path,
) -> dict[str, tuple[bytes, int, Literal["directory", "file", "symlink"]]]:
    """Capture every shadow directory, file, and symlink after one action.

    Materialization exclusions limit only the synchronizer's inputs. The
    snapshot is its write boundary, so it deliberately includes artifacts in
    excluded trees too. New, removed, byte-changed, or mode-changed entries
    can therefore be rejected before any result reaches the in-memory change
    set.
    """

    result: dict[str, tuple[bytes, int, Literal["directory", "file", "symlink"]]] = {}
    for path in shadow_root.rglob("*"):
        if path.is_symlink():
            relative = path.relative_to(shadow_root).as_posix()
            result[relative] = (
                path.readlink().as_posix().encode("utf-8"),
                stat.S_IMODE(path.lstat().st_mode),
                "symlink",
            )
            continue
        if path.is_dir():
            relative = path.relative_to(shadow_root).as_posix()
            result[relative] = (b"", stat.S_IMODE(path.stat().st_mode), "directory")
            continue
        if not path.is_file():
            continue
        relative = path.relative_to(shadow_root).as_posix()
        result[relative] = (path.read_bytes(), stat.S_IMODE(path.stat().st_mode), "file")
    return result


def _closure_applies(projected_files: set[str]) -> bool:
    """Return whether this projection presents any canonical Officina closure marker.

    Plain mechanics fixtures intentionally contain no schema inventory. Seeing
    a schema marker means the tree claims to be an Officina closure input and
    all required inputs, including an injected synchronizer, must be present.
    """

    return any(path.startswith(_SCHEMA_PREFIX) for path in projected_files)


def _require_closure_inputs(shadow_root: Path) -> None:
    """Reject an incomplete canonical closure tree with the missing exact path."""

    required = (
        _BASIS_PATH,
        "references/blueprint-schema",
    )
    for relative in required:
        if not (shadow_root / relative).exists():
            raise MechanicalClosureError(f"missing closure input: {relative}")
    if not (shadow_root / _MODULE_SCHEMA_PATH).is_file():
        raise MechanicalClosureError(f"missing closure input: {_MODULE_SCHEMA_PATH}")


def _read_basis(shadow_root: Path) -> list[str]:
    """Parse the certification basis list in the shadow with strict path entries."""

    path = shadow_root / _BASIS_PATH
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MechanicalClosureError(f"cannot parse certification basis {_BASIS_PATH}: {exc}") from exc
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise MechanicalClosureError(
            f"invalid certification basis {_BASIS_PATH}: expected a list of non-empty paths"
        )
    return value


def _is_readme_initializer(path: Path, relative: str) -> bool:
    """Return whether an initializer has exactly one module-docstring statement.

    The AST condition prevents executable package code from entering the
    certification bootstrap basis merely because it appeared in a catalog.
    """

    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
    except (OSError, UnicodeError, SyntaxError) as exc:
        raise MechanicalClosureError(f"cannot parse catalog initializer {relative}: {exc}") from exc
    return (
        len(tree.body) == 1
        and isinstance(tree.body[0], ast.Expr)
        and isinstance(tree.body[0].value, ast.Constant)
        and isinstance(tree.body[0].value.value, str)
    )


def _update_certification_basis(shadow_root: Path, manifest: RelocationManifest) -> bool:
    """Add README-only Officina catalog initializers to the shadow basis manifest.

    The manifest remains the authority for catalog paths. Only catalog
    initializers below ``src/officina`` are mechanical certification-basis
    additions; a substantive initializer fails instead of creating trust.
    """

    entries = _read_basis(shadow_root)
    additions: set[str] = set()
    for catalog in manifest.package_catalogs:
        relative = f"{catalog.path}/__init__.py"
        if not relative.startswith("src/officina/"):
            continue
        path = shadow_root / relative
        if not path.is_file():
            raise MechanicalClosureError(f"missing catalog initializer for certification basis: {relative}")
        if not _is_readme_initializer(path, relative):
            raise MechanicalClosureError(
                f"{relative}: certification basis requires a README-only initializer"
            )
        additions.add(relative)
    updated = sorted(set(entries) | additions)
    if updated == entries:
        return False
    (shadow_root / _BASIS_PATH).write_text(
        json.dumps(updated, indent=2) + "\n",
        encoding="utf-8",
    )
    return True


def _generated_skill_change(before: bytes, after: bytes, relative: str) -> bool:
    """Return whether a skill Markdown difference is confined to generated blocks.

    Each canonical block is removed before comparing the two documents. This
    permits the synchronizer to insert a missing generated block while still
    rejecting any change to authored Markdown. Missing or duplicated
    delimiters are rejected because they would make the write boundary ambiguous.
    """

    try:
        before_text = before.decode("utf-8")
        after_text = after.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MechanicalClosureError(f"generated skill artifact is not UTF-8: {relative}: {exc}") from exc

    def mask(text: str) -> str | None:
        """Remove each complete generated block from authored Markdown."""

        masked = text
        for start, end in _GENERATED_BLOCKS:
            start_count = masked.count(start)
            end_count = masked.count(end)
            if start_count != end_count or start_count > 1:
                return None
            if start_count == 1:
                first = masked.index(start)
                last = masked.index(end, first) + len(end)
                if masked[last:last + 1] == "\n":
                    last += 1
                masked = masked[:first] + masked[last:]
        return masked

    before_masked, after_masked = mask(before_text), mask(after_text)
    return before_masked is not None and before_masked == after_masked


def _allowed_generated_change(relative: str, before: bytes, after: bytes) -> bool:
    """Return whether one synchronizer difference belongs to its narrow allowlist."""

    if relative == "references/blueprint-schema/runtime_dependencies.json":
        return True
    parts = PurePosixPath(relative).parts
    return (
        len(parts) == 3
        and parts[0] == "skills"
        and parts[2] == "SKILL.md"
        and _generated_skill_change(before, after, relative)
    )


def _reconcile_generated_changes(
    changes: ChangeSet,
    before: dict[str, tuple[bytes, int, Literal["directory", "file", "symlink"]]],
    after: dict[str, tuple[bytes, int, Literal["directory", "file", "symlink"]]],
) -> tuple[str, ...]:
    """Reject unapproved synchronizer writes and absorb exact allowed bytes/modes.

    Synchronizer deletes are rejected because the compact closure has no
    declared generated-deletion authority. Allowed changed files are written
    only into ``ChangeSet``; the real tree remains untouched until publication.
    """

    changed_paths = sorted(set(before) | set(after))
    generated: list[str] = []
    for relative in changed_paths:
        previous, current = before.get(relative), after.get(relative)
        if previous == current:
            continue
        if current is None:
            raise MechanicalClosureError(f"unexpected shadow delete: {relative}")
        if current[2] == "symlink" or (previous is not None and previous[2] == "symlink"):
            raise MechanicalClosureError(f"unexpected shadow symlink: {relative}")
        if previous is not None and previous[2] != current[2]:
            raise MechanicalClosureError(f"unexpected shadow kind change: {relative}")
        if previous is not None and previous[0] == current[0]:
            raise MechanicalClosureError(f"unexpected shadow mode change: {relative}")
        before_bytes = previous[0] if previous is not None else b""
        if not _allowed_generated_change(relative, before_bytes, current[0]):
            raise MechanicalClosureError(f"unexpected shadow write: {relative}")
        changes.write_bytes(relative, current[0])
        if relative in changes.writes:
            changes.write_modes[relative] = current[1]
            generated.append(relative)
    return tuple(generated)


def _first_snapshot_difference(
    before: dict[str, tuple[bytes, int, Literal["directory", "file", "symlink"]]],
    after: dict[str, tuple[bytes, int, Literal["directory", "file", "symlink"]]],
) -> str | None:
    """Return the first deterministic path whose shadow bytes or mode changed.

    The check-only synchronizer action has no write authority. Returning the
    first sorted difference makes a violation immediately actionable without
    presenting an unstable inventory order.
    """

    for relative in sorted(set(before) | set(after)):
        if before.get(relative) != after.get(relative):
            return relative
    return None


def close_projected_relocation(
    changes: ChangeSet,
    manifest: RelocationManifest,
    *,
    synchronize: BlueprintSynchronizer | None = None,
) -> MechanicalClosureResult:
    """Close deterministic blueprint artifacts without changing the real tree.

    A projection with neither canonical graph-schema nor synchronizer marker is
    a narrow relocation-mechanics fixture and has no closure work. Once a
    marker is present, all canonical closure inputs and an authorized injected
    synchronizer are required. The function updates only the supplied in-memory
    ``ChangeSet`` and returns exact report categories for certification-basis,
    generated-artifact, and graph/sync validation results.
    """

    if not _closure_applies(changes.projected_files()):
        return MechanicalClosureResult()
    if synchronize is None:
        raise MechanicalClosureError("missing closure synchronizer")

    with TemporaryDirectory(prefix="officina-relocation-") as temporary:
        shadow_root = Path(temporary)
        _materialize_projection(changes, shadow_root)
        _require_closure_inputs(shadow_root)
        basis_changed = _update_certification_basis(shadow_root, manifest)
        before_sync = _snapshot(shadow_root)
        synchronize(shadow_root, check=False)
        after_sync = _snapshot(shadow_root)
        generated = _reconcile_generated_changes(changes, before_sync, after_sync)
        before_check = _snapshot(shadow_root)
        synchronize(shadow_root, check=True)
        after_check = _snapshot(shadow_root)
        check_difference = _first_snapshot_difference(before_check, after_check)
        if check_difference is not None:
            raise MechanicalClosureError(
                f"blueprint synchronizer check changed shadow: {check_difference}"
            )
        try:
            load_repository_blueprint_graph(
                shadow_root,
                schema_root=shadow_root / "references/blueprint-schema",
                expected_schema_version=6,
            )
        except Exception as exc:
            raise MechanicalClosureError(f"repository graph validation failed: {exc}") from exc

        basis_changes: tuple[str, ...] = ()
        if basis_changed:
            basis_payload, basis_mode, _ = after_sync[_BASIS_PATH]
            changes.write_bytes(_BASIS_PATH, basis_payload)
            if _BASIS_PATH in changes.writes:
                changes.write_modes[_BASIS_PATH] = basis_mode
                basis_changes = (_BASIS_PATH,)
        return MechanicalClosureResult(
            certification_basis_changes=basis_changes,
            generated_artifact_changes=generated,
            validation_results=(
                "blueprint synchronizer synchronize",
                "blueprint synchronizer check",
                "repository blueprint graph",
            ),
        )
