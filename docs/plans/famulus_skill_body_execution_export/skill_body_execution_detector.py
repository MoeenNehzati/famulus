#!/usr/bin/env python3
"""Detect command leakage in hand-authored SKILL.md text.

This module is designed for a Famulus-style validator: it checks all
hand-authored Markdown/RST-ish text (paragraphs, bullets, inline code, fenced
blocks, and malformed/defenced snippets), but strips YAML frontmatter and
known generated blueprint blocks before scanning.

It is intentionally not a general English classifier. It catches command-shaped
spans that should be moved behind blueprint-declared machine interfaces.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import re
import sys
from pathlib import Path
from typing import Iterable, NamedTuple

GENERATED_BLOCKS = [
    ("<!-- BEGIN BLUEPRINT CONTRACT -->", "<!-- END BLUEPRINT CONTRACT -->"),
    ("<!-- BEGIN BLUEPRINT INTERFACES -->", "<!-- END BLUEPRINT INTERFACES -->"),
]

# Keep this list intentionally focused on command names that commonly leak into
# skill instructions. Add project-specific tools here as needed.
EXECS = {
    "python", "python3", "bash", "sh", "zsh",
    "pytest", "git", "uv", "uvx",
    "node", "npm", "npx", "pip", "pipx", "conda",
    "make", "cargo", "go", "ruby",
    "chmod", "chown", "rm", "cp", "mv", "mkdir", "touch", "ln",
    "cat", "grep", "sed", "awk", "find", "xargs",
    "curl", "wget", "jq", "flask", "gpgv", "shasum",
    "brew", "docker", "docker-compose", "kubectl",
}

# Standalone appearances of these are highly operational in SKILL.md.
STRONG_OPERATIONAL = {
    "chmod", "chown", "rm", "cp", "mv", "mkdir", "touch", "ln",
    "curl", "wget", "gpgv", "shasum", "xargs",
}

KNOWN_SUBCOMMANDS = {
    "git": {"clone", "config", "status", "diff", "add", "commit", "push", "checkout", "restore", "reset", "grep", "log", "pull", "fetch"},
    "python": {"-m", "-c"},
    "python3": {"-m", "-c"},
    "pip": {"install", "uninstall", "freeze", "show", "list", "download", "wheel"},
    "conda": {"install", "create", "env", "activate", "deactivate", "update"},
    "npm": {"install", "run", "test", "start", "publish", "exec"},
    "npx": {"create", "eslint", "prettier"},
    "uv": {"run", "x", "pip", "sync", "add", "tool", "venv"},
    "uvx": {"ruff", "pytest"},
    "cargo": {"build", "test", "run", "install", "check", "fmt", "clippy"},
    "go": {"build", "test", "run", "install", "mod", "get", "fmt"},
    "make": {"test", "install", "build", "quick-release"},
    "flask": {"run"},
    "brew": {"update", "doctor", "tap", "audit", "install", "bundle"},
    "docker": {"compose", "run", "build", "pull", "push", "exec", "ps"},
    "docker-compose": {"up", "down", "run", "build"},
    "kubectl": {"apply", "get", "describe", "logs", "exec", "delete"},
}

# Verbs that make nearby executable names instructional rather than conceptual.
IMPERATIVE_VERBS = {
    "run", "execute", "call", "invoke", "launch", "install", "shell", "use",
    "test", "check", "verify", "clone", "build",
}

# Followers that often mean an executable name is being discussed, not invoked.
EXPLANATORY_FOLLOWERS = {
    "is", "are", "was", "were", "means", "refers", "supports", "requires",
    "framework", "module", "package", "tool", "state", "repository", "config",
    "version", "versions", "dependency", "dependencies", "docs", "documentation",
    "project", "language", "environment",
}

# Phrase-level tripwires for operational prose that may not mention a command.
OPERATIONAL_PHRASES = [
    "run the following", "execute the following", "from the terminal",
    "shell out", "pipe into", "redirect output", "make executable",
    "change the executable bit", "command line", "cli command", "manual command",
    "copy and paste this command", "terminal command", "in your shell",
]

RULE_DESCRIPTIONS = {
    "SBX001": "raw script/executable path",
    "SBX002": "shell prompt command",
    "SBX003": "shell operator/redirection",
    "SBX004": "CLI flag leakage",
    "SBX005": "command-like executable invocation",
    "SBX006": "operational execution phrase",
}

PATH_PATTERNS = [
    # Local or cross-skill script paths.
    re.compile(r"\b(?:scripts/|skills/[a-z0-9-]+/scripts/)[^\s`)]*"),
    # Relative executable paths.
    re.compile(r"(?<![\w.-])(?:\./|\.\./)[^\s`)]*\.(?:py|sh|bash|zsh)\b"),
    # Bare executable file names. Aggressive by design for SKILL.md bodies.
    re.compile(r"\b[\w.-]+\.(?:py|sh|bash|zsh)\b"),
]

PROMPT_RE = re.compile(r"(?:^|\s)(?:[-*+]\s*)?(?:\$|>)\s+\S+")
SHELL_OPERATOR_RES = [
    re.compile(r"&&|\|\|"),
    re.compile(r"\|[ \t]*[A-Za-z_][\w.-]*"),
    re.compile(r"(?:^|\s)\d?>\s*\S+"),
    re.compile(r">>\s*\S+"),
]
FLAG_RE = re.compile(r"(?<![\w])--[A-Za-z][\w-]*")
INLINE_CODE_RE = re.compile(r"`([^`]+)`")
TOKEN_RE = re.compile(r"[A-Za-z0-9_./:+=$~{}\"'-]+")
URL_RE = re.compile(r"https?://|git@|[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+:")


@dataclasses.dataclass(frozen=True)
class Finding:
    rule: str
    line: int
    column: int
    match: str
    message: str


class Token(NamedTuple):
    raw: str
    clean: str
    start: int
    end: int


def strip_frontmatter_and_generated_blocks(text: str) -> str:
    """Remove YAML frontmatter and generated blueprint blocks."""
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                lines = lines[i + 1 :]
                break
    text = "\n".join(lines)
    for start, end in GENERATED_BLOCKS:
        pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.DOTALL)
        text = pattern.sub("", text)
    return text


def _clean_token(tok: str) -> str:
    return tok.strip("`'\"()[]{}.,:;").lower()


def _logical_line(line: str) -> str:
    """Strip Markdown/RST list, quote, prompt indentation prefixes."""
    s = line.strip()
    # Remove common Markdown quote prefixes.
    s = re.sub(r"^(?:>\s*)+", "", s)
    # Remove Markdown/RST bullets and numbered lists.
    s = re.sub(r"^(?:[-*+]\s+|\d+[.)]\s+)", "", s)
    # Remove RST literal block indentation; keep actual command text.
    return s.strip()


def _tokens(s: str) -> list[Token]:
    return [Token(m.group(0), _clean_token(m.group(0)), m.start(), m.end()) for m in TOKEN_RE.finditer(s)]


def _looks_like_version(tok: str) -> bool:
    t = tok.strip("`'\"()[]{}.,:;").lower()
    return bool(re.match(r"^v?\d+(?:\.\d+)*(?:\+)?$", t))


def _looks_like_arg(tok: str) -> bool:
    t = tok.strip("`'\"()[]{}.,;")
    if not t:
        return False
    if t.startswith("-"):
        return True
    if t in {".", "..", "~"}:
        return True
    if "/" in t:
        return True
    if URL_RE.search(t):
        return True
    if re.search(r"\.(?:py|sh|bash|zsh|toml|ya?ml|json|md|txt|rst|asc|kbx|gz|zip|whl)\b", t):
        return True
    if "=" in t and not re.match(r"^[A-Za-z]+\s*=\s*", t):
        return True
    if t.startswith("${") or t.startswith("$HOME") or t.startswith("~/"):
        return True
    return False


def _span(s: str, toks: list[Token], i: int, extra: int = 4) -> str:
    end_idx = min(len(toks) - 1, i + extra)
    return s[toks[i].start : toks[end_idx].end]


def _is_probably_conceptual_command_name(toks: list[Token], i: int) -> bool:
    """Avoid obvious prose such as 'pytest is used' or 'Python 3.10+'."""
    word = toks[i].clean
    nxt = toks[i + 1].clean if i + 1 < len(toks) else ""
    nxt2 = toks[i + 2].clean if i + 2 < len(toks) else ""
    nxt_raw = toks[i + 1].raw if i + 1 < len(toks) else ""

    # Dependency/version bullets: "Python (>= 3.11)", "pytest >= 7".
    if word in {"python", "python3", "pytest", "node", "npm", "pip"} and re.match(r"^[\(\[]? *(?:>=|<=|==|>|<|~=|=)", nxt_raw):
        return True

    if nxt in EXPLANATORY_FOLLOWERS:
        return True
    if _looks_like_version(nxt):
        return True
    # Proper-noun docs: "Docker Compose is..." is not a command; lower-case
    # `docker compose up` is.
    if word == "docker" and toks[i].raw[:1].isupper() and nxt == "compose" and nxt2 in EXPLANATORY_FOLLOWERS:
        return True
    if word == "git" and nxt in {"state", "repository", "config", "history"}:
        return True
    if word == "python" and nxt in {"package", "packages", "module", "modules", "version", "versions"}:
        return True
    return False


def _is_command_sequence(toks: list[Token], i: int) -> bool:
    """Does toks[i:] look like an executable invocation?"""
    if i >= len(toks) or toks[i].clean not in EXECS:
        return False
    exe = toks[i].clean
    if _is_probably_conceptual_command_name(toks, i):
        return False
    if exe in STRONG_OPERATIONAL:
        # Strong operational commands need either args, a nearby imperative, or
        # command position. This avoids flagging a bare conceptual `curl` word.
        return i + 1 < len(toks) and not toks[i + 1].clean in EXPLANATORY_FOLLOWERS
    if i + 1 >= len(toks):
        # A bare command on its own line or in inline code is still a command.
        return exe in {"pytest", "make", "bash", "sh", "zsh", "python", "python3", "node", "npm", "pip", "git", "brew"}
    nxt = toks[i + 1].clean
    if _looks_like_arg(toks[i + 1].raw):
        return True
    if nxt in KNOWN_SUBCOMMANDS.get(exe, set()):
        return True
    # docker compose up: command sequence of two known words plus arg/subcommand.
    if exe == "docker" and nxt == "compose" and i + 2 < len(toks):
        return toks[i + 2].clean in {"up", "down", "run", "build"} or _looks_like_arg(toks[i + 2].raw)
    return False


def _command_invocation_spans(line: str) -> list[tuple[int, str]]:
    spans: list[tuple[int, str]] = []
    s = _logical_line(line)
    toks = _tokens(s)
    if not toks:
        return spans

    # Prompt after prose or at line start: "Install: $ python -m pip install".
    # Treat Markdown blockquote "> prose" as a prompt only when the text after
    # the marker is itself command-shaped.
    for m in PROMPT_RE.finditer(s):
        marker_text = m.group(0).strip()
        after = re.sub(r"^(?:[-*+]\s*)?(?:\$|>)\s+", "", marker_text)
        after_toks = _tokens(after)
        if marker_text.startswith("$") or (after_toks and _is_command_sequence(after_toks, 0)):
            spans.append((m.start(), marker_text))

    # Command at logical line start or after stripping bullets/indentation.
    if _is_command_sequence(toks, 0):
        spans.append((toks[0].start, _span(s, toks, 0)))

    # Colon/semicolon often introduces a command after defencing/joining.
    for i, tok in enumerate(toks):
        if tok.clean in EXECS and _is_command_sequence(toks, i):
            prefix = s[: tok.start]
            near_prefix = prefix[-12:]
            if re.search(r"[:;]\s*$", near_prefix):
                spans.append((tok.start, _span(s, toks, i)))

    # Imperative verb within a short window before executable.
    words = [t.clean for t in toks]
    for i, tok in enumerate(toks):
        if tok.clean not in EXECS:
            continue
        prev = words[max(0, i - 7):i]
        if any(v in prev for v in IMPERATIVE_VERBS) and _is_command_sequence(toks, i):
            start_i = max(0, i - 2)
            spans.append((toks[start_i].start, _span(s, toks, start_i, extra=6)))

    # Embedded command-shaped sequence anywhere: catches removed surrounding
    # context like "Before certification git clone -c ..." without requiring the
    # word "run". Keep this strict via _is_command_sequence to avoid prose FPs.
    for i, tok in enumerate(toks):
        if i == 0:
            continue
        if _is_command_sequence(toks, i):
            spans.append((tok.start, _span(s, toks, i)))
        # Bare test commands after context removal: "Before certification pytest".
        elif tok.clean in {"pytest", "make"} and any(w in words[max(0, i - 4):i] for w in {"before", "certification", "certifying", "execute"}):
            spans.append((max(0, toks[max(0, i - 2)].start), _span(s, toks, max(0, i - 2), extra=4)))

    # Inline code: flag command sequences with arguments, or strong operational
    # one-word commands like `chmod`. Do not flag conceptual `pytest`.
    for m in INLINE_CODE_RE.finditer(line):
        inner = m.group(1).strip()
        inner_toks = _tokens(inner)
        if not inner_toks:
            continue
        if len(inner_toks) == 1:
            before = line[max(0, m.start() - 40):m.start()].lower()
            if inner_toks[0].clean in STRONG_OPERATIONAL or re.search(r"\b(instruction|command|execute|run|call|invoke)\b", before):
                spans.append((m.start(), m.group(0)))
        elif _is_command_sequence(inner_toks, 0):
            spans.append((m.start(), m.group(0)))

    # Deduplicate.
    out: list[tuple[int, str]] = []
    seen = set()
    for col, span in spans:
        key = (col, span)
        if key not in seen:
            seen.add(key)
            out.append((col, span))
    return out


def detect_command_leakage(text: str, *, strip_generated: bool = True) -> list[Finding]:
    if strip_generated:
        text = strip_frontmatter_and_generated_blocks(text)
    findings: list[Finding] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        for regex in PATH_PATTERNS:
            for m in regex.finditer(line):
                findings.append(Finding("SBX001", line_no, m.start() + 1, m.group(0), f"{RULE_DESCRIPTIONS['SBX001']}: move executable paths into a blueprint interface"))
        for m in PROMPT_RE.finditer(line):
            marker_text = m.group(0).strip()
            after = re.sub(r"^(?:[-*+]\s*)?(?:\$|>)\s+", "", marker_text)
            after_toks = _tokens(after)
            if marker_text.startswith("$") or (after_toks and _is_command_sequence(after_toks, 0)):
                findings.append(Finding("SBX002", line_no, m.start() + 1, marker_text, f"{RULE_DESCRIPTIONS['SBX002']}: generated interface blocks are the place for commands"))
        for regex in SHELL_OPERATOR_RES:
            for m in regex.finditer(line):
                op = m.group(0).strip()
                # Do not treat a Markdown blockquote marker as shell redirection
                # unless the quoted content itself is command-shaped.
                if op.startswith(">") and m.start() == 0:
                    after = line[m.end():].strip()
                    after_toks = _tokens(after)
                    if not (after_toks and _is_command_sequence(after_toks, 0)):
                        continue
                findings.append(Finding("SBX003", line_no, m.start() + 1, op, f"{RULE_DESCRIPTIONS['SBX003']}: shell composition belongs in scripts"))
        for m in FLAG_RE.finditer(line):
            findings.append(Finding("SBX004", line_no, m.start() + 1, m.group(0), f"{RULE_DESCRIPTIONS['SBX004']}: move flags to blueprint usage/patterns or script help"))
        for col, span in _command_invocation_spans(line):
            findings.append(Finding("SBX005", line_no, col + 1, span, f"{RULE_DESCRIPTIONS['SBX005']}: refer to an exported interface id instead"))
        lower = line.lower()
        for phrase in OPERATIONAL_PHRASES:
            idx = lower.find(phrase)
            if idx >= 0:
                findings.append(Finding("SBX006", line_no, idx + 1, line[idx : idx + len(phrase)], f"{RULE_DESCRIPTIONS['SBX006']}: make execution a script/interface, not prose"))
    uniq: list[Finding] = []
    seen = set()
    for f in findings:
        key = (f.rule, f.line, f.column, f.match)
        if key not in seen:
            uniq.append(f)
            seen.add(key)
    return uniq


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", help="Markdown files to scan; stdin if omitted")
    parser.add_argument("--json", action="store_true", help="emit JSON findings")
    args = parser.parse_args(argv)

    all_findings: list[dict] = []
    if not args.paths:
        text = sys.stdin.read()
        all_findings = [{"path": "<stdin>", **dataclasses.asdict(f)} for f in detect_command_leakage(text)]
    else:
        for p in args.paths:
            path = Path(p)
            text = path.read_text(encoding="utf-8")
            all_findings.extend({"path": str(path), **dataclasses.asdict(f)} for f in detect_command_leakage(text))

    if args.json:
        print(json.dumps(all_findings, indent=2))
    else:
        for f in all_findings:
            print(f"{f['path']}:{f['line']}:{f['column']}: {f['rule']}: {f['match']!r} — {f['message']}")
    return 1 if all_findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
