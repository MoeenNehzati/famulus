"""Generic runner for Python dispatcher machine-interface bindings."""
from __future__ import annotations

import argparse
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
    sources: dict[str, tuple[bytes, str, bool]] = {}
    for source_fd, logical_path in package_files:
        module_name, is_package = _bound_module_name(logical_path)
        if module_name in sources:
            raise InterfaceLoadError(f"duplicate bound package module: {module_name}")
        source = _read_bound_source(Path(logical_path), source_fd)
        sources[module_name] = (source, logical_path, is_package)
    for module_name in tuple(sources):
        parts = module_name.split(".")
        for index in range(1, len(parts)):
            package_name = ".".join(parts[:index])
            sources.setdefault(
                package_name,
                (b"", package_name.replace(".", "/"), True),
            )
    return sources


@contextmanager
def _bound_package_imports(
    package_files: Sequence[tuple[int, str]],
) -> Iterator[dict[str, tuple[bytes, str, bool]]]:
    """Keep snapshot-only package imports active for one interface lifecycle."""

    sources = _load_bound_package_sources(package_files)
    roots = {name.partition(".")[0] for name in sources}
    for cached_name in list(sys.modules):
        if any(cached_name == root or cached_name.startswith(f"{root}.") for root in roots):
            del sys.modules[cached_name]
    finder = _BoundPackageFinder(sources)
    sys.meta_path.insert(0, finder)
    try:
        yield sources
    finally:
        if finder in sys.meta_path:
            sys.meta_path.remove(finder)


def route_smoke_requested(argv: Sequence[str]) -> bool:
    """Return whether argv requests the shared dispatcher route-smoke path.

    This check intentionally happens before normal parser validation so route
    smoke does not need to satisfy interface-specific required arguments.
    """

    return "--route-smoke" in argv


def _read_bound_source(path: Path, source_fd: int | None) -> bytes:
    """Read Python source from a bound descriptor, opening no-follow if needed."""

    owned_fd = -1
    current_fd = -1
    try:
        if source_fd is None:
            if (
                os.name != "posix"
                or not hasattr(os, "O_NOFOLLOW")
                or not hasattr(os, "O_DIRECTORY")
                or os.open not in os.supports_dir_fd
            ):
                raise InterfaceLoadError(
                    f"descriptor-safe interface loading is unavailable: {path}"
                )
            absolute = Path(os.path.abspath(path))
            file_flags = (
                os.O_RDONLY
                | os.O_NOFOLLOW
                | os.O_NONBLOCK
                | getattr(os, "O_CLOEXEC", 0)
            )
            directory_flags = file_flags | os.O_DIRECTORY
            current_fd = os.open("/", directory_flags)
            for index, component in enumerate(absolute.parts[1:]):
                final = index == len(absolute.parts[1:]) - 1
                next_fd = os.open(
                    component,
                    file_flags if final else directory_flags,
                    dir_fd=current_fd,
                )
                os.close(current_fd)
                current_fd = next_fd
            owned_fd = current_fd
            current_fd = -1
            source_fd = owned_fd
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
    finally:
        if current_fd >= 0:
            os.close(current_fd)
        if owned_fd >= 0:
            os.close(owned_fd)


def _load_module_from_path(
    path: Path,
    source_fd: int | None = None,
    package_files: Sequence[tuple[int, str]] = (),
    package_sources: dict[str, tuple[bytes, str, bool]] | None = None,
) -> ModuleType:
    """Execute a trusted source snapshot with the path's package context."""

    if package_sources is not None:
        logical_path = path.relative_to(Path.cwd()).as_posix()
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

    module_text, sep, class_name = spec.partition(":")
    if sep != ":" or not module_text or not class_name:
        raise InterfaceLoadError("interface spec must be `path/to/file.py:ClassName`")
    module_path = Path(module_text)
    if not module_path.is_absolute():
        module_path = Path.cwd() / module_path
    module = _load_module_from_path(
        module_path,
        source_fd,
        package_files,
        package_sources=_package_sources,
    )
    interface_type = getattr(module, class_name, None)
    if interface_type is None:
        raise InterfaceLoadError(f"{spec}: class `{class_name}` not found")
    interface = interface_type()
    if not isinstance(interface, PythonMachineInterface):
        raise InterfaceLoadError(f"{spec}: class must inherit PythonMachineInterface")
    return interface


def run_python_machine_interface(interface: PythonMachineInterface, argv: Sequence[str]) -> int:
    """Run one loaded Python machine interface through the standard lifecycle.

    Lifecycle:
    1. Build the interface-owned parser.
    2. If ``--route-smoke`` is present, call ``interface.route_smoke()`` and
       exit before normal argument parsing or real execution.
    3. Otherwise parse arguments and call ``interface.run(args)``.
    """

    parser = interface.build_parser()
    if not isinstance(parser, argparse.ArgumentParser):
        raise TypeError("build_parser() must return argparse.ArgumentParser")
    if route_smoke_requested(argv):
        interface.route_smoke()
        print("route-smoke ok")
        return 0
    args = interface.parse_args(parser, list(argv))
    return coerce_exit_code(interface.run(args))


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
    while argv and argv[0] in {"--source-fd", "--package-file"}:
        option = argv.pop(0)
        required = 1 if option == "--source-fd" else 2
        if len(argv) < required:
            print(f"error: {option} is missing required arguments", file=sys.stderr)
            return 2
        try:
            descriptor = int(argv.pop(0))
        except ValueError:
            print(f"error: {option} descriptor must be an integer", file=sys.stderr)
            return 2
        if option == "--source-fd":
            source_fd = descriptor
        else:
            package_files.append((descriptor, argv.pop(0)))
    if not argv:
        print("error: missing interface spec", file=sys.stderr)
        return 2
    spec, *interface_argv = argv
    try:
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
