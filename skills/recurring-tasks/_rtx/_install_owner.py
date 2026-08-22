"""Which checkout owns the installed scheduler registrations.

The host has one installation -- the registrations plus the health-check cron
entry -- and as many copies of this skill as there are checkouts of the
repository: the canonical one, plus every git worktree. Every module here
derives its own paths from ``Path(__file__)``, so a sync run from any copy
rendered registrations pointing at *that* copy, with no error and exit 0.

On 2026-08-17 that happened from a worktree: the cron entry was repointed into
it, and the health check then rendered its expectation from the worktree while
comparing against the canonical units. It reported ``service unit stale`` for
all three jobs every four hours, against an installation that was healthy the
whole time. The same class had already been patched once, for validator mirrors
under the temp directory, by the narrower predicate this module replaces.

This module records the owning checkout once and answers one question: is the
copy running right now the one that owns this installation? SYNC refuses when
the answer is no; CHECK declines to judge registration rather than reporting
drift that does not exist.

The record lives beside the registrations rather than under a Famulus state
root, and that placement is deliberate. Every backend resolves its unit
directory from ``Path.home()`` alone (Windows tolerates a missing
``LOCALAPPDATA`` instead of raising), whereas the state roots resolve through
``XDG_STATE_HOME``. Sync is run by a human from a terminal that sources
``.bashrc``; CHECK runs from cron and RUN from a systemd unit, and neither does.
A record whose *location* depended on an environment variable set in one of
those and not the others would recreate exactly the false-alarm class this
module exists to end. Co-location also puts the record in the same failure
domain as the thing it describes: restore both, lose both, move both together.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# Kept outside the ``ai-*.timer``/``ai-*.service`` shapes the backends glob when
# they sweep away disabled jobs, so the record is never mistaken for a
# registration and deleted with one.
RECORD_NAME = "install-owner.json"
_SCHEMA_VERSION = 2


@dataclass(frozen=True)
class OwnerRecord:
    installation_id: str
    source_path: Path


class NotTheOwnerError(RuntimeError):
    """Raised when a copy that does not own the installation tries to write it."""


def record_path(unit_dir: Path, installation_id: str = "standard") -> Path:
    name = RECORD_NAME if installation_id == "standard" else f"install-owner-{installation_id}.json"
    return Path(unit_dir) / name


def write_owner(
    *, unit_dir: Path, owner: Path, installation_id: str = "standard"
) -> None:
    """Record ``owner`` as the checkout owning this installation.

    Written atomically: a torn record reads back as no record at all, which is
    a branch the caller must already handle, whereas a half-written one would
    be a new failure mode.
    """
    target = record_path(unit_dir, installation_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": _SCHEMA_VERSION,
        "installation_id": installation_id,
        "source_path": str(Path(owner)),
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    tmp = target.with_name(f".{target.name}.tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, target)


def read_owner_record(
    unit_dir: Path, installation_id: str = "standard"
) -> OwnerRecord | None:
    """Return the recorded owner, or None when there is not a usable record.

    Every unreadable shape -- absent, truncated, malformed, wrong type, a
    directory where the file belongs, unreadable by this user -- collapses to
    None rather than raising. CHECK runs from cron with no one to see a
    traceback, and an exception escaping there would leave the sentinel
    reporting the previous run's stale reason.
    """
    try:
        raw = record_path(unit_dir, installation_id).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None
    try:
        payload = json.loads(raw)
    except (ValueError, AttributeError):
        return None
    if not isinstance(payload, dict):
        return None
    schema = payload.get("schema_version")
    if schema == 1 and installation_id == "standard":
        owner = payload.get("owner")
        if not isinstance(owner, str) or not owner:
            return None
        write_owner(unit_dir=unit_dir, installation_id="standard", owner=Path(owner))
        return OwnerRecord("standard", Path(owner))
    if schema != _SCHEMA_VERSION or payload.get("installation_id") != installation_id:
        return None
    source_path = payload.get("source_path")
    if not isinstance(source_path, str) or not source_path:
        return None
    return OwnerRecord(installation_id, Path(source_path))


def read_owner(unit_dir: Path, installation_id: str = "standard") -> Path | None:
    record = read_owner_record(unit_dir, installation_id)
    return record.source_path if record is not None else None


def require_ownership(
    *,
    unit_dir: Path,
    skill_dir: Path,
    registrations_present: bool,
    adopt: bool = False,
    live_install: bool = True,
    installation_id: str = "standard",
) -> None:
    """Refuse unless this copy may write the installation.

    ``adopt`` is the deliberate move of an installation to another checkout; it
    is the only way past a recorded owner that disagrees.

    ``live_install`` says whether this targets the machine's real installation
    rather than an isolated directory. It gates the temporary-copy rule only:
    the repository's own checks run this code from a mirror under the temp
    directory and render units into directories of their own, which harms
    nothing, whereas the same mirror claiming the real installation does.
    """
    # Becoming the owner of the real installation is refused from a throwaway
    # copy however it is reached -- fresh install, explicit --adopt, or a
    # missing record. The repository checks run validators from a mirror under
    # the temp directory which is then deleted; an installation recorded as
    # owned by it points at a path that is about to stop existing, and the
    # health check would be pointing there too.
    if live_install and Path(skill_dir).is_relative_to(
        Path(tempfile.gettempdir()).resolve()
    ):
        raise NotTheOwnerError(
            "Refusing to take ownership of the installation from a temporary "
            f"copy of the skill tree ({Path(skill_dir)})."
        )
    if adopt:
        return
    record = read_owner_record(unit_dir, installation_id)
    if record is not None:
        return
    if record is None:
        # No record is a fresh install only when there is nothing installed.
        # Treating it as one whenever the record is merely absent would let a
        # single deleted file disarm the guard: the next sync from any copy
        # would adopt, repoint the installation, and exit 0.
        if not registrations_present:
            return
        raise NotTheOwnerError(
            "Scheduler registrations are installed but no owner is recorded, so "
            "the checkout that installed them cannot be confirmed.\n  running "
            f"from: {Path(skill_dir)}\nRe-run from the owning checkout, or pass "
            "--adopt to record this one as the owner."
        )
