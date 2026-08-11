"""Materialize non-live version 5 blueprint fixture templates."""

from __future__ import annotations

from pathlib import Path
import shutil


def copy_v5_fixture_tree(source: Path, destination: Path) -> Path:
    """Copy a shadow fixture and activate only its copied module markers."""

    shutil.copytree(source, destination)
    templates = tuple(sorted(destination.rglob("blueprint.v5.yaml")))
    if not templates:
        raise ValueError(f"{source}: no version 5 blueprint templates")
    for template in templates:
        marker = template.with_name("blueprint.yaml")
        if marker.exists():
            raise FileExistsError(f"{marker}: fixture marker already exists")
        template.rename(marker)
    return destination
