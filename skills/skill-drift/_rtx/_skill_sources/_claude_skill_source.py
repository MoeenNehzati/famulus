"""Claude installed-skill source discovery."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ._skill_source_common import (
    SkillSource,
    SkillSourceDiscoveryError,
    any_skill_dir,
    host_skill_sources,
)


_REMEDIATION = (
    "repair installed_plugins.json or pass --skill-root, --skills-root, "
    "or --repo-root for the exact intended installation"
)


def _registry_error(path: Path, detail: str, record: object | None = None) -> SkillSourceDiscoveryError:
    record_detail = ""
    if record is not None:
        record_detail = "; record=" + json.dumps(
            record, sort_keys=True, ensure_ascii=False, default=str
        )
    return SkillSourceDiscoveryError(
        f"{path}: {detail}{record_detail}; {_REMEDIATION}"
    )


def _plugin_sources(home: Path) -> list[SkillSource]:
    registry_path = home / "plugins" / "installed_plugins.json"
    if not registry_path.exists():
        return []
    try:
        registry: Any = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise _registry_error(registry_path, f"cannot read version-2 registry: {exc}") from exc
    if not isinstance(registry, dict) or registry.get("version") != 2:
        raise _registry_error(registry_path, "unsupported registry version", registry)
    plugins = registry.get("plugins")
    if not isinstance(plugins, dict):
        raise _registry_error(registry_path, "/plugins must be a mapping", plugins)

    sources: list[SkillSource] = []
    for plugin_id, records in plugins.items():
        if not isinstance(plugin_id, str) or not plugin_id or not isinstance(records, list):
            raise _registry_error(
                registry_path,
                "/plugins entries must map nonempty plugin IDs to arrays",
                {str(plugin_id): records},
            )
        for index, record in enumerate(records):
            if not isinstance(record, dict):
                raise _registry_error(
                    registry_path,
                    f"/plugins/{plugin_id}/{index} must be an object",
                    record,
                )
            version = record.get("version")
            if not isinstance(version, str) or not version:
                raise _registry_error(
                    registry_path,
                    f"/plugins/{plugin_id}/{index}/version must be a nonempty string",
                    record,
                )
            install_path = record.get("installPath")
            if not isinstance(install_path, str) or not install_path:
                raise _registry_error(
                    registry_path,
                    f"/plugins/{plugin_id}/{index}/installPath must be a nonempty string",
                    record,
                )
            install_root = Path(install_path).expanduser()
            if not install_root.is_absolute():
                raise _registry_error(
                    registry_path,
                    f"/plugins/{plugin_id}/{index}/installPath must be absolute",
                    record,
                )
            skills_root = install_root / "skills"
            if (
                not install_root.is_dir()
                or not skills_root.is_dir()
                or not any_skill_dir(skills_root)
            ):
                raise _registry_error(
                    registry_path,
                    f"/plugins/{plugin_id}/{index}/installPath does not resolve to a plugin with skills",
                    record,
                )
            sources.append(
                SkillSource(
                    source="claude",
                    package_root=install_root.resolve(),
                    skills_root=skills_root.resolve(),
                    plugin_id=plugin_id,
                    plugin_version=version,
                )
            )
    return sources


def sources() -> list[SkillSource]:
    """Return Claude direct skills and registry-named active plugin roots."""

    home = Path(os.environ.get("CLAUDE_HOME", "~/.claude")).expanduser()
    return [*host_skill_sources("claude", home), *_plugin_sources(home)]
