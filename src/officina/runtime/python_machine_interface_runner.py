"""Generic runner for Python dispatcher machine-interface bindings."""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Sequence

from .python_machine_interface import PythonMachineInterface, coerce_exit_code


class InterfaceLoadError(RuntimeError):
    """Raised when a Python machine-interface binding cannot be loaded."""


def route_smoke_requested(argv: Sequence[str]) -> bool:
    """Return whether argv requests the shared dispatcher route-smoke path.

    This check intentionally happens before normal parser validation so route
    smoke does not need to satisfy interface-specific required arguments.
    """

    return "--route-smoke" in argv


def _load_module_from_path(path: Path) -> ModuleType:
    """Import a Python module from an explicit filesystem path."""

    if not path.is_file():
        raise InterfaceLoadError(f"interface module not found: {path}")
    module_name = _module_name_for_path(path)
    _clear_conflicting_package_modules(path, module_name)
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise InterfaceLoadError(f"could not load interface module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _clear_conflicting_package_modules(path: Path, module_name: str) -> None:
    """Remove cached package modules that point at a different interface tree."""
    package_parts = module_name.split(".")[:-1]
    if not package_parts:
        return

    current = path.resolve().parent
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
        if module_file is None or Path(module_file).resolve() != expected_init:
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

    resolved = path.resolve()
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


def load_interface(spec: str) -> PythonMachineInterface:
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
    module = _load_module_from_path(module_path)
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
    spec, *interface_argv = argv
    try:
        interface = load_interface(spec)
        return run_python_machine_interface(interface, interface_argv)
    except InterfaceLoadError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
