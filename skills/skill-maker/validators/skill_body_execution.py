"""Reject executable-file references used as execution instructions in SKILL.md."""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from validators.skill_md_body import hand_authored_skill_body  # noqa: E402


EXECUTABLE_SUFFIXES = (
    ".bash",
    ".bat",
    ".cmd",
    ".cjs",
    ".exe",
    ".fish",
    ".jar",
    ".js",
    ".lua",
    ".mjs",
    ".php",
    ".pl",
    ".ps1",
    ".py",
    ".r",
    ".rb",
    ".sh",
    ".ts",
    ".zsh",
)

_SUFFIX_ALT = "|".join(re.escape(s) for s in sorted(EXECUTABLE_SUFFIXES, key=len, reverse=True))
_TOKEN_RE = re.compile(
    rf"(?<![A-Za-z0-9_./~-])"
    rf"(?:[A-Za-z0-9_.~+-]+/)*[A-Za-z0-9_.~+-]+(?:{_SUFFIX_ALT})"
    rf"(?![A-Za-z0-9_./~-])",
    re.IGNORECASE,
)
_INTERPRETER_RE = re.compile(
    r"\b(?:python(?:3(?:\.\d+)?)?|bash|sh|zsh|fish|pwsh|powershell|node|ruby|perl|php|lua|Rscript)\s+"
    r"[^\n`]*?"
    rf"(?:[A-Za-z0-9_.~+-]+/)*[A-Za-z0-9_.~+-]+(?:{_SUFFIX_ALT})\b",
    re.IGNORECASE,
)
_EXECUTION_CONTEXT_RE = re.compile(
    r"(?<!-)\b(?:run|execute|executes|executing|invoke|invokes|invoking|"
    r"call|calls|calling|launch|launches|launching|chmod|make executable|"
    r"from the terminal|in a shell|shell out|pipe into|redirect output)\b(?!-)",
    re.IGNORECASE,
)
_SHELL_FENCE_LANGS = {"bash", "sh", "shell", "console", "terminal", "zsh", "fish", "powershell", "ps1"}


def _iter_skill_files(repo_root: Path):
    skills_root = repo_root / "skills"
    if not skills_root.is_dir():
        return
    for skill_file in sorted(skills_root.glob("*/SKILL.md")):
        if ".system" in skill_file.parts:
            continue
        yield skill_file


def _line_has_execution_context(line: str, in_shell_fence: bool) -> bool:
    return in_shell_fence or bool(_INTERPRETER_RE.search(line)) or bool(_EXECUTION_CONTEXT_RE.search(line))


def validate(repo_root: Path) -> list[str]:
    errors: list[str] = []
    for skill_file in _iter_skill_files(repo_root):
        try:
            body = hand_authored_skill_body(skill_file.read_text(encoding="utf-8"))
        except UnicodeDecodeError:
            continue
        in_shell_fence = False
        for lineno, line in enumerate(body.splitlines(), start=1):
            fence_match = re.match(r"\s*```\s*([A-Za-z0-9_-]+)?", line)
            if fence_match:
                lang = (fence_match.group(1) or "").lower()
                in_shell_fence = not in_shell_fence and lang in _SHELL_FENCE_LANGS
                continue
            if not _line_has_execution_context(line, in_shell_fence):
                continue
            for match in _TOKEN_RE.finditer(line):
                errors.append(
                    f"{skill_file}:{lineno}: SBE001 executable-file reference "
                    f"`{match.group(0)}` appears in an execution context in hand-authored SKILL.md; "
                    "put execution behind a blueprint machine interface and refer to the interface name"
                )
    return errors


def main() -> int:
    errors = validate(REPO_ROOT)
    if errors:
        print("error: executable references found in SKILL.md bodies.", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
