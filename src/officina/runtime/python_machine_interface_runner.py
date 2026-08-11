"""Generic runner for Python dispatcher machine-interface bindings."""
from __future__ import annotations

import argparse
import hashlib
import hmac
import importlib
import importlib.abc
import importlib.util
import os
import stat
import sys
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Iterator, Sequence

from officina.common.atomic_files import read_regular_file_bytes
from officina.common.blueprint_graph import (
    BlueprintGraphError,
    decode_runtime_python_package_snapshot,
    snapshot_runtime_python_package,
)
from officina.common.repository_paths import (
    RepositoryPathError,
    repository_relative_path,
    repository_relative_posix,
)

from .python_machine_interface import (
    PythonMachineInterface,
    PythonProcessTarget,
    PythonProcessTargetError,
    coerce_exit_code,
    set_runtime_dispatch_context,
)


class InterfaceLoadError(RuntimeError):
    """Raised when a Python machine-interface binding cannot be loaded."""


class _BoundPackageSources(dict[str, tuple[bytes, str, bool]]):
    """Bound source map with persistent executed-module evidence."""

    def __init__(self) -> None:
        super().__init__()
        self.executed_modules: set[str] = set()
        self.trusted_modules: dict[str, ModuleType] = {}


class _BoundPackageFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Load one package namespace only from dispatcher-bound source snapshots."""

    def __init__(self, sources: dict[str, tuple[bytes, str, bool]]) -> None:
        self.sources = sources
        self.roots = {name.partition(".")[0] for name in sources}
        self.executed_modules = getattr(
            sources,
            "executed_modules",
            set(),
        )

    def find_spec(self, fullname: str, path=None, target=None):
        source = self.sources.get(fullname)
        if source is not None:
            return importlib.util.spec_from_loader(
                fullname,
                self,
                is_package=source[2],
            )
        if any(fullname == root or fullname.startswith(f"{root}.") for root in self.roots):
            raise ImportError(
                f"{fullname}: module is outside the validated Python package snapshot"
            )
        return None

    def create_module(self, spec):
        return None

    def exec_module(self, module: ModuleType) -> None:
        source, logical_path, is_package = self.sources[module.__name__]
        self.executed_modules.add(module.__name__)
        module.__file__ = logical_path
        if is_package:
            module.__path__ = []
        exec(compile(source, logical_path, "exec"), module.__dict__)


class _LazyConfinedPackageFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Resolve one synthetic package from exact, no-follow module-root probes."""

    def __init__(self, module_root: Path, logical_package: str) -> None:
        self.module_root = Path(os.path.abspath(module_root))
        self.logical_package = logical_package
        self._resolved: dict[str, tuple[Path | None, bool]] = {}

    def _regular(self, path: Path) -> bool:
        try:
            relative = path.relative_to(self.module_root)
        except ValueError as exc:
            raise ImportError(f"{path}: import escaped the confined module root") from exc
        current = self.module_root
        for part in relative.parts:
            current /= part
            try:
                metadata = current.lstat()
            except FileNotFoundError:
                return False
            except OSError as exc:
                raise ImportError(f"cannot inspect confined import {current}") from exc
            if stat.S_ISLNK(metadata.st_mode):
                raise ImportError(f"confined import contains a symlink: {current}")
        return stat.S_ISREG(path.stat().st_mode)

    def find_spec(self, fullname: str, path=None, target=None):
        if fullname == self.logical_package:
            init_path = self.module_root / "__init__.py"
            resolved = (init_path, True) if self._regular(init_path) else (None, True)
        elif fullname.startswith(f"{self.logical_package}."):
            suffix = fullname[len(self.logical_package) + 1 :].split(".")
            if not all(part.isidentifier() for part in suffix):
                raise ImportError(f"invalid confined module name: {fullname}")
            package_path = self.module_root.joinpath(*suffix, "__init__.py")
            module_path = self.module_root.joinpath(*suffix).with_suffix(".py")
            package_exists = self._regular(package_path)
            module_exists = self._regular(module_path)
            if package_exists and module_exists:
                raise ImportError(f"ambiguous confined module: {fullname}")
            if package_exists:
                resolved = (package_path, True)
            elif module_exists:
                resolved = (module_path, False)
            else:
                raise ImportError(f"{fullname}: module is outside the confined package")
        else:
            return None
        self._resolved[fullname] = resolved
        return importlib.util.spec_from_loader(fullname, self, is_package=resolved[1])

    def create_module(self, spec):
        return None

    def exec_module(self, module: ModuleType) -> None:
        path, is_package = self._resolved[module.__name__]
        module.__file__ = str(path) if path is not None else str(self.module_root)
        if is_package:
            module.__path__ = []
        if path is None:
            return
        source = read_regular_file_bytes(
            path,
            allowed_root=self.module_root,
            allow_non_atomic=False,
        )
        saved_sys_path = list(sys.path)
        try:
            exec(compile(source, str(path), "exec"), module.__dict__)
            if sys.path != saved_sys_path:
                raise ImportError(f"{module.__name__}: gateway mutated sys.path")
        finally:
            sys.path[:] = saved_sys_path


@contextmanager
def _lazy_confined_package_imports(
    module_root: Path,
    logical_package: str,
) -> Iterator[None]:
    """Install a per-invocation exact-path finder and clear its module cache."""

    finder = _LazyConfinedPackageFinder(module_root, logical_package)
    saved = {
        name: module
        for name, module in sys.modules.items()
        if name == logical_package or name.startswith(f"{logical_package}.")
    }
    for name in tuple(saved):
        del sys.modules[name]
    sys.meta_path.insert(0, finder)
    try:
        yield
    finally:
        if finder in sys.meta_path:
            sys.meta_path.remove(finder)
        for name in tuple(sys.modules):
            if name == logical_package or name.startswith(f"{logical_package}."):
                del sys.modules[name]
        sys.modules.update(saved)


_BOUND_PACKAGE_SOURCES_ATTRIBUTE = "_officina_bound_package_sources"
_BOUND_LOGICAL_PACKAGE_ATTRIBUTE = "_officina_bound_logical_package"
_BOUND_MODULE_ROOT_ATTRIBUTE = "_officina_bound_module_root"


def _bound_module_name(
    physical_path: str,
    logical_package: str | None = None,
) -> tuple[str, bool]:
    path = Path(physical_path)
    if path.suffix != ".py" or path.is_absolute() or ".." in path.parts:
        raise InterfaceLoadError(f"invalid bound package source path: {physical_path}")
    if path.name == "__init__.py":
        parts = path.parent.parts
        is_package = True
    else:
        parts = (*path.parent.parts, path.stem)
        is_package = False
    if not parts:
        if logical_package is None:
            raise InterfaceLoadError(
                f"invalid bound package source path: {physical_path}"
            )
        return logical_package, is_package
    physical_name = ".".join(parts)
    return (
        physical_name
        if logical_package is None
        else f"{logical_package}.{physical_name}"
    ), is_package


def _load_bound_package_sources(
    package_files: Sequence[tuple[int, str]],
    *,
    logical_package: str | None = None,
    physical_package_prefix: str | None = None,
) -> dict[str, tuple[bytes, str, bool]]:
    entries = [
        (_read_bound_source(Path(logical_path), source_fd), logical_path)
        for source_fd, logical_path in package_files
    ]
    return _index_bound_package_sources(
        entries,
        logical_package=logical_package,
        physical_package_prefix=physical_package_prefix,
    )


def _index_bound_package_sources(
    entries: Sequence[tuple[bytes, str]],
    *,
    logical_package: str | None = None,
    physical_package_prefix: str | None = None,
) -> dict[str, tuple[bytes, str, bool]]:
    sources: _BoundPackageSources = _BoundPackageSources()
    for source, logical_path in entries:
        physical_path = Path(logical_path)
        if physical_package_prefix is not None:
            try:
                physical_path = physical_path.relative_to(
                    physical_package_prefix
                )
            except ValueError as exc:
                raise InterfaceLoadError(
                    "bound package source is outside its physical module root: "
                    f"{logical_path}"
                ) from exc
        physical_text = physical_path.as_posix()
        module_name, is_package = _bound_module_name(
            physical_text,
            logical_package,
        )
        if module_name in sources:
            raise InterfaceLoadError(f"duplicate bound package module: {module_name}")
        sources[module_name] = (
            source,
            str(Path(os.path.abspath(physical_text))),
            is_package,
        )
    for module_name in tuple(sources):
        parts = module_name.split(".")
        for index in range(1, len(parts)):
            package_name = ".".join(parts[:index])
            sources.setdefault(
                package_name,
                (b"", package_name.replace(".", "/"), True),
            )
    return sources


def _load_package_snapshot_sources(
    snapshot_path: Path,
    expected_sha256: str,
    *,
    logical_package: str | None = None,
    physical_package_prefix: str | None = None,
) -> dict[str, tuple[bytes, str, bool]]:
    if (
        len(expected_sha256) != 64
        or any(character not in "0123456789abcdef" for character in expected_sha256)
    ):
        raise InterfaceLoadError(
            "package snapshot SHA-256 must be 64 lowercase hexadecimal characters"
        )
    absolute = Path(os.path.abspath(snapshot_path))
    try:
        payload = read_regular_file_bytes(
            absolute,
            allowed_root=absolute.parent,
            allow_non_atomic=False,
        )
    except OSError as exc:
        raise InterfaceLoadError(
            f"could not safely read package snapshot {snapshot_path}: {exc}"
        ) from exc
    actual_sha256 = hashlib.sha256(payload).hexdigest()
    if not hmac.compare_digest(actual_sha256, expected_sha256):
        raise InterfaceLoadError("package snapshot digest mismatch")
    try:
        entries = decode_runtime_python_package_snapshot(payload)
    except BlueprintGraphError as exc:
        raise InterfaceLoadError(str(exc)) from exc
    return _index_bound_package_sources(
        [(source, logical_path) for logical_path, source in entries],
        logical_package=logical_package,
        physical_package_prefix=physical_package_prefix,
    )


@contextmanager
def _bound_package_source_imports(
    sources: dict[str, tuple[bytes, str, bool]],
    *,
    clear_cached: bool = True,
    confined_module_root: Path | None = None,
) -> Iterator[dict[str, tuple[bytes, str, bool]]]:
    """Keep snapshot-only package imports active for one interface lifecycle."""

    roots = {name.partition(".")[0] for name in sources}
    saved_modules = {
        cached_name: module
        for cached_name, module in tuple(sys.modules.items())
        if any(
            cached_name == root or cached_name.startswith(f"{root}.")
            for root in roots
        )
    }
    if clear_cached:
        for cached_name in saved_modules:
            del sys.modules[cached_name]
        trusted_modules = getattr(sources, "trusted_modules", {})
        if isinstance(trusted_modules, dict):
            sys.modules.update(trusted_modules)
    saved_sys_path = list(sys.path)
    if confined_module_root is not None:
        physical_root = confined_module_root.resolve()

        def admits(entry: object) -> bool:
            if not isinstance(entry, str):
                return True
            try:
                candidate = Path(entry or os.curdir).resolve()
            except OSError:
                return True
            try:
                same_root = os.path.samefile(candidate, physical_root)
            except OSError:
                same_root = False
            return not (
                same_root
                or candidate == physical_root
                or candidate.is_relative_to(physical_root)
            )

        sys.path[:] = [entry for entry in sys.path if admits(entry)]
    finder = _BoundPackageFinder(sources)
    sys.meta_path.insert(0, finder)
    try:
        yield sources
    finally:
        if finder in sys.meta_path:
            sys.meta_path.remove(finder)
        if clear_cached:
            trusted_modules = getattr(sources, "trusted_modules", None)
            if isinstance(trusted_modules, dict):
                trusted_modules.clear()
                trusted_modules.update(
                    {
                        cached_name: module
                        for cached_name, module in tuple(sys.modules.items())
                        if cached_name in sources
                    }
                )
            for cached_name in tuple(sys.modules):
                if any(
                    cached_name == root
                    or cached_name.startswith(f"{root}.")
                    for root in roots
                ):
                    del sys.modules[cached_name]
            sys.modules.update(saved_modules)
        if confined_module_root is not None:
            sys.path[:] = saved_sys_path


@contextmanager
def _bound_package_imports(
    package_files: Sequence[tuple[int, str]],
    *,
    logical_package: str | None = None,
    physical_package_prefix: str | None = None,
) -> Iterator[dict[str, tuple[bytes, str, bool]]]:
    sources = _load_bound_package_sources(
        package_files,
        logical_package=logical_package,
        physical_package_prefix=physical_package_prefix,
    )
    with _bound_package_source_imports(
        sources,
        confined_module_root=(
            Path.cwd() if logical_package is not None else None
        ),
    ) as active_sources:
        yield active_sources


def route_smoke_requested(argv: Sequence[str]) -> bool:
    """Return whether argv requests the shared dispatcher route-smoke path.

    This check intentionally happens before normal parser validation so route
    smoke does not need to satisfy interface-specific required arguments.
    """

    return "--route-smoke" in argv


def _read_bound_source(
    path: Path,
    source_fd: int | None,
    *,
    allowed_root: Path | None = None,
) -> bytes:
    """Read Python source from a bound descriptor, opening no-follow if needed."""

    try:
        if source_fd is None:
            root = Path.cwd() if allowed_root is None else allowed_root
            return read_regular_file_bytes(
                Path(os.path.abspath(path)),
                allowed_root=Path(os.path.abspath(root)),
                allow_non_atomic=False,
            )
        metadata = os.fstat(source_fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise InterfaceLoadError(f"interface module is not a regular file: {path}")
        os.lseek(source_fd, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        while chunk := os.read(source_fd, 1024 * 1024):
            chunks.append(chunk)
        return b"".join(chunks)
    except InterfaceLoadError:
        raise
    except OSError as exc:
        raise InterfaceLoadError(f"could not safely read interface module {path}: {exc}") from exc


def _load_confined_package_sources(
    path: Path,
    *,
    logical_package: str | None = None,
) -> dict[str, tuple[bytes, str, bool]] | None:
    """Snapshot the gateway package without following paths outside cwd."""

    root = Path(os.path.abspath(Path.cwd()))
    absolute = Path(os.path.abspath(path))
    try:
        relative = repository_relative_path(absolute, root)
    except RepositoryPathError as exc:
        raise InterfaceLoadError(
            f"interface module is outside allowed root {root}: {path}"
        ) from exc
    if logical_package is None and len(relative.parts) < 2:
        return None

    package_root = (
        root
        if logical_package is not None
        else root / relative.parts[0]
    )
    try:
        snapshots = snapshot_runtime_python_package(
            package_root,
            package_root.parent if logical_package is not None else root,
            package_root.parent if logical_package is not None else root,
            allow_non_atomic=False,
        )
    except BlueprintGraphError as exc:
        raise InterfaceLoadError(str(exc)) from exc

    sources: _BoundPackageSources = _BoundPackageSources()
    for source_path, source in snapshots:
        try:
            logical_path = repository_relative_posix(source_path, root)
        except RepositoryPathError as exc:
            raise InterfaceLoadError(
                f"package source is outside allowed root {root}: {source_path}"
            ) from exc
        module_name, is_package = _bound_module_name(
            logical_path,
            logical_package,
        )
        if module_name in sources:
            raise InterfaceLoadError(f"duplicate bound package module: {module_name}")
        sources[module_name] = (source, str(source_path), is_package)
    for module_name in tuple(sources):
        parts = module_name.split(".")
        for index in range(1, len(parts)):
            package_name = ".".join(parts[:index])
            sources.setdefault(
                package_name,
                (b"", package_name.replace(".", "/"), True),
            )

    entry_name, _is_package = _bound_module_name(
        relative.as_posix(),
        logical_package,
    )
    if entry_name not in sources:
        raise InterfaceLoadError(
            f"interface module is not a regular package source: {path}"
        )
    return sources


def _load_module_from_path(
    path: Path,
    source_fd: int | None = None,
    package_files: Sequence[tuple[int, str]] = (),
    package_sources: dict[str, tuple[bytes, str, bool]] | None = None,
    logical_package: str | None = None,
    logical_entrypoint: str | None = None,
    physical_package_prefix: str | None = None,
) -> ModuleType:
    """Execute a trusted source snapshot with the path's package context."""

    if package_sources is not None:
        try:
            logical_path = repository_relative_posix(path, Path.cwd())
        except RepositoryPathError as exc:
            raise InterfaceLoadError(
                f"interface module is outside the validated package root: {path}"
            ) from exc
        module_name = logical_entrypoint
        if module_name is None:
            module_name, _is_package = _bound_module_name(logical_path)
        if module_name not in package_sources:
            raise InterfaceLoadError(
                f"interface module is outside the validated package snapshot: {path}"
            )
        return importlib.import_module(module_name)

    if package_files:
        with _bound_package_imports(
            package_files,
            logical_package=logical_package,
            physical_package_prefix=physical_package_prefix,
        ) as sources:
            return _load_module_from_path(
                path,
                source_fd,
                package_sources=sources,
                logical_package=logical_package,
                logical_entrypoint=logical_entrypoint,
                physical_package_prefix=physical_package_prefix,
            )

    source = _read_bound_source(path, source_fd)
    module_name = _module_name_for_path(path)
    _clear_conflicting_package_modules(path, module_name)
    module = ModuleType(module_name)
    module.__file__ = str(path)
    module.__package__ = module_name.rpartition(".")[0]
    sys.modules[module_name] = module
    try:
        exec(compile(source, str(path), "exec"), module.__dict__)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


def _clear_conflicting_package_modules(path: Path, module_name: str) -> None:
    """Remove cached package modules that point at a different interface tree."""
    package_parts = module_name.split(".")[:-1]
    if not package_parts:
        return

    current = Path(os.path.abspath(path)).parent
    expected_inits: dict[str, Path] = {}
    for index in range(len(package_parts) - 1, -1, -1):
        package_name = ".".join(package_parts[: index + 1])
        expected_inits[package_name] = current / "__init__.py"
        current = current.parent

    for package_name, expected_init in expected_inits.items():
        module = sys.modules.get(package_name)
        if module is None:
            continue
        module_file = getattr(module, "__file__", None)
        if module_file is None or Path(os.path.abspath(module_file)) != expected_init:
            for cached_name in list(sys.modules):
                if cached_name == package_name or cached_name.startswith(f"{package_name}."):
                    del sys.modules[cached_name]


def _module_name_for_path(path: Path) -> str:
    """Return an import name that preserves package context when available.

    Skill interfaces commonly live under ``_rtx`` and use relative imports.
    When the path is inside a real package directory, use that package's
    dotted name and add its parent to ``sys.path``. Otherwise use an isolated
    synthetic module name for standalone files.
    """

    resolved = Path(os.path.abspath(path))
    package_dir = resolved.parent
    parts = [resolved.stem]
    while (package_dir / "__init__.py").is_file():
        parts.append(package_dir.name)
        package_dir = package_dir.parent
    if len(parts) == 1:
        return f"_officina_machine_interface_{abs(hash(resolved))}"
    package_root = str(package_dir)
    if package_root not in sys.path:
        sys.path.insert(0, package_root)
    return ".".join(reversed(parts))


def load_interface(
    gateway_path: str | Path,
    process_entry: str,
    *,
    source_fd: int | None = None,
    package_files: Sequence[tuple[int, str]] = (),
    logical_package: str | None = None,
    logical_entrypoint: str | None = None,
    physical_package_prefix: str | None = None,
    _package_sources: dict[str, tuple[bytes, str, bool]] | None = None,
    _lazy_confined: bool = False,
) -> PythonMachineInterface:
    """Load a Python machine-interface binding from separate target fields."""

    try:
        target = PythonProcessTarget(
            Path(gateway_path),
            process_entry,
            logical_package=logical_package,
            logical_entrypoint=logical_entrypoint,
        )
    except PythonProcessTargetError as exc:
        raise InterfaceLoadError(str(exc)) from exc
    module_path = target.gateway_path
    if not module_path.is_absolute():
        module_path = Path.cwd() / module_path
    confined_sources = None
    if (
        source_fd is None
        and not package_files
        and _package_sources is None
        and not _lazy_confined
    ):
        confined_sources = _load_confined_package_sources(
            module_path,
            logical_package=target.logical_package,
        )
    active_sources = _package_sources or confined_sources

    def instantiate() -> PythonMachineInterface:
        if _lazy_confined:
            if target.logical_entrypoint is None:
                raise InterfaceLoadError("lazy confined loading requires logical identity")
            module = importlib.import_module(target.logical_entrypoint)
        else:
            module = _load_module_from_path(
                module_path,
                source_fd,
                package_files,
                package_sources=active_sources,
                logical_package=target.logical_package,
                logical_entrypoint=target.logical_entrypoint,
                physical_package_prefix=physical_package_prefix,
            )
        interface_type = getattr(module, target.process_entry, None)
        if interface_type is None:
            raise InterfaceLoadError(
                f"{target.gateway_path}: class "
                f"`{target.process_entry}` not found"
            )
        interface = interface_type()
        if not isinstance(interface, PythonMachineInterface):
            raise InterfaceLoadError(
                f"{target.gateway_path}: class must inherit "
                "PythonMachineInterface"
            )
        if active_sources:
            setattr(interface, _BOUND_PACKAGE_SOURCES_ATTRIBUTE, active_sources)
            if target.logical_package is not None:
                setattr(
                    interface,
                    _BOUND_LOGICAL_PACKAGE_ATTRIBUTE,
                    target.logical_package,
                )
                setattr(
                    interface,
                    _BOUND_MODULE_ROOT_ATTRIBUTE,
                    Path.cwd().resolve(),
                )
        return interface

    if active_sources is None:
        return instantiate()
    with _bound_package_source_imports(
        active_sources,
        confined_module_root=(
            Path.cwd() if target.logical_package is not None else None
        ),
    ):
        return instantiate()


def run_python_machine_interface(interface: PythonMachineInterface, argv: Sequence[str]) -> int:
    """Run one loaded Python machine interface through the standard lifecycle.

    Lifecycle:
    1. Build the interface-owned parser.
    2. If ``--route-smoke`` is present, call ``interface.route_smoke()`` and
       exit before normal argument parsing or real execution.
    3. Otherwise parse arguments and call ``interface.run(args)``.
    """

    def run() -> int:
        parser = interface.build_parser()
        if not isinstance(parser, argparse.ArgumentParser):
            raise TypeError("build_parser() must return argparse.ArgumentParser")
        if route_smoke_requested(argv):
            interface.route_smoke()
            print("route-smoke ok")
            return 0
        args = interface.parse_args(parser, list(argv))
        return coerce_exit_code(interface.run(args))

    sources = getattr(interface, _BOUND_PACKAGE_SOURCES_ATTRIBUTE, None)
    if not isinstance(sources, dict) or not sources:
        return run()
    logical_package = getattr(
        interface,
        _BOUND_LOGICAL_PACKAGE_ATTRIBUTE,
        None,
    )
    module_root = getattr(interface, _BOUND_MODULE_ROOT_ATTRIBUTE, None)
    with _bound_package_source_imports(
        sources,
        confined_module_root=(
            Path(module_root)
            if isinstance(logical_package, str)
            and isinstance(module_root, Path)
            else None
        ),
    ):
        return run()


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint used by dispatcher command runtimes.

    Expected argv shape:
        ``<gateway-path> <process-entry> [interface/default args...] [caller args...]``

    Example:
        ``_rtx/_lists.py ReadListInterface --list todo``
    """

    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(
            "error: missing Python gateway path or process entry",
            file=sys.stderr,
        )
        return 2
    source_fd: int | None = None
    package_files: list[tuple[int, str]] = []
    package_snapshot: Path | None = None
    package_snapshot_sha256: str | None = None
    logical_package: str | None = None
    logical_entrypoint: str | None = None
    physical_package_prefix: str | None = None
    runtime_caller_module_id: str | None = None
    runtime_caller_source_id: str | None = None
    runtime_repo_root: Path | None = None
    runtime_repository_config: Path | None = None
    confined_module_root: Path | None = None
    private_options = {
        "--source-fd",
        "--package-file",
        "--package-snapshot",
        "--package-snapshot-sha256",
        "--logical-package",
        "--logical-entrypoint",
        "--physical-package-prefix",
        "--runtime-caller-module-id",
        "--runtime-caller-source-id",
        "--runtime-repo-root",
        "--runtime-repository-config",
        "--confined-module-root",
    }
    while argv and argv[0] in private_options:
        option = argv.pop(0)
        required = 2 if option == "--package-file" else 1
        if len(argv) < required:
            print(f"error: {option} is missing required arguments", file=sys.stderr)
            return 2
        if option == "--package-snapshot":
            if package_snapshot is not None:
                print(f"error: duplicate {option}", file=sys.stderr)
                return 2
            package_snapshot = Path(argv.pop(0))
            continue
        if option == "--package-snapshot-sha256":
            if package_snapshot_sha256 is not None:
                print(f"error: duplicate {option}", file=sys.stderr)
                return 2
            package_snapshot_sha256 = argv.pop(0)
            continue
        if option == "--logical-package":
            if logical_package is not None:
                print(f"error: duplicate {option}", file=sys.stderr)
                return 2
            logical_package = argv.pop(0)
            continue
        if option == "--logical-entrypoint":
            if logical_entrypoint is not None:
                print(f"error: duplicate {option}", file=sys.stderr)
                return 2
            logical_entrypoint = argv.pop(0)
            continue
        if option == "--physical-package-prefix":
            if physical_package_prefix is not None:
                print(f"error: duplicate {option}", file=sys.stderr)
                return 2
            physical_package_prefix = argv.pop(0)
            continue
        if option == "--runtime-caller-module-id":
            if runtime_caller_module_id is not None:
                print(f"error: duplicate {option}", file=sys.stderr)
                return 2
            runtime_caller_module_id = argv.pop(0)
            continue
        if option == "--runtime-caller-source-id":
            if runtime_caller_source_id is not None:
                print(f"error: duplicate {option}", file=sys.stderr)
                return 2
            runtime_caller_source_id = argv.pop(0)
            continue
        if option == "--runtime-repo-root":
            if runtime_repo_root is not None:
                print(f"error: duplicate {option}", file=sys.stderr)
                return 2
            runtime_repo_root = Path(argv.pop(0)).resolve()
            continue
        if option == "--runtime-repository-config":
            if runtime_repository_config is not None:
                print("error: duplicate runtime repository config", file=sys.stderr)
                return 2
            runtime_repository_config = Path(argv.pop(0))
            continue
        if option == "--confined-module-root":
            if confined_module_root is not None:
                print("error: duplicate confined module root", file=sys.stderr)
                return 2
            confined_module_root = Path(argv.pop(0))
            continue
        try:
            descriptor = int(argv.pop(0))
        except ValueError:
            print(f"error: {option} descriptor must be an integer", file=sys.stderr)
            return 2
        if option == "--source-fd":
            source_fd = descriptor
        else:
            package_files.append((descriptor, argv.pop(0)))
    if (package_snapshot is None) != (package_snapshot_sha256 is None):
        print(
            "error: package snapshot path and SHA-256 must be provided together",
            file=sys.stderr,
        )
        return 2
    if (logical_package is None) != (logical_entrypoint is None):
        print(
            "error: logical package and entrypoint must be provided together",
            file=sys.stderr,
        )
        return 2
    if physical_package_prefix is not None and logical_package is None:
        print(
            "error: physical package prefix requires a logical package",
            file=sys.stderr,
        )
        return 2
    if physical_package_prefix is not None and (
        not physical_package_prefix
        or Path(physical_package_prefix).name != physical_package_prefix
        or physical_package_prefix in {".", ".."}
    ):
        print("error: invalid physical package prefix", file=sys.stderr)
        return 2
    if package_snapshot is not None and (source_fd is not None or package_files):
        print(
            "error: package snapshot transport cannot be combined with descriptors",
            file=sys.stderr,
        )
        return 2
    if len(argv) < 2:
        print("error: missing Python gateway path or process entry", file=sys.stderr)
        return 2
    gateway_path, process_entry, *interface_argv = argv

    def run_loaded_interface(interface: PythonMachineInterface) -> int:
        set_runtime_dispatch_context(
            interface,
            caller_module_id=runtime_caller_module_id,
            caller_source_id=runtime_caller_source_id,
            repo_root=runtime_repo_root,
            repository_config=runtime_repository_config,
        )
        return run_python_machine_interface(interface, interface_argv)

    try:
        if package_snapshot is not None:
            assert package_snapshot_sha256 is not None
            sources = _load_package_snapshot_sources(
                package_snapshot,
                package_snapshot_sha256,
                logical_package=logical_package,
                physical_package_prefix=physical_package_prefix,
            )
            with _bound_package_source_imports(
                sources,
                confined_module_root=(
                    Path.cwd() if logical_package is not None else None
                ),
            ):
                interface = load_interface(
                    gateway_path,
                    process_entry,
                    logical_package=logical_package,
                    logical_entrypoint=logical_entrypoint,
                    physical_package_prefix=physical_package_prefix,
                    _package_sources=sources,
                )
                return run_loaded_interface(interface)
        if package_files:
            with _bound_package_imports(
                package_files,
                logical_package=logical_package,
                physical_package_prefix=physical_package_prefix,
            ) as sources:
                interface = load_interface(
                    gateway_path,
                    process_entry,
                    source_fd=source_fd,
                    logical_package=logical_package,
                    logical_entrypoint=logical_entrypoint,
                    physical_package_prefix=physical_package_prefix,
                    _package_sources=sources,
                )
                return run_loaded_interface(interface)
        if confined_module_root is not None:
            if logical_package is None:
                print("error: confined module root requires logical package", file=sys.stderr)
                return 2
            if confined_module_root.resolve() != Path.cwd().resolve():
                print("error: confined module root must equal cwd", file=sys.stderr)
                return 2
            with _lazy_confined_package_imports(
                confined_module_root,
                logical_package,
            ):
                interface = load_interface(
                    gateway_path,
                    process_entry,
                    logical_package=logical_package,
                    logical_entrypoint=logical_entrypoint,
                    physical_package_prefix=physical_package_prefix,
                    _lazy_confined=True,
                )
                return run_loaded_interface(interface)
        interface = load_interface(
            gateway_path,
            process_entry,
            source_fd=source_fd,
            logical_package=logical_package,
            logical_entrypoint=logical_entrypoint,
        )
        return run_loaded_interface(interface)
    except InterfaceLoadError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
