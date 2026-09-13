"""Shared installed-skill source discovery helpers."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SkillSource:
    """One installed skills root plus the package root used for labels/hashes."""

    source: str
    package_root: Path
    skills_root: Path
    plugin_id: str | None = None
    plugin_version: str | None = None


class SkillSourceDiscoveryError(ValueError):
    """Raised when a host's authoritative installed-source state is invalid."""


def host_skill_sources(host: str, home: Path) -> list[SkillSource]:
    """Return only the direct skills root below one host home."""

    sources: list[SkillSource] = []
    direct = home / "skills"
    if direct.is_dir():
        skills_root = direct.resolve()
        sources.append(SkillSource(source=host, package_root=skills_root.parent, skills_root=skills_root))

    return sources


def any_skill_dir(skills_root: Path) -> bool:
    """Return whether a directory looks like a skills root."""

    return any(path.is_dir() and (path / "SKILL.md").is_file() for path in skills_root.iterdir())


def current_skill_source() -> SkillSource | None:
    """Return the dispatcher cwd skill root when running from an installed skill."""

    cwd = Path.cwd().resolve()
    if (cwd / "SKILL.md").is_file() and cwd.parent.name == "skills":
        return SkillSource(source="current", package_root=cwd.parent.parent, skills_root=cwd.parent)
    return None


def dedupe_skill_sources(sources: list[SkillSource]) -> list[SkillSource]:
    """Deduplicate installed roots while preserving discovery order."""

    seen: dict[Path, SkillSource] = {}
    result: list[SkillSource] = []
    for source in sources:
        key = source.skills_root.resolve()
        if key in seen:
            previous = seen[key]
            if (
                previous.plugin_id,
                previous.plugin_version,
            ) != (
                source.plugin_id,
                source.plugin_version,
            ):
                raise SkillSourceDiscoveryError(
                    "installed source metadata conflict for "
                    f"{key}: {previous.plugin_id}@{previous.plugin_version} and "
                    f"{source.plugin_id}@{source.plugin_version}"
                )
            continue
        seen[key] = source
        result.append(source)
    return result
