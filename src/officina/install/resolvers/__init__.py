"""Public API and deployment metadata for the standalone resolver."""

from pathlib import Path

from .launch import (
    ResolverError,
    _load_current_pointer,
    _require_contained_or_trusted,
    _trusted_interpreter_roots,
    main,
)

__all__ = ["ResolverError", "main"]


def resolver_source_bundle() -> dict[str, Path]:
    """Return the dependency-free resolver implementation source."""
    root = Path(__file__).resolve().parent
    return {"launch.py": root / "launch.py"}
