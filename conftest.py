"""Repository-wide pytest scaffolding.

Sits at the pytest rootdir (see `pytest.ini`'s `testpaths = tests hooks/tests
skills`), so this conftest.py's fixtures apply to every test collected under
`tests/`, `hooks/tests/`, and every `skills/*/tests/` directory -- not just
the top-level `tests/` suite (which has its own, narrower conftest.py).
"""
from __future__ import annotations

import pytest


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
