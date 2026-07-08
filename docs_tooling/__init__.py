"""Shared documentation generation and validation support."""

from .catalog import COVERAGE_BLOCKS, SKILL_INDEX_PATH, SkillInfo, load_catalog
from .render import generate_all, render_doc_with_updated_blocks, render_skill_index

__all__ = [
    "COVERAGE_BLOCKS",
    "SKILL_INDEX_PATH",
    "SkillInfo",
    "generate_all",
    "load_catalog",
    "render_doc_with_updated_blocks",
    "render_skill_index",
]
