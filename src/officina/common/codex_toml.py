"""Codex-specific facade over the shared TOML transaction boundary."""
from __future__ import annotations

from pathlib import Path
from typing import Mapping

from . import toml_io


_TABLE = "sandbox_workspace_write"
_KEY = "writable_roots"

# Stable facade exception for callers that must not depend on TOML-boundary internals.
CodexTomlError = toml_io.TomlManagedArrayError


def config_filename() -> str:
    """Return the Codex user config filename."""
    return "config.toml"


def config_path(base: Path | str) -> Path:
    """Return the canonical Codex user configuration path."""
    return Path(base) / config_filename()


def config_state(base: Path | str) -> toml_io.TomlFileState:
    """Return content and mode identity without exposing configuration bytes."""
    return toml_io.managed_file_state(base, config_filename())


def plan_access_roots(
    base: Path | str,
    required: list[str],
    *,
    prior: Mapping[str, object] | None,
    begin: str,
    end: str,
) -> toml_io.ManagedArrayPlan:
    """Plan a structure-preserving Codex writable-root reconciliation."""
    return toml_io.plan_managed_string_array_update(
        base,
        config_filename(),
        table_name=_TABLE,
        key_name=_KEY,
        required=required,
        prior=prior,
        begin=begin,
        end=end,
    )


def plan_access_removal(
    base: Path | str, *, ownership: Mapping[str, object]
) -> toml_io.ManagedArrayPlan:
    """Plan identity-proven removal of the managed Codex writable roots."""
    return toml_io.plan_managed_string_array_removal(
        base,
        config_filename(),
        table_name=_TABLE,
        key_name=_KEY,
        ownership=ownership,
    )


def inspect_access_roots(
    base: Path | str, *, begin: str, end: str
) -> toml_io.ManagedArrayInspection:
    """Inspect managed Codex writable roots without mutation."""
    return toml_io.inspect_managed_string_array(
        base,
        config_filename(),
        table_name=_TABLE,
        key_name=_KEY,
        begin=begin,
        end=end,
    )


def apply_access_plan(plan: toml_io.ManagedArrayPlan) -> None:
    """Apply a previously planned Codex transition if its pre-state remains exact."""
    toml_io.apply_managed_array_plan(plan)
