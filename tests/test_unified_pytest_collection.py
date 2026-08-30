"""Regression coverage for collection across independently packaged runtimes."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CANONICAL_TEST_ROOTS = (
    "tests",
    "hooks/tests",
    "skills",
    "src/officina/rutter/tests",
    "src/officina/wakeup/tests",
)
RUNTIME_TEST_ROOTS = (
    "skills/list-manager/_rtx/tests",
    "skills/email-triage/_rtx/tests",
)


def _collected_nodes(output: str) -> set[str]:
    """Return the node IDs printed by quiet collect-only output."""
    return {line for line in output.splitlines() if "::" in line}


def _collect(*paths: str) -> subprocess.CompletedProcess[str]:
    """Collect repository tests through a real pytest process."""
    return subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", *paths],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_no_argument_collection_matches_explicit_repository_roots() -> None:
    """Catch a pytest exclusion that silently drops a canonical test root."""
    native = _collect()
    explicit = _collect(*CANONICAL_TEST_ROOTS)

    assert native.returncode == 0, native.stdout + native.stderr
    assert explicit.returncode == 0, explicit.stdout + explicit.stderr
    native_nodes = _collected_nodes(native.stdout)
    explicit_nodes = _collected_nodes(explicit.stdout)
    assert native_nodes == explicit_nodes
    assert {
        node
        for node in native_nodes
        if node.startswith("skills/initialize-tdd/_rtx/tests/")
    } == {
        "skills/initialize-tdd/_rtx/tests/test_host_links_interface.py::"
        "test_interface_build_parser_accepts_project_dir",
        "skills/initialize-tdd/_rtx/tests/test_host_links_interface.py::"
        "test_shared_runner_loads_interface_from_skill_root",
        "skills/initialize-tdd/_rtx/tests/test_host_links_interface.py::"
        "test_interface_run_creates_compat_aliases",
    }


def test_collects_two_runtime_roots_without_module_name_collisions() -> None:
    """Collect separate `_rtx` trees in one pytest process."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            *RUNTIME_TEST_ROOTS,
        ],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "skills/list-manager/_rtx/tests/test_lists.py::test_init_executable_smoke" in result.stdout
    assert (
        "skills/email-triage/_rtx/tests/test_filter_envelopes.py::"
        "test_load_cutoff_missing_watermark_warns_and_records_status"
    ) in result.stdout
