"""Provider-neutral orchestration over Claude and Codex session adapters."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from pathlib import Path

from . import WakeupError
from .providers import all_providers, provider_for
from .providers.base import DetectedRateLimit


UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)


def json_lines(path: Path, *, strict: bool = False) -> Iterator[dict]:
    """Yield mapping-valued JSONL records and ignore partial malformed lines."""

    try:
        with path.open(errors="replace") as handle:
            for line in handle:
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    yield value
    except OSError as error:
        if strict:
            raise WakeupError(f"could not read transcript {path}: {error}") from error


def session_logs(provider: str) -> list[Path]:
    """List transcript files accepted by the selected provider adapter."""

    adapter = provider_for(provider)
    root = adapter.transcript_root()
    if not root.is_dir():
        return []
    return [path for path in root.rglob("*.jsonl") if adapter.include_log(path)]


def session_id_from_log(provider: str, path: Path) -> str | None:
    """Extract a normalized UUID from a filename or provider metadata."""

    filename_id = UUID_RE.search(path.name)
    if filename_id:
        return filename_id.group(0).lower()
    adapter = provider_for(provider)
    for event in json_lines(path):
        candidate = adapter.session_id(event)
        if isinstance(candidate, str) and UUID_RE.fullmatch(candidate):
            return candidate.lower()
    return None


def find_session_log(provider: str, session_id: str) -> Path | None:
    """Find the transcript belonging to an exact normalized UUID."""

    target = session_id.lower()
    for path in session_logs(provider):
        if target in path.name.lower() or session_id_from_log(provider, path) == target:
            return path
    return None


def _resolve_alias(provider: str, alias: str, context: str) -> str | None:
    """Resolve an adapter-defined title alias while rejecting ambiguity."""

    adapter = provider_for(provider)
    candidates: list[tuple[str, Path]] = []
    for candidate in adapter.indexed_sessions(alias):
        path = find_session_log(provider, candidate)
        if UUID_RE.fullmatch(candidate) and path:
            candidates.append((candidate.lower(), path))
    for path in session_logs(provider):
        for event in json_lines(path):
            if str(adapter.alias(event) or "").casefold() != alias.casefold():
                continue
            candidate = str(adapter.session_id(event) or "")
            if UUID_RE.fullmatch(candidate):
                candidates.append((candidate.lower(), path))
            break
    unique = list({session_id: path for session_id, path in candidates}.items())
    if len(unique) == 1:
        return unique[0][0]
    if len(unique) > 1:
        worktree = re.search(
            r"--worktree(?:=|\s+)[\"']?([^\s\"']+)", context, re.IGNORECASE
        )
        if worktree:
            token = worktree.group(1).casefold()
            matches = [sid for sid, path in unique if token in str(path).casefold()]
            if len(matches) == 1:
                return matches[0]
        raise WakeupError(f'{provider} session alias "{alias}" is ambiguous')
    return None


def resolve_session(provider: str, value: str, context: str = "") -> tuple[str, Path]:
    """Resolve a UUID or provider alias to an exact session and transcript."""

    identifier = value.strip().strip("\"'")
    session_id = (
        identifier.lower()
        if UUID_RE.fullmatch(identifier)
        else _resolve_alias(provider, identifier, context)
    )
    if not session_id:
        raise WakeupError(f'could not resolve {provider} session "{identifier}"')
    path = find_session_log(provider, session_id)
    if not path:
        raise WakeupError(f"could not find transcript for {provider} session {session_id}")
    return session_id, path


def latest_session(provider: str) -> tuple[str, Path]:
    """Return the newest session unless concurrent updates make it ambiguous."""

    candidates = sorted(session_logs(provider), key=lambda path: path.stat().st_mtime, reverse=True)
    resolved = [(session_id_from_log(provider, path), path) for path in candidates[:10]]
    resolved = [(session_id, path) for session_id, path in resolved if session_id]
    if not resolved:
        raise WakeupError(f"could not find a recent {provider} session")
    if len(resolved) > 1 and resolved[0][1].stat().st_mtime - resolved[1][1].stat().st_mtime < 30:
        raise WakeupError(f"recent {provider} session is ambiguous; include its resume command")
    return resolved[0]


def infer_provider(text: str) -> str:
    """Infer exactly one provider from timeout or resume-command text."""

    lowered = text.lower()
    matches = [adapter.name for adapter in all_providers() if re.search(rf"\b{re.escape(adapter.name)}\b", lowered)]
    if len(matches) == 1:
        return matches[0]
    if "session limit" in lowered:
        return all_providers()[0].name
    raise WakeupError("could not infer provider; include its resume command")


def infer_session_token(provider: str, text: str) -> str | None:
    """Extract a resume token or sole UUID from provider output."""

    if provider == "claude":
        pattern = r"\bclaude\b[^\n]*?--resume(?:=|\s+)[\"']?([^\s\"']+)"
    else:
        pattern = r"\bcodex\b[^\n]*?\bresume\s+[\"']?([^\s\"']+)"
    match = re.search(pattern, text, re.IGNORECASE)
    if match:
        return match.group(1)
    identifiers = UUID_RE.findall(text)
    return identifiers[0] if len(identifiers) == 1 else None


def latest_rate_limit() -> DetectedRateLimit:
    """Return the newest structured exhausted-limit event across adapters."""

    matches: list[tuple[object, DetectedRateLimit]] = []
    for adapter in all_providers():
        paths = sorted(
            session_logs(adapter.name), key=lambda path: path.stat().st_mtime, reverse=True
        )[:20]
        for path in paths:
            session_id = session_id_from_log(adapter.name, path)
            if not session_id:
                continue
            for event in json_lines(path):
                limit = adapter.rate_limit(event)
                if limit is not None:
                    matches.append(
                        (
                            limit.observed_at,
                            DetectedRateLimit(
                                adapter.name, session_id, limit.reset_at, limit.context
                            ),
                        )
                    )
    if not matches:
        raise WakeupError("could not find a recent structured rate-limit event")
    return max(matches, key=lambda item: item[0])[1]


def transcript_state(provider: str, path: Path) -> str:
    """Hash the latest provider-defined meaningful conversation event."""

    adapter = provider_for(provider)
    latest = ""
    for event in json_lines(path, strict=True):
        if adapter.meaningful(event):
            encoded = json.dumps(event, sort_keys=True, separators=(",", ":")).encode()
            latest = hashlib.sha256(encoded).hexdigest()
    return latest


def session_cwd(provider: str, path: Path) -> Path | None:
    """Return the latest existing provider-recorded working directory."""

    adapter = provider_for(provider)
    latest: Path | None = None
    for event in json_lines(path, strict=True):
        candidate = adapter.cwd(event)
        if candidate is not None and candidate.is_dir():
            latest = candidate
    return latest


__all__ = [
    "infer_provider", "infer_session_token", "latest_rate_limit", "latest_session",
    "resolve_session", "session_cwd", "transcript_state",
]
