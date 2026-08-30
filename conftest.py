"""Repository-wide pytest scaffolding.

Sits at the pytest rootdir, so its fixtures apply to every test collected under
`tests/`, `hooks/tests/`, `src/officina/wakeup/tests/`, and skill-owned test
directories -- not just the top-level `tests/` suite (which has its own,
narrower conftest.py).
"""
from __future__ import annotations

from itertools import count
from pathlib import Path

import pytest

from officina.blueprints.graph import (
    RepositoryBlueprintGraph,
    load_repository_blueprint_graph,
)


_LOCALAPPDATA_CASES = count()
_REPOSITORY_ROOT = Path(__file__).resolve().parent


@pytest.fixture
def ordinary_repository_graph(
    request: pytest.FixtureRequest,
) -> RepositoryBlueprintGraph:
    """Return an isolated live graph for ordinary repository contract tests.

    Canonical repository checks already prepare a graph snapshot and expose a
    function-scoped defensive copy as ``graph``. Direct pytest invocations do
    not install that runner plugin, so they load a fresh graph for this test.
    """

    try:
        candidate = request.getfixturevalue("graph")
    except pytest.FixtureLookupError as exc:
        if exc.argname != "graph":
            raise
        candidate = None
    if candidate is None:
        candidate = load_repository_blueprint_graph(_REPOSITORY_ROOT)
    if not isinstance(candidate, RepositoryBlueprintGraph):
        raise TypeError(
            "ordinary repository graph must be a RepositoryBlueprintGraph, "
            f"got {type(candidate).__name__}"
        )

    materialized_paths = (
        path
        for node in candidate.nodes.values()
        for path in (node.module_root, node.blueprint_path, node.gateway_path)
        if path is not None
    )
    mismatched = []
    for path in materialized_paths:
        if not isinstance(path, Path):
            raise TypeError(
                "ordinary repository graph paths must be pathlib.Path values, "
                f"got {type(path).__name__}"
            )
        resolved = (
            path.resolve()
            if path.is_absolute()
            else (_REPOSITORY_ROOT / path).resolve()
        )
        if not resolved.is_relative_to(_REPOSITORY_ROOT):
            mismatched.append(path)
    if mismatched:
        raise AssertionError(
            "ordinary repository graph belongs to a different materialized root: "
            f"{mismatched[0]} is outside {_REPOSITORY_ROOT}"
        )
    return candidate


@pytest.fixture(autouse=True)
def _isolate_xdg_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent ambient XDG_*_HOME environment variables from overriding an
    explicit `home=`/`--home` override passed by a test.

    `officina.common.famulus_paths.resolve_famulus_paths` honors
    `XDG_CONFIG_HOME`/`XDG_DATA_HOME`/`XDG_STATE_HOME` ahead of its `home`
    parameter on Linux -- intentional XDG Base Directory spec behavior for
    real users, but a hazard for tests. GitHub Actions' hosted ubuntu-latest
    runners set these env vars to the real runner home's
    `.config`/`.local/share`/`.local/state` directories, so a test that
    thought it was writing under an isolated `tmp_path` `home=` override was
    actually reading/writing the SAME real path as every other test in the
    same process -- a cross-test collision. This is what made
    `test_officina_google_credentials.py::test_install_client_refuses_different_client_without_replace`
    (and siblings across `tests/test_officina_google_credentials.py` and
    `skills/connect-google/tests/test_client_config.py`/`test_authorize_services.py`)
    fail with a `client.json already exists at /home/runner/.config/...`
    error even though each failing test itself correctly passed
    `home=tmp_path`: an earlier test in the same session had already
    written a real file at that ambient path, and every later test resolved
    to that same real path regardless of its own `home=` override.

    Clearing these three variables for every test makes `home=`/`--home`
    the sole source of truth during the test session, matching what each
    test actually asserts.
    """
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def _isolate_localappdata_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Give every test its own `LOCALAPPDATA`, mirroring `_isolate_xdg_env` above.

    `resolve_famulus_paths`'s Windows branch resolves *only* from the
    `LOCALAPPDATA` env var and never falls back to (or is influenced by) its
    `home` parameter -- unlike the Linux/XDG branch, there's no safe "clear
    it" option here, since an unset `LOCALAPPDATA` is itself a tested error
    path (`test_windows_requires_localappdata`). Left at whatever the real
    Windows runner has ambiently, every test that resolves Windows paths
    collides on that same real path regardless of its own `home=`/`tmp_path`
    override -- the same cross-test collision `_isolate_xdg_env` fixes for
    Linux, just via a variable that can't simply be cleared. Defaulting it to
    a unique path under pytest's session temp root gives every test a private
    value; a test that needs a specific value (or needs it unset) still wins
    by calling `monkeypatch.setenv`/`delenv` itself, since that runs after this
    fixture within the same test.

    The path is deliberately not created here. Most tests never exercise the
    Windows path resolver, so allocating a physical ``tmp_path`` directory for
    every collected case adds filesystem work without strengthening isolation.
    Code that actually writes beneath ``LOCALAPPDATA`` remains responsible for
    creating its normal application directories.
    """
    case_root = (
        tmp_path_factory.getbasetemp()
        / "localappdata"
        / f"case-{next(_LOCALAPPDATA_CASES)}"
    )
    monkeypatch.setenv("LOCALAPPDATA", str(case_root / "AppData" / "Local"))
