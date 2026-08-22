from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class RunRecord:
    job_name: str
    started_at: str
    finished_at: str
    process_exit_code: int
    success: bool
    reason: str = ""
    run_id: str = ""


def write_record(*, log_root: Path, record: RunRecord) -> None:
    directory = log_root / record.job_name
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = directory / "latest.json"
    temporary = directory / ".latest.json.tmp"
    temporary.write_text(json.dumps(asdict(record), indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, target)


def read_record(*, log_root: Path, job_name: str) -> dict[str, object] | None:
    try:
        value = json.loads((log_root / job_name / "latest.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


__all__ = ["RunRecord", "read_record", "write_record"]
