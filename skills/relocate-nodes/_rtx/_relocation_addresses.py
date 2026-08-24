"""Derive immutable node addresses from one repository's configured roots."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, Mapping, Protocol

import yaml

from officina.common import toml_io
from officina.configuration.repository import load_repository_configuration


class AddressResolutionError(ValueError):
    """Signal an unsafe configured-root address state.

    Intent
    ------
    Give callers one dedicated error for address derivation failures.

    Rationale
    ---------
    Address validation must not be confused with generic filesystem errors.

    Pseudocode
    ----------
    - carry one unsafe-address explanation

    Wraps
    -----
    - none
    """


class _RelocationEntry(Protocol):
    """Describe the move fields address derivation needs.

    Intent
    ------
    Constrain derivation to source and target path facts.

    Rationale
    ---------
    The address layer must not depend on the manifest implementation.

    Pseudocode
    ----------
    - expose source and target strings

    Wraps
    -----
    - none
    """

    source: str
    target: str


@dataclass(frozen=True)
class NodeAddress:
    """Store one checkout-independent configured-root node identity.

    Intent
    ------
    Preserve an immutable address for later relocation validation.

    Rationale
    ---------
    Repository-relative values compare across source and target states.

    Pseudocode
    ----------
    - retain ID root and repository path values

    Wraps
    -----
    - none
    """

    node_id: str
    configured_root: str
    repository_path: str


@dataclass(frozen=True)
class DerivedRelocation:
    """Store the source and target addresses induced by one move.

    Intent
    ------
    Bind two immutable address values into one relocation fact.

    Rationale
    ---------
    Later phases need a state-independent mapping before projection.

    Pseudocode
    ----------
    - retain source and target addresses

    Wraps
    -----
    - none
    """

    source: NodeAddress
    target: NodeAddress


def _relative_path(value: str, *, field: str) -> PurePosixPath:
    """Validate one repository-relative POSIX path.

    Intent
    ------
    Reject empty, absolute, and traversal move endpoints.

    Rationale
    ---------
    Address lookup is confined to the selected repository.

    Pseudocode
    ----------
    - parse value as a POSIX path
    - reject an unsafe spelling
    - return the parsed path

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .AddressResolutionError:
      why:
        constructs: "Reports an endpoint that cannot be confined to the repository."
    """

    path = PurePosixPath(value)
    if (
        not isinstance(value, str)
        or not value
        or path.is_absolute()
        or ".." in path.parts
        or value.startswith("./")
    ):
        raise AddressResolutionError(f"{field} must be a repository-relative path: {value!r}")
    return path


def _containing_roots(
    path: PurePosixPath, roots: tuple[Path, ...], repository: Path
) -> tuple[tuple[Path, PurePosixPath], ...]:
    """Find configured roots that contain one endpoint.

    Intent
    ------
    Enumerate containment without ambient root discovery.

    Rationale
    ---------
    Nested configured roots must remain visible as ambiguity.

    Pseudocode
    ----------
    - compare the path to every configured root
    - return each matching root and suffix

    Wraps
    -----
    - none
    """

    matches: list[tuple[Path, PurePosixPath]] = []
    for root in roots:
        relative_root = PurePosixPath(root.relative_to(repository).as_posix())
        try:
            suffix = path.relative_to(relative_root)
        except ValueError:
            continue
        matches.append((root, suffix))
    return tuple(matches)


def _address_for_path(
    path: PurePosixPath,
    *,
    roots: tuple[Path, ...],
    repository: Path,
) -> tuple[NodeAddress, Path, PurePosixPath]:
    """Derive one address and its unique configured root.

    Intent
    ------
    Turn one endpoint into an immutable root-relative address.

    Rationale
    ---------
    A node ID is authoritative only under exactly one configured root.

    Pseudocode
    ----------
    - find containing configured roots
    - reject no root, multiple roots, or the root itself
    - return the derived address root and suffix

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._containing_roots:
      why:
        computes: "Builds the complete configured-root containment set."

    InstantiationsFromRepo
    ----------------------
    .AddressResolutionError:
      why:
        constructs: "Reports an endpoint without one unambiguous node address."
    .NodeAddress:
      why:
        constructs: "Builds the immutable address returned to later phases."
    """

    matches = _containing_roots(path, roots, repository)
    if not matches:
        raise AddressResolutionError(
            f"path is outside every configured root: {path.as_posix()}"
        )
    if len(matches) != 1:
        labels = ", ".join(root.relative_to(repository).as_posix() for root, _ in matches)
        raise AddressResolutionError(
            f"path is contained by multiple configured roots: {path.as_posix()} ({labels})"
        )
    root, suffix = matches[0]
    if not suffix.parts:
        raise AddressResolutionError(
            f"configured root itself is not a node address: {path.as_posix()}"
        )
    root_text = root.relative_to(repository).as_posix()
    return (
        NodeAddress(
            node_id=".".join(suffix.parts),
            configured_root=root_text,
            repository_path=path.as_posix(),
        ),
        root,
        suffix,
    )


def _blueprint(path: Path, *, label: str) -> Mapping[str, object]:
    """Load one existing schema-v6 node blueprint.

    Intent
    ------
    Return only a valid mapping for existing-side validation.

    Rationale
    ---------
    Address evidence must come from an actual registered blueprint.

    Pseudocode
    ----------
    - read the adjacent blueprint
    - reject unreadable or non-v6 mappings
    - return the mapping

    Wraps
    -----
    - YAML and filesystem decoding errors become address errors

    InstantiationsFromRepo
    ----------------------
    .AddressResolutionError:
      why:
        constructs: "Reports a missing or invalid existing-side registration fact."
    """

    blueprint_path = path / "blueprint.yaml"
    try:
        value = yaml.safe_load(blueprint_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise AddressResolutionError(f"missing {label} blueprint: {blueprint_path}") from exc
    if not isinstance(value, Mapping) or value.get("schema_version") != 6:
        raise AddressResolutionError(f"invalid {label} blueprint: {blueprint_path}")
    return value


def _validate_existing_address(
    address: NodeAddress,
    *,
    root: Path,
    suffix: PurePosixPath,
) -> None:
    """Validate an existing node ID and parent registrations.

    Intent
    ------
    Prove the physically present side belongs to its derived address.

    Rationale
    ---------
    Projected target registration remains Task 2 responsibility.

    Pseudocode
    ----------
    - load and compare the node blueprint ID
    - load every ancestor blueprint
    - require each parent to register its next child

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._blueprint:
      why:
        computes: "Builds each node or parent registration mapping used as evidence."

    InstantiationsFromRepo
    ----------------------
    .AddressResolutionError:
      why:
        constructs: "Reports inconsistent existing-side identity or ancestry."
    """

    node_path = root.joinpath(*suffix.parts)
    blueprint = _blueprint(node_path, label="node")
    if blueprint.get("id") != address.node_id:
        raise AddressResolutionError(
            f"blueprint ID mismatch for {address.repository_path}: "
            f"expected {address.node_id!r}, found {blueprint.get('id')!r}"
        )

    for index in range(1, len(suffix.parts)):
        parent_suffix = suffix.parts[:index]
        child_name = suffix.parts[index]
        parent_path = root.joinpath(*parent_suffix)
        parent_address = ".".join(parent_suffix)
        parent = _blueprint(parent_path, label="parent registration")
        if parent.get("id") != parent_address:
            raise AddressResolutionError(
                f"blueprint ID mismatch for parent {parent_path}: "
                f"expected {parent_address!r}, found {parent.get('id')!r}"
            )
        children = parent.get("children")
        if not isinstance(children, Mapping) or not children:
            raise AddressResolutionError(
                f"missing parent registration for {address.repository_path}: {child_name!r}"
            )
        if child_name not in children:
            raise AddressResolutionError(
                f"mismatched parent registration for {address.repository_path}: {child_name!r}"
            )


def derive_relocations(
    repository: Path, entries: Iterable[_RelocationEntry]
) -> tuple[DerivedRelocation, ...]:
    """Derive moves from configured roots in either physical state.

    Intent
    ------
    Produce immutable source-target addresses after existing-side validation.

    Rationale
    ---------
    Exactly one physical endpoint proves a stable transition without inventing
    projected target registrations before graph validation.

    Pseudocode
    ----------
    - load the exact repository configuration
    - derive both endpoint addresses for every move
    - require exactly one endpoint to exist
    - validate the existing endpoint and return all pairs

    Wraps
    -----
    - repository configuration errors propagate unchanged

    CallsFromRepo
    -------------
    ._relative_path:
      why:
        computes: "Builds confined POSIX endpoint paths."
    ._address_for_path:
      why:
        computes: "Builds each configured-root address and containment evidence."
    ._validate_existing_address:
      why:
        computes: "Proves the existing side's blueprint and registration ancestry."
    .officina.common.toml_io.repository_config_filename:
      why:
        computes: "Builds the sole repository configuration filename."
    .officina.configuration.repository.load_repository_configuration:
      why:
        computes: "Builds validated configured roots from the exact repository file."

    InstantiationsFromRepo
    ----------------------
    .AddressResolutionError:
      why:
        constructs: "Reports ambiguous physical state or configuration-root mismatch."
    .DerivedRelocation:
      why:
        constructs: "Builds each immutable source-target result pair."
    """

    repository = Path(repository).absolute()
    configuration = load_repository_configuration(
        repository / toml_io.repository_config_filename()
    )
    if configuration.repository_root != repository:
        raise AddressResolutionError(
            f"configuration resolved a different repository root: {configuration.repository_root}"
        )

    derived: list[DerivedRelocation] = []
    for entry in entries:
        source_path = _relative_path(entry.source, field="move source")
        target_path = _relative_path(entry.target, field="move target")
        source, source_root, source_suffix = _address_for_path(
            source_path,
            roots=configuration.module_roots,
            repository=repository,
        )
        target, target_root, target_suffix = _address_for_path(
            target_path,
            roots=configuration.module_roots,
            repository=repository,
        )
        source_exists = repository.joinpath(*source_path.parts, "blueprint.yaml").is_file()
        target_exists = repository.joinpath(*target_path.parts, "blueprint.yaml").is_file()
        if source_exists and target_exists:
            raise AddressResolutionError(
                f"physical target collision: both source and target exist for "
                f"{source.repository_path} -> {target.repository_path}"
            )
        if not source_exists and not target_exists:
            raise AddressResolutionError(
                f"neither source nor target exists for "
                f"{source.repository_path} -> {target.repository_path}"
            )
        if source_exists:
            _validate_existing_address(source, root=source_root, suffix=source_suffix)
        else:
            _validate_existing_address(target, root=target_root, suffix=target_suffix)
        derived.append(DerivedRelocation(source=source, target=target))
    return tuple(derived)


__all__ = [
    "AddressResolutionError",
    "DerivedRelocation",
    "NodeAddress",
    "derive_relocations",
]
