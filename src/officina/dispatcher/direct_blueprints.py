"""Route-local v6 blueprint lookup for dispatcher invocations.

The resolver derives every candidate path from the requested dotted module ID
and configured roots.  It never inventories a root, builds a graph, repairs
state, or writes routing data.  Only the requested ancestry is parsed, and each
parent must explicitly register the next local child segment.
"""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import yaml

from officina.common.repository_configuration import RepositoryConfiguration
from officina.dispatcher.errors import DirectBlueprintError


_ORDINARY_SEGMENT = r"[a-z0-9]+(?:-[a-z0-9]+)*"
_LOCAL_SEGMENT_RE = re.compile(rf"^(?:_rtx|{_ORDINARY_SEGMENT})$")
_INTERFACE_NAME_RE = re.compile(rf"^{_ORDINARY_SEGMENT}$")


def _module_parts(module_id: str) -> tuple[str, ...]:
    """Return validated canonical module segments for direct path derivation."""

    if not isinstance(module_id, str) or not module_id:
        raise DirectBlueprintError(
            f"invalid module id: {module_id!r}",
            code="dispatcher.invalid_module_id",
            target_module_id=module_id if isinstance(module_id, str) else "",
        )
    parts = tuple(module_id.split("."))
    invalid = (
        not _INTERFACE_NAME_RE.fullmatch(parts[0])
        or any(not _LOCAL_SEGMENT_RE.fullmatch(part) for part in parts[1:])
        or any(part in {"interface", "source"} for part in parts)
        or any(part.endswith("-rtx") for part in parts)
    )
    if invalid:
        raise DirectBlueprintError(
            f"invalid module id: {module_id}",
            code="dispatcher.invalid_module_id",
            target_module_id=module_id,
        )
    return parts


def parse_interface_id(interface_id: str) -> tuple[str, str]:
    """Split one canonical ``<module>.interface.<name>`` identifier."""

    if not isinstance(interface_id, str) or interface_id.count(".interface.") != 1:
        raise DirectBlueprintError(
            f"invalid interface id: {interface_id!r}",
            code="dispatcher.invalid_interface_id",
        )
    module_id, interface_name = interface_id.split(".interface.", 1)
    try:
        _module_parts(module_id)
    except DirectBlueprintError as exc:
        raise DirectBlueprintError(
            f"invalid interface id: {interface_id!r}",
            code="dispatcher.invalid_interface_id",
        ) from exc
    if not _INTERFACE_NAME_RE.fullmatch(interface_name):
        raise DirectBlueprintError(
            f"invalid interface id: {interface_id!r}",
            code="dispatcher.invalid_interface_id",
            target_module_id=module_id,
        )
    return module_id, interface_name


@dataclass(frozen=True)
class DirectModule:
    """One route-local module declaration loaded from its canonical path."""

    module_id: str
    root: Path
    blueprint_path: Path
    declaration: Mapping[str, object]


class DirectBlueprintRepository:
    """Resolve v6 modules by exact configured-root and dotted-ID probes."""

    def __init__(self, configuration: RepositoryConfiguration) -> None:
        self.configuration = configuration
        self._top_level_roots: dict[str, Path] = {}
        self._modules: dict[str, DirectModule] = {}

    @staticmethod
    def _candidate_path(root: Path, parts: tuple[str, ...]) -> Path:
        return root.joinpath(*parts, "blueprint.yaml")

    @staticmethod
    def _probe_regular_blueprint(path: Path, *, module_id: str) -> bool:
        """Check one exact candidate without following a symlink component."""

        current = Path(path.anchor)
        for part in path.parts[1:]:
            current /= part
            try:
                metadata = current.lstat()
            except FileNotFoundError:
                return False
            except OSError as exc:
                raise DirectBlueprintError(
                    f"cannot inspect blueprint path for {module_id}: {current}",
                    code="dispatcher.unsafe_blueprint_path",
                    target_module_id=module_id,
                ) from exc
            if stat.S_ISLNK(metadata.st_mode):
                raise DirectBlueprintError(
                    f"blueprint path contains a symlink for {module_id}: {current}",
                    code="dispatcher.unsafe_blueprint_path",
                    target_module_id=module_id,
                )
        try:
            return stat.S_ISREG(path.stat().st_mode)
        except OSError as exc:
            raise DirectBlueprintError(
                f"cannot inspect blueprint for {module_id}: {path}",
                code="dispatcher.unsafe_blueprint_path",
                target_module_id=module_id,
            ) from exc

    def _root_for(self, top_level_id: str) -> Path:
        cached = self._top_level_roots.get(top_level_id)
        if cached is not None:
            return cached
        matches = []
        for root in self.configuration.module_roots:
            path = self._candidate_path(root, (top_level_id,))
            if self._probe_regular_blueprint(path, module_id=top_level_id):
                matches.append(root)
        if not matches:
            raise DirectBlueprintError(
                f"module not found: {top_level_id}",
                code="dispatcher.module_not_found",
                target_module_id=top_level_id,
            )
        if len(matches) != 1:
            raise DirectBlueprintError(
                f"module is present in multiple configured roots: {top_level_id}",
                code="dispatcher.module_ambiguous",
                target_module_id=top_level_id,
            )
        self._top_level_roots[top_level_id] = matches[0]
        return matches[0]

    @staticmethod
    def _parse_declaration(path: Path, *, module_id: str) -> Mapping[str, object]:
        try:
            with path.open("rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise DirectBlueprintError(
                        f"blueprint is not a regular file: {path}",
                        code="dispatcher.unsafe_blueprint_path",
                        target_module_id=module_id,
                    )
                declaration = yaml.load(stream, Loader=yaml.CSafeLoader)
        except DirectBlueprintError:
            raise
        except (OSError, yaml.YAMLError) as exc:
            raise DirectBlueprintError(
                f"malformed blueprint for {module_id}: {path}",
                code="dispatcher.blueprint_malformed",
                target_module_id=module_id,
            ) from exc
        if not isinstance(declaration, Mapping):
            raise DirectBlueprintError(
                f"blueprint must be a mapping for {module_id}: {path}",
                code="dispatcher.blueprint_malformed",
                target_module_id=module_id,
            )
        if declaration.get("schema_version") != 6 or declaration.get("node_type") != "module":
            raise DirectBlueprintError(
                f"direct dispatch requires a v6 module blueprint: {path}",
                code="dispatcher.blueprint_schema_mismatch",
                target_module_id=module_id,
            )
        if declaration.get("id") != module_id:
            raise DirectBlueprintError(
                f"blueprint identity does not match path for {module_id}: {path}",
                code="dispatcher.blueprint_identity_mismatch",
                target_module_id=module_id,
            )
        for field in ("children", "namespace_exports", "sources", "exports"):
            if not isinstance(declaration.get(field), Mapping):
                raise DirectBlueprintError(
                    f"blueprint field {field} must be a mapping for {module_id}",
                    code="dispatcher.blueprint_malformed",
                    target_module_id=module_id,
                )
        children = declaration["children"]
        if any(
            not isinstance(key, str)
            or not _LOCAL_SEGMENT_RE.fullmatch(key)
            or key in {"interface", "source"}
            or value != {}
            for key, value in children.items()
        ):
            raise DirectBlueprintError(
                f"blueprint has invalid child registrations for {module_id}",
                code="dispatcher.blueprint_malformed",
                target_module_id=module_id,
            )
        return declaration

    def _load_at(self, root: Path, parts: tuple[str, ...]) -> DirectModule:
        module_id = ".".join(parts)
        cached = self._modules.get(module_id)
        if cached is not None:
            return cached
        path = self._candidate_path(root, parts)
        if not self._probe_regular_blueprint(path, module_id=module_id):
            raise DirectBlueprintError(
                f"registered module blueprint not found: {module_id}",
                code="dispatcher.module_not_found",
                target_module_id=module_id,
            )
        module = DirectModule(
            module_id=module_id,
            root=root,
            blueprint_path=path,
            declaration=self._parse_declaration(path, module_id=module_id),
        )
        self._modules[module_id] = module
        return module

    def load_ancestry(self, module_id: str) -> tuple[DirectModule, ...]:
        """Load only ``module_id`` and its explicitly registered ancestry."""

        parts = _module_parts(module_id)
        root = self._root_for(parts[0])
        ancestry = []
        for depth in range(1, len(parts) + 1):
            current = self._load_at(root, parts[:depth])
            ancestry.append(current)
            if depth < len(parts):
                child = parts[depth]
                if current.declaration["children"].get(child) != {}:
                    raise DirectBlueprintError(
                        f"{current.module_id} does not register child {child}",
                        code="dispatcher.child_unregistered",
                        target_module_id=".".join(parts[: depth + 1]),
                    )
        return tuple(ancestry)

    def load_module(self, module_id: str) -> DirectModule:
        """Load one module after verifying every parent registration hop."""

        return self.load_ancestry(module_id)[-1]


__all__ = [
    "DirectBlueprintError",
    "DirectBlueprintRepository",
    "DirectModule",
    "parse_interface_id",
]
