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
    repository_relative_path,
    snapshot_runtime_python_package,
)

from .python_machine_interface import PythonMachineInterface, coerce_exit_code


class InterfaceLoadError(RuntimeError):
    """Raised when a Python machine-interface binding cannot be loaded."""


class _BoundPackageFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Load one package namespace only from dispatcher-bound source snapshots."""

    def __init__(self, sources: dict[str, tuple[bytes, str, bool]]) -> None:
        self.sources = sources
        self.roots = {name.partition(".")[0] for name in sources}

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
        module.__file__ = logical_path
        if is_package:
            module.__path__ = []
        exec(compile(source, logical_path, "exec"), module.__dict__)


_BOUND_PACKAGE_SOURCES_ATTRIBUTE = "_officina_bound_package_sources"


def _bound_module_name(logical_path: str) -> tuple[str, bool]:
    path = Path(logical_path)
    if path.suffix != ".py" or path.is_absolute() or ".." in path.parts:
        raise InterfaceLoadError(f"invalid bound package source path: {logical_path}")
    if path.name == "__init__.py":
        parts = path.parent.parts
        is_package = True
    else:
        parts = (*path.parent.parts, path.stem)
        is_package = False
    if not parts:
        raise InterfaceLoadError(f"invalid bound package source path: {logical_path}")
    return ".".join(parts), is_package


def _load_bound_package_sources(
    package_files: Sequence[tuple[int, str]],
) -> dict[str, tuple[bytes, str, bool]]:
    entries = [
        (_read_bound_source(Path(logical_path), source_fd), logical_path)
        for source_fd, logical_path in package_files
    ]
    return _index_bound_package_sources(entries)


def _index_bound_package_sources(
    entries: Sequence[tuple[bytes, str]],
) -> dict[str, tuple[bytes, str, bool]]:
    sources: dict[str, tuple[bytes, str, bool]] = {}
    for source, logical_path in entries:
        module_name, is_package = _bound_module_name(logical_path)
        if module_name in sources:
            raise InterfaceLoadError(f"duplicate bound package module: {module_name}")
        sources[module_name] = (
            source,
            str(Path(os.path.abspath(logical_path))),
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
        [(source, logical_path) for logical_path, source in entries]
    )


@contextmanager
def _bound_package_source_imports(
    sources: dict[str, tuple[bytes, str, bool]],
    *,
    clear_cached: bool = True,
) -> Iterator[dict[str, tuple[bytes, str, bool]]]:
    """Keep snapshot-only package imports active for one interface lifecycle."""

    roots = {name.partition(".")[0] for name in sources}
    if clear_cached:
        for cached_name in list(sys.modules):
            if any(
                cached_name == root or cached_name.startswith(f"{root}.")
                for root in roots
            ):
                del sys.modules[cached_name]
    finder = _BoundPackageFinder(sources)
    sys.meta_path.insert(0, finder)
    try:
        yield sources
    finally:
        if finder in sys.meta_path:
            sys.meta_path.remove(finder)


@contextmanager
def _bound_package_imports(
    package_files: Sequence[tuple[int, str]],
) -> Iterator[dict[str, tuple[bytes, str, bool]]]:
    sources = _load_bound_package_sources(package_files)
    with _bound_package_source_imports(sources) as active_sources:
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
) -> dict[str, tuple[bytes, str, bool]] | None:
    """Snapshot the entrypoint package without following paths outside cwd."""

    root = Path(os.path.abspath(Path.cwd()))
    absolute = Path(os.path.abspath(path))
    try:
        relative = repository_relative_path(absolute, root)
    except BlueprintGraphError as exc:
        raise InterfaceLoadError(
            f"interface module is outside allowed root {root}: {path}"
        ) from exc
    if len(relative.parts) < 2:
        return None

    package_root = root / relative.parts[0]
    try:
        snapshots = snapshot_runtime_python_package(
            package_root,
            root,
            root,
            allow_non_atomic=False,
        )
    except BlueprintGraphError as exc:
        raise InterfaceLoadError(str(exc)) from exc

    sources: dict[str, tuple[bytes, str, bool]] = {}
    for source_path, source in snapshots:
        logical_path = source_path.relative_to(root).as_posix()
        module_name, is_package = _bound_module_name(logical_path)
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

    entry_name, _is_package = _bound_module_name(relative.as_posix())
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
) -> ModuleType:
    """Execute a trusted source snapshot with the path's package context."""

    if package_sources is not None:
        try:
            logical_path = repository_relative_path(path, Path.cwd()).as_posix()
        except BlueprintGraphError as exc:
            raise InterfaceLoadError(
                f"interface module is outside the validated package root: {path}"
            ) from exc
        module_name, _is_package = _bound_module_name(logical_path)
        if module_name not in package_sources:
            raise InterfaceLoadError(
                f"interface module is outside the validated package snapshot: {path}"
            )
        return importlib.import_module(module_name)

    if package_files:
        with _bound_package_imports(package_files) as sources:
            return _load_module_from_path(
                path,
                source_fd,
                package_sources=sources,
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
    spec: str,
    *,
    source_fd: int | None = None,
    package_files: Sequence[tuple[int, str]] = (),
    _package_sources: dict[str, tuple[bytes, str, bool]] | None = None,
) -> PythonMachineInterface:
    """Load and instantiate a Python machine-interface binding.

    ``spec`` has the form ``path/to/module.py:ClassName``. Relative paths are
    resolved from the current working directory, which is the skill root for
    dispatcher command runtimes.
    """

    module_text, sep, class_name = spec.rpartition(":")
    if sep != ":" or not module_text or not class_name:
        raise InterfaceLoadError("interface spec must be `path/to/file.py:ClassName`")
    module_path = Path(module_text)
    if not module_path.is_absolute():
        module_path = Path.cwd() / module_path
    confined_sources = None
    if source_fd is None and not package_files and _package_sources is None:
        confined_sources = _load_confined_package_sources(module_path)
    active_sources = _package_sources or confined_sources

    def instantiate() -> PythonMachineInterface:
        module = _load_module_from_path(
            module_path,
            source_fd,
            package_files,
            package_sources=active_sources,
        )
        interface_type = getattr(module, class_name, None)
        if interface_type is None:
            raise InterfaceLoadError(f"{spec}: class `{class_name}` not found")
        interface = interface_type()
        if not isinstance(interface, PythonMachineInterface):
            raise InterfaceLoadError(f"{spec}: class must inherit PythonMachineInterface")
        if active_sources:
            setattr(interface, _BOUND_PACKAGE_SOURCES_ATTRIBUTE, active_sources)
        return interface

    if active_sources is None:
        return instantiate()
    with _bound_package_source_imports(active_sources):
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
    with _bound_package_source_imports(sources, clear_cached=False):
        return run()


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint used by dispatcher command runtimes.

    Expected argv shape:
        ``<entrypoint-spec> [interface/default args...] [caller args...]``

    Example:
        ``_rtx/_lists.py:ReadListInterface --list todo``
    """

    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("error: missing interface spec", file=sys.stderr)
        return 2
    source_fd: int | None = None
    package_files: list[tuple[int, str]] = []
    package_snapshot: Path | None = None
    package_snapshot_sha256: str | None = None
    private_options = {
        "--source-fd",
        "--package-file",
        "--package-snapshot",
        "--package-snapshot-sha256",
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
    if package_snapshot is not None and (source_fd is not None or package_files):
        print(
            "error: package snapshot transport cannot be combined with descriptors",
            file=sys.stderr,
        )
        return 2
    if not argv:
        print("error: missing interface spec", file=sys.stderr)
        return 2
    spec, *interface_argv = argv
    try:
        if package_snapshot is not None:
            assert package_snapshot_sha256 is not None
            sources = _load_package_snapshot_sources(
                package_snapshot,
                package_snapshot_sha256,
            )
            with _bound_package_source_imports(sources):
                interface = load_interface(spec, _package_sources=sources)
                return run_python_machine_interface(interface, interface_argv)
        if package_files:
            with _bound_package_imports(package_files) as sources:
                interface = load_interface(
                    spec,
                    source_fd=source_fd,
                    _package_sources=sources,
                )
                return run_python_machine_interface(interface, interface_argv)
        interface = load_interface(
            spec,
            source_fd=source_fd,
        )
        return run_python_machine_interface(interface, interface_argv)
    except InterfaceLoadError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
