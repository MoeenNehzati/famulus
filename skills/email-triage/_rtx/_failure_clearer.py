#!/usr/bin/env python3
"""Clear a latched triage failure without advancing the watermark.

Use this only after the reported failure's cause has been corrected and before
starting a fresh triage run. Keeping recovery separate from watermark updates
preserves the guard against skipping mail during the failed run.
"""
import json
import sys
from pathlib import Path

from officina.runtime.python_machine_interface import PythonArgvMachineInterface

SKILL_DIR = Path(__file__).resolve().parent.parent


if __package__:
    from ._state_paths import default_state_dir
else:
    from _state_paths import default_state_dir


STATUS_FILE = default_state_dir() / "status.json"


class Interface(PythonArgvMachineInterface):
    prog = "clear_failure.py"

    def run(self, argv: list[str]) -> int:
        return main(argv)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    reason = argv[0] if argv else "operator confirmed recovery"

    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    message = f"failure cleared: {reason}; watermark unchanged"
    STATUS_FILE.write_text(
        json.dumps({"result": "ok", "message": message}, indent=2)
    )
    print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
