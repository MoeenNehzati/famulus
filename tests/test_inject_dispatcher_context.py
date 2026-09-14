"""Session-start guidance for the dispatcher."""

import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
HOOK = ROOT / "llmhooks" / "inject_dispatcher_context.py"
SPEC = importlib.util.spec_from_file_location("inject_dispatcher_context", HOOK)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_executable_interface_guidance_selects_its_exact_security_tool() -> None:
    assert "Security level: x" in MODULE.DISPATCHER_CORE
    assert "famulus_dispatcher.invoke_security_x" in MODULE.DISPATCHER_CORE
    assert "famulus_dispatcher.invoke`" not in MODULE.DISPATCHER_CORE
