"""Fixed-operation dispatcher adapters for the agent-timeline reader."""
from __future__ import annotations

from officina.runtime.python_machine_interface import PythonArgvMachineInterface

from ._agent_timeline import main as _timeline_main


class _TimelineInterface(PythonArgvMachineInterface):
    operation: str

    def run(self, argv: list[str]) -> int:
        return _timeline_main(self.operation, argv, prog=self.prog)


class ListSessions(_TimelineInterface):
    operation = "list-sessions"
    prog = "list-sessions"


class ShowLatestSession(_TimelineInterface):
    operation = "show-latest-session"
    prog = "show-latest-session"


class ShowSession(_TimelineInterface):
    operation = "show-session"
    prog = "show-session"


class ShowRun(_TimelineInterface):
    operation = "show-run"
    prog = "show-run"


class ReadRunJson(_TimelineInterface):
    operation = "read-run-json"
    prog = "read-run-json"


if __name__ == "__main__":
    raise SystemExit(ListSessions().main())
