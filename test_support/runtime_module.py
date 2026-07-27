"""Test-only loading for private runtime modules with package-relative imports."""
from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.util
import os
from pathlib import Path
import sys
from types import ModuleType
from typing import Sequence

def load_runtime_module(path: Path) -> ModuleType:
    """Load one live target under a collision-free logical package name."""

    resolved = path.resolve()
    package_root = resolved.parent
    while (package_root.parent / "__init__.py").is_file():
        package_root = package_root.parent
    digest = hashlib.sha256(os.fsencode(package_root)).hexdigest()[:16]
    logical_package = f"_officina_test_runtime_{digest}"
    if logical_package not in sys.modules:
        package_init = package_root / "__init__.py"
        if package_init.is_file():
            package_spec = importlib.util.spec_from_file_location(
                logical_package,
                package_init,
                submodule_search_locations=[str(package_root)],
            )
            if package_spec is None or package_spec.loader is None:
                raise ImportError(f"could not load runtime package {package_root}")
            package_module = importlib.util.module_from_spec(package_spec)
            sys.modules[logical_package] = package_module
            try:
                package_spec.loader.exec_module(package_module)
            except Exception:
                sys.modules.pop(logical_package, None)
                raise
        else:
            package_module = ModuleType(logical_package)
            package_module.__package__ = logical_package
            package_module.__path__ = [str(package_root)]  # type: ignore[attr-defined]
            sys.modules[logical_package] = package_module

    relative = resolved.relative_to(package_root)
    module_parts = list(relative.with_suffix("").parts)
    if module_parts[-1] == "__init__":
        module_parts.pop()
    if not module_parts:
        return sys.modules[logical_package]
    for depth in range(1, len(module_parts)):
        importlib.import_module(
            ".".join([logical_package, *module_parts[:depth]])
        )
    module_name = ".".join([logical_package, *module_parts])
    module_spec = importlib.util.spec_from_file_location(module_name, resolved)
    if module_spec is None or module_spec.loader is None:
        raise ImportError(f"could not load runtime module {resolved}")
    module = importlib.util.module_from_spec(module_spec)
    sys.modules[module_name] = module
    saved_physical_modules = {
        name: cached
        for name, cached in tuple(sys.modules.items())
        if name == "_rtx" or name.startswith("_rtx.")
    }
    for name in saved_physical_modules:
        del sys.modules[name]
    sys.modules["_rtx"] = sys.modules[logical_package]
    try:
        module_spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    finally:
        for name in tuple(sys.modules):
            if name == "_rtx" or name.startswith("_rtx."):
                del sys.modules[name]
        sys.modules.update(saved_physical_modules)
    return module


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    forwarded = list(args.arguments)
    if forwarded[:1] == ["--"]:
        forwarded.pop(0)
    module = load_runtime_module(args.path)
    entry = getattr(module, "main", None)
    if not callable(entry):
        parser.error(f"{args.path}: runtime test target has no callable main")
    result = entry(forwarded)
    return result if isinstance(result, int) else 0


if __name__ == "__main__":
    raise SystemExit(main())
