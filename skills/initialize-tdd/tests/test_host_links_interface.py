from __future__ import annotations

import importlib.util
import sys
from argparse import Namespace
from pathlib import Path


_HOST_LINKS = Path(__file__).resolve().parents[1] / "_rtx" / "_host_links.py"
sys.path.insert(0, str(_HOST_LINKS.parents[3] / "src"))
from officina.runtime.python_machine_interface import PythonMachineInterface  # noqa: E402
from officina.runtime.python_machine_interface_runner import load_interface  # noqa: E402

_SPEC = importlib.util.spec_from_file_location("initialize_tdd_host_links", _HOST_LINKS)
assert _SPEC is not None
host_links = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(host_links)


def test_interface_build_parser_accepts_project_dir() -> None:
    interface = host_links.Interface()
    parser = interface.build_parser()

    args = parser.parse_args(["project"])

    assert args.project_dir == "project"


def test_shared_runner_loads_interface_from_skill_root(monkeypatch) -> None:
    monkeypatch.chdir(_HOST_LINKS.parents[1])

    interface = load_interface("_rtx/_host_links.py:Interface")

    assert isinstance(interface, PythonMachineInterface)


def test_interface_run_creates_compat_aliases(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("# Instructions\n", encoding="utf-8")
    interface = host_links.Interface()

    result = interface.run(Namespace(project_dir=str(tmp_path)))

    assert result == 0
    alias = tmp_path / "CLAUDE.md"
    assert alias.is_symlink()
    assert alias.readlink() == Path("AGENTS.md")
