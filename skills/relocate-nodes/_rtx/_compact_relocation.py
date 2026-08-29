"""Compact recipe, review, and atomic publication for node relocation."""
from __future__ import annotations
import ast, base64
from dataclasses import dataclass
import hashlib, json, os
from pathlib import Path, PurePosixPath
import re, shutil, stat, subprocess, tempfile, time
from typing import Callable, Iterable, Mapping
import yaml
from yaml.nodes import MappingNode, ScalarNode, SequenceNode
from officina.common import toml_io
from officina.common.atomic_files import atomic_replace_bytes, exclusive_file_lock
from officina.configuration.repository import load_repository_configuration
_IGNORED = {".git", ".venv", ".worktrees", "__pycache__", ".pytest_cache", ".mypy_cache", "_build", "build", "dist", "node_modules"}
_STRUCTURED = {".yaml", ".yml", ".json"}
class RelocationError(RuntimeError):
    pass
@dataclass(frozen=True)
class Occurrence:
    occurrence_id: str; path: str
    byte_start: int; byte_end: int; line: int
    match: str; candidate: str; context: str
@dataclass
class Recipe:
    root: Path; writes: dict[str, bytes]; deletes: set[str]
    expected: dict[str, bytes | None]; modes: dict[str, int]; occurrences: list[Occurrence]
    @property
    def empty(self) -> bool:
        return not self.writes and not self.deletes and not self.occurrences
    def report(self) -> dict[str, object]:
        return {"writes": sorted(self.writes), "deletes": sorted(self.deletes),
                "semantic_occurrences": [dict(vars(item)) for item in self.occurrences],
                "unaccounted_semantic_occurrences": [item.occurrence_id for item in self.occurrences]}
def _relative(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise RelocationError(f"{label} must be a nonempty path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or value.startswith("./"):
        raise RelocationError(f"{label} must be repository-relative: {value!r}")
    return path.as_posix()
def _pairs(root: Path, manifest: Mapping[str, object], inventory: set[str]) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    if manifest.get("schema_version") != 3:
        raise RelocationError("manifest schema_version must be 3")
    raw_moves = manifest.get("relocations")
    if not isinstance(raw_moves, list) or not raw_moves:
        raise RelocationError("manifest relocations must be a nonempty list")
    moves = [(_relative(item.get("from"), "relocations.from"), _relative(item.get("to"), "relocations.to"))
             for item in raw_moves if isinstance(item, Mapping)]
    if len(moves) != len(raw_moves) or any(old == new for old, new in moves):
        raise RelocationError("each relocation needs distinct from and to paths")
    config = load_repository_configuration(root / toml_io.repository_config_filename())
    logical: list[tuple[str, str]] = []
    for old, new in moves:
        old_path, new_path = root / old, root / new
        old_selected = any(item == old or item.startswith(old + "/") for item in inventory)
        new_selected = any(item == new or item.startswith(new + "/") for item in inventory)
        if old_selected and new_selected:
            raise RelocationError(f"destination already exists: {new}")
        if not old_selected and not new_selected:
            raise RelocationError(f"relocation source does not exist: {old}")
        sources = [item for item in config.module_roots if old_path.is_relative_to(item)]
        targets = [item for item in config.module_roots if new_path.is_relative_to(item)]
        if len(sources) != 1 or len(targets) != 1 or sources[0] != targets[0]:
            raise RelocationError(f"relocation must stay within one configured root: {old} -> {new}")
        logical.append((".".join(old_path.relative_to(sources[0]).parts), ".".join(new_path.relative_to(sources[0]).parts)))
    raw_python = manifest.get("python_modules", [])
    if not isinstance(raw_python, list):
        raise RelocationError("python_modules must be a list")
    python_pairs = [(str(item["from"]), str(item["to"])) for item in raw_python
                    if isinstance(item, Mapping) and isinstance(item.get("from"), str) and isinstance(item.get("to"), str)]
    if len(python_pairs) != len(raw_python):
        raise RelocationError("python_modules entries require from and to")
    return moves, logical + python_pairs
def _files(root: Path, exclusions: Iterable[str]) -> Iterable[Path]:
    excluded = tuple(item.rstrip("/") for item in exclusions)
    if (root / ".git").exists():
        result = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "-z"], cwd=root,
                                check=True, stdout=subprocess.PIPE)
        for raw in result.stdout.split(b"\0"):
            if not raw:
                continue
            relative = raw.decode("utf-8", errors="surrogateescape")
            if not any(part in _IGNORED for part in PurePosixPath(relative).parts) and not _excluded(relative, excluded):
                yield root / relative
        return
    for directory, names, files in os.walk(root):
        base = Path(directory).relative_to(root).as_posix()
        names[:] = [name for name in names if name not in _IGNORED and not _excluded(f"{base}/{name}".lstrip("./"), excluded)]
        for name in files:
            path = Path(directory) / name
            if not _excluded(path.relative_to(root).as_posix(), excluded):
                yield path
def _excluded(relative: str, exclusions: Iterable[str]) -> bool:
    return any(relative == item or relative.startswith(item + "/") for item in exclusions)
def _replace(payload: bytes, pairs: Iterable[tuple[str, str]]) -> bytes:
    for old, new in sorted(pairs, key=lambda item: len(item[0]), reverse=True):
        payload = payload.replace(old.encode(), new.encode())
    return payload
def _python_imports(payload: bytes, pairs: list[tuple[str, str]], path: str) -> bytes:
    try:
        text = payload.decode("utf-8")
        ast.parse(text, filename=path)
    except (UnicodeDecodeError, SyntaxError):
        return payload
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.lstrip().startswith(("import ", "from ")):
            for old, new in pairs:
                line = re.sub(rf"(?<![A-Za-z0-9_]){re.escape(old)}(?=\.|\b)", new, line)
            lines[index] = line
    return "".join(lines).encode()
def _structured(payload: bytes, pairs: list[tuple[str, str]]) -> bytes:
    text = payload.decode("utf-8")
    root = yaml.compose(text)
    stack, edits = ([root] if root else []), []
    while stack:
        node = stack.pop()
        if isinstance(node, ScalarNode) and any(old in node.value for old, _new in pairs):
            start, end = node.start_mark.index, node.end_mark.index
            edits.append((start, end, _replace(text[start:end].encode(), pairs).decode()))
        elif isinstance(node, MappingNode):
            stack.extend(child for pair in node.value for child in pair)
        elif isinstance(node, SequenceNode):
            stack.extend(node.value)
    for start, end, replacement in sorted(edits, reverse=True):
        text = text[:start] + replacement + text[end:]
    return text.encode()
def _mechanical(relative: str, payload: bytes, address_pairs: list[tuple[str, str]], python_pairs: list[tuple[str, str]]) -> bytes:
    if not any(old.encode() in payload for old, _new in address_pairs + python_pairs):
        return payload
    suffix = Path(relative).suffix.lower()
    if suffix in _STRUCTURED:
        try:
            return _structured(payload, address_pairs)
        except (UnicodeDecodeError, yaml.YAMLError):
            return payload
    if suffix == ".py":
        return _python_imports(payload, python_pairs, relative)
    if Path(relative).name == "SKILL.md":
        text = payload.decode("utf-8", errors="strict")
        start, end = "<!-- BEGIN BLUEPRINT CONTRACT -->", "<!-- END BLUEPRINT INTERFACES -->"
        if start in text and end in text:
            stop = text.index(end) + len(end)
            return _replace(text[:stop].encode(), address_pairs) + text[stop:].encode()
    return payload
def _occurrences(relative: str, payload: bytes, pairs: list[tuple[str, str]]) -> list[Occurrence]:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        return []
    found: list[Occurrence] = []
    occupied: set[int] = set()
    for old, new in sorted(pairs, key=lambda item: len(item[0]), reverse=True):
        cursor = 0
        while (index := text.find(old, cursor)) >= 0:
            cursor = index + len(old)
            before = text[index - 1] if index else ""
            after = text[cursor] if cursor < len(text) else ""
            if (old[0].isalnum() or old[0] == "_") and (before.isalnum() or before == "_"):
                continue
            if (old[-1].isalnum() or old[-1] == "_") and (after.isalnum() or after == "_"):
                continue
            if any(position in occupied for position in range(index, cursor)):
                continue
            occupied.update(range(index, cursor))
            byte_start = len(text[:index].encode())
            digest = hashlib.sha256(payload).hexdigest()
            identity = f"{relative}\0{digest}\0{byte_start}\0{old}\0{new}".encode()
            line_start, line_end = text.rfind("\n", 0, index) + 1, text.find("\n", cursor)
            if line_end < 0:
                line_end = len(text)
            found.append(Occurrence("sha256:" + hashlib.sha256(identity).hexdigest(), relative,
                                    byte_start, byte_start + len(old.encode()), text.count("\n", 0, index) + 1,
                                    old, new, text[line_start:line_end]))
    return sorted(found, key=lambda item: (item.path, item.byte_start))
def _apply_decisions(payload: bytes, occurrences: list[Occurrence], decisions: Mapping[str, Mapping[str, object]], default: str | None, overrides: Mapping[str, str]) -> tuple[bytes, list[Occurrence]]:
    replacements: list[tuple[int, int, bytes]] = []
    undecided: list[Occurrence] = []
    for occurrence in occurrences:
        decision = decisions.get(occurrence.occurrence_id)
        disposition = decision.get("disposition") if decision else overrides.get(occurrence.path, default)
        if disposition is None:
            undecided.append(occurrence)
        elif disposition == "rewrite":
            replacement = decision.get("replacement", occurrence.candidate) if decision else occurrence.candidate
            if not isinstance(replacement, str):
                raise RelocationError(f"invalid replacement for {occurrence.occurrence_id}")
            replacements.append((occurrence.byte_start, occurrence.byte_end, replacement.encode()))
        elif disposition != "preserve":
            raise RelocationError(f"invalid disposition for {occurrence.occurrence_id}")
    for start, end, replacement in sorted(replacements, reverse=True):
        payload = payload[:start] + replacement + payload[end:]
    return payload, undecided
def _review_policy(manifest: Mapping[str, object]) -> tuple[dict[str, Mapping[str, object]], str | None, dict[str, str]]:
    raw_decisions, raw_overrides = manifest.get("semantic_decisions", []), manifest.get("disposition_overrides", [])
    default = manifest.get("default_disposition")
    if not isinstance(raw_decisions, list) or not isinstance(raw_overrides, list):
        raise RelocationError("semantic decisions and disposition overrides must be lists")
    if default not in (None, "rewrite", "preserve"):
        raise RelocationError("default_disposition must be rewrite or preserve")
    decisions = {str(item.get("occurrence_id")): item for item in raw_decisions if isinstance(item, Mapping)}
    overrides: dict[str, str] = {}
    for item in raw_overrides:
        if not isinstance(item, Mapping) or item.get("disposition") not in ("rewrite", "preserve"):
            raise RelocationError("disposition overrides require path and rewrite or preserve")
        path = _relative(item.get("path"), "disposition_overrides.path")
        if path in overrides:
            raise RelocationError(f"duplicate disposition override: {path}")
        overrides[path] = str(item["disposition"])
    return decisions, default, overrides
def _supplemental(manifest: Mapping[str, object]) -> dict[str, list[tuple[bytes, bytes]]]:
    raw = manifest.get("supplemental_edits", [])
    if not isinstance(raw, list):
        raise RelocationError("supplemental_edits must be a list")
    edits: dict[str, list[tuple[bytes, bytes]]] = {}
    for item in raw:
        if not isinstance(item, Mapping) or not isinstance(item.get("expected"), str) or not item.get("expected") or not isinstance(item.get("replacement"), str):
            raise RelocationError("supplemental edits require path, expected, and replacement")
        path = _relative(item.get("path"), "supplemental_edits.path")
        edits.setdefault(path, []).append((str(item["expected"]).encode(), str(item["replacement"]).encode()))
    return edits
def plan(root: Path, manifest: Mapping[str, object], *, recover_interrupted: bool = True) -> Recipe:
    root = root.resolve()
    if recover_interrupted:
        recover(root)
    exclusions = manifest.get("inventory_exclusions", [])
    if not isinstance(exclusions, list) or not all(isinstance(item, str) for item in exclusions):
        raise RelocationError("inventory_exclusions must be a string list")
    paths = list(_files(root, exclusions))
    inventory = {path.relative_to(root).as_posix() for path in paths if path.is_file() or path.is_symlink()}
    moves, logical_python = _pairs(root, manifest, inventory)
    address_pairs = moves + logical_python
    python_pairs = [(old.replace("-", "_"), new.replace("-", "_")) for old, new in logical_python]
    decisions, default, overrides = _review_policy(manifest)
    supplemental = _supplemental(manifest)
    writes: dict[str, bytes] = {}; deletes: set[str] = set()
    expected: dict[str, bytes | None] = {}; modes: dict[str, int] = {}
    pending: list[Occurrence] = []
    for path in paths:
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            target = os.readlink(path)
            if any(relative == old or relative.startswith(old + "/") or old in relative or old in target
                   for old, _new in address_pairs):
                raise RelocationError(f"symlink relocation requires explicit handling: {relative}")
            continue
        if not path.is_file():
            continue
        target = relative
        for old, new in sorted(moves, key=lambda item: len(item[0]), reverse=True):
            if relative == old or relative.startswith(old + "/"):
                target = new + relative[len(old):]
                break
        payload = path.read_bytes()
        projected = _mechanical(target, payload, address_pairs, python_pairs)
        projected, unresolved = _apply_decisions(projected, _occurrences(target, projected, address_pairs), decisions, default, overrides)
        for expected_edit, replacement in supplemental.pop(target, []):
            count = projected.count(expected_edit)
            if count == 1:
                projected = projected.replace(expected_edit, replacement, 1)
            elif count != 0 or projected.count(replacement) != 1:
                raise RelocationError(f"supplemental edit precondition failed: {target}")
        pending.extend(unresolved)
        if target != relative or projected != payload:
            writes[target] = projected
            expected[target] = None if target != relative else payload
            modes[target] = stat.S_IMODE(path.stat().st_mode)
        if target != relative:
            deletes.add(relative)
            expected[relative] = payload
    if supplemental:
        raise RelocationError(f"supplemental edit path not selected: {next(iter(supplemental))}")
    return Recipe(root, writes, deletes, expected, modes, sorted(pending, key=lambda item: (item.path, item.byte_start)))
def build_packet(root: Path, report: Mapping[str, object]) -> dict[str, object]:
    grouped: dict[tuple[str, str | None], list[Mapping[str, object]]] = {}
    for occurrence in report.get("semantic_occurrences", []):
        if not isinstance(occurrence, Mapping) or not isinstance(occurrence.get("path"), str):
            raise RelocationError("invalid semantic occurrence report")
        relative = str(occurrence["path"])
        section = None
        if Path(relative).suffix.lower() in {".md", ".markdown"}:
            try:
                lines = (root / relative).read_text(encoding="utf-8").splitlines()
            except OSError:
                lines = []
            for line in lines[: int(occurrence.get("line", 1))]:
                if line.startswith("#") and line.lstrip("#").startswith(" "):
                    section = line.lstrip("#").strip()
        grouped.setdefault((relative, section), []).append(occurrence)
    units = [{"path": path, "section": section, "suggestion": "preserve" if path.startswith(("docs/plans/", "docs/superpowers/")) else "rewrite", "decision": None, "occurrences": items}
             for (path, section), items in sorted(grouped.items(), key=lambda item: (item[0][0], item[0][1] or ""))]
    return {"schema_version": 1, "summary": {"occurrences": sum(len(items) for items in grouped.values()), "review_units": len(units)}, "review_units": units}
def render_packet(packet: Mapping[str, object]) -> str:
    summary, units = packet["summary"], packet["review_units"]
    if not isinstance(summary, Mapping) or not isinstance(units, list):
        raise RelocationError("invalid review packet")
    lines = [f"Relocation review: {summary['occurrences']} occurrences in {summary['review_units']} review units"]
    for index, unit in enumerate(units, 1):
        if not isinstance(unit, Mapping) or not isinstance(unit.get("occurrences"), list):
            raise RelocationError("invalid review unit")
        counts: dict[tuple[str, str], int] = {}
        for occurrence in unit["occurrences"]:
            key = (str(occurrence["match"]), str(occurrence["candidate"]))
            counts[key] = counts.get(key, 0) + 1
        count = len(unit["occurrences"])
        section = f" — {unit['section']}" if unit.get("section") else ""
        pairs = "; ".join(f"`{old}` → `{new}` ×{amount}" for (old, new), amount in counts.items())
        lines.append(f"{index}. `{unit['path']}`{section} — suggested `{unit['suggestion']}` — {count} occurrence{'s' if count != 1 else ''}: {pairs}")
    return "\n".join(lines)
def _state(root: Path) -> tuple[Path, Path, Path]:
    base = Path(tempfile.gettempdir()) / "officina-relocation"
    base.mkdir(mode=0o700, exist_ok=True)
    key = hashlib.sha256(os.fsencode(str(root))).hexdigest()
    return base, base / f"{key}.lock", base / key
def _restore(root: Path, marker: Mapping[str, object]) -> None:
    baseline, directories, created = marker.get("baseline"), marker.get("directories"), marker.get("created_directories")
    if not isinstance(baseline, Mapping) or not isinstance(directories, Mapping) or not isinstance(created, list):
        raise RelocationError("invalid recovery marker")
    for relative, mode in sorted(directories.items(), key=lambda item: len(PurePosixPath(str(item[0])).parts)):
        path = root / str(relative)
        path.mkdir(parents=True, exist_ok=True)
        path.chmod(int(mode))
    for relative, raw in sorted(baseline.items(), key=lambda item: len(PurePosixPath(str(item[0])).parts), reverse=True):
        path = root / str(relative)
        if raw is None:
            if path.is_file() or path.is_symlink():
                path.unlink()
            continue
        if not isinstance(raw, Mapping):
            raise RelocationError("invalid recovery entry")
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_replace_bytes(path, base64.b64decode(str(raw["data"])), allowed_root=root, mode=int(raw["mode"]))
    for relative in sorted((str(item) for item in created), key=lambda item: len(PurePosixPath(item).parts), reverse=True):
        try:
            (root / relative).rmdir()
        except OSError:
            pass
def recover(root: Path) -> bool:
    root = root.resolve()
    base, lock, state = _state(root)
    with exclusive_file_lock(lock, allowed_root=base):
        marker_path = state / "marker.json"
        if not marker_path.exists():
            if state.exists():
                shutil.rmtree(state)
            return False
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        if marker.get("repository") != str(root):
            raise RelocationError("recovery marker repository mismatch")
        _restore(root, marker)
        shutil.rmtree(state)
        return True
def apply(recipe: Recipe, *, verify: Callable[[], Mapping[str, float] | None]) -> dict[str, float]:
    if recipe.occurrences:
        raise RelocationError("apply rejected: unaccounted semantic occurrences remain")
    root = recipe.root; base, lock, state = _state(root)
    started, verification_seconds = time.perf_counter(), 0.0
    with exclusive_file_lock(lock, allowed_root=base):
        if state.exists():
            marker = json.loads((state / "marker.json").read_text(encoding="utf-8"))
            _restore(root, marker)
            shutil.rmtree(state)
        for relative, expected in recipe.expected.items():
            path = root / relative
            current = path.read_bytes() if path.is_file() and not path.is_symlink() else None
            if current != expected:
                raise RelocationError(f"repository changed after preflight: {relative}")
        baseline: dict[str, object] = {}; touched = set(recipe.writes) | recipe.deletes
        directories: dict[str, int] = {}; created_directories: set[str] = set()
        for relative in touched:
            parent = (root / relative).parent
            while parent != root:
                key = parent.relative_to(root).as_posix()
                if parent.is_dir():
                    directories[key] = stat.S_IMODE(parent.stat().st_mode)
                elif not parent.exists():
                    created_directories.add(key)
                parent = parent.parent
        for relative in sorted(touched):
            path = root / relative
            if path.is_file() and not path.is_symlink():
                baseline[relative] = {"data": base64.b64encode(path.read_bytes()).decode(), "mode": stat.S_IMODE(path.stat().st_mode)}
            else:
                baseline[relative] = None
        state.mkdir(mode=0o700)
        marker = {"repository": str(root), "baseline": baseline, "directories": directories,
                  "created_directories": sorted(created_directories)}
        atomic_replace_bytes(state / "marker.json", (json.dumps(marker, sort_keys=True) + "\n").encode(), allowed_root=state, mode=0o600)
        try:
            for relative, payload in sorted(recipe.writes.items()):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                atomic_replace_bytes(path, payload, allowed_root=root, mode=recipe.modes.get(relative, 0o644))
            for relative in sorted(recipe.deletes, reverse=True):
                path = root / relative
                if path.is_file() or path.is_symlink():
                    path.unlink()
                parent = path.parent
                while parent != root:
                    try:
                        parent.rmdir()
                    except OSError:
                        break
                    parent = parent.parent
            verification_started = time.perf_counter()
            verification = verify() or {}
            verification_seconds = time.perf_counter() - verification_started
        except BaseException:
            _restore(root, marker)
            shutil.rmtree(state)
            raise
        shutil.rmtree(state)
    return {"transactional_writes_seconds": time.perf_counter() - started - verification_seconds, **verification}
