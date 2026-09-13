#!/usr/bin/env python3
"""Startup-only cross-host diagnostic for the Famulus dispatcher runtime."""

from __future__ import annotations

import os
import sys
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from llmhooks.lib.cross_host import CrossHostHook, HookInput, HookResult, parse_platform_args


def diagnose_dispatcher_runtime(
    *,
    environment: dict[str, str] | None = None,
    home: Path | None = None,
) -> str | None:
    """Return the launcher's dedicated-runtime diagnosis when it can be loaded."""
    try:
        runtime_environment = (
            os.environ.copy() if environment is None else environment.copy()
        )
        path_environment = runtime_environment.copy()
        path_environment.pop("FAMULUS_HOST", None)
        path_environment.pop("FAMULUS_PLUGIN_DATA", None)

        source_root = _REPO_ROOT / "src"
        if str(source_root) not in sys.path:
            sys.path.insert(0, str(source_root))

        from mcp_launcher import diagnose_runtime
        from officina.common.famulus_paths import resolve_famulus_paths

        paths = resolve_famulus_paths(
            platform=sys.platform,
            home=Path.home() if home is None else home,
            environ=path_environment,
        )
        return diagnose_runtime(paths.venv_python_path, runtime_environment)
    except Exception:  # noqa: BLE001
        return None


class DiagnoseDispatcherRuntimeHook(CrossHostHook):
    hook_name = "diagnose-dispatcher-runtime"

    event = "SessionStart"
    matcher = "startup"

    def build(self, hook_input: HookInput) -> HookResult:
        diagnosis = diagnose_dispatcher_runtime()
        return HookResult(
            additional_context=diagnosis,
            system_message=diagnosis,
        )


def main(argv: list[str] | None = None) -> int:
    host = parse_platform_args(argv)
    return DiagnoseDispatcherRuntimeHook().run(host)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"diagnose_dispatcher_runtime: error: {exc}", file=sys.stderr)
        sys.exit(0)
