"""Contracts for refined repository-check task and pytest-node selection."""

from __future__ import annotations

from pathlib import Path

import pytest

import officina.repository.checks.runner as runner


def test_task_alias_and_selectors_reach_one_existing_runner_invocation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Catch a selector route that bypasses the canonical local runner."""

    test_file = tmp_path / "tests" / "test_example.py"
    test_file.parent.mkdir()
    test_file.write_text("", encoding="utf-8")
    calls: list[tuple[Path, str, dict[str, object]]] = []
    monkeypatch.setattr(runner, "_pytest_xdist_available", lambda: True)
    monkeypatch.setattr(
        runner,
        "run_suite",
        lambda repo_root, suite, **kwargs: calls.append((repo_root, suite, kwargs))
        or 0,
    )

    assert runner.main(
        [
            "--repo-root",
            str(tmp_path),
            "--suite",
            "full",
            "--task",
            "tests:shared",
            "--selector",
            "tests/test_example.py::test_case",
            "--jobs",
            "1",
        ]
    ) == 0

    assert calls[0][2]["task_id"] == "tests:shared"
    assert calls[0][2]["selectors"] == (
        "tests/test_example.py::test_case",
    )


def test_selector_normalization_is_stable_and_repository_bounded(
    tmp_path: Path,
) -> None:
    """Catch absolute, escaping, missing, or duplicate test selectors."""

    test_file = tmp_path / "tests" / "test_example.py"
    test_file.parent.mkdir()
    test_file.write_text("", encoding="utf-8")

    assert runner.normalize_test_selectors(
        tmp_path,
        "tests:shared",
        (
            "tests\\test_example.py::test_case[param]",
            "tests/test_example.py::test_case[param]",
        ),
    ) == ("tests/test_example.py::test_case[param]",)

    for invalid in (
        str(test_file.resolve()),
        "../tests/test_example.py",
        "tests/missing.py",
        "tests/test_example.py\n--collect-only",
    ):
        with pytest.raises(ValueError):
            runner.normalize_test_selectors(
                tmp_path,
                "tests:shared",
                (invalid,),
            )


def test_specialized_task_rejects_a_selector_owned_by_another_task(
    tmp_path: Path,
) -> None:
    """Catch task labels that do not constrain the selected pytest file."""

    browser = tmp_path / "tests" / "test_visualization_browser.py"
    ordinary = tmp_path / "tests" / "test_example.py"
    browser.parent.mkdir()
    browser.write_text("", encoding="utf-8")
    ordinary.write_text("", encoding="utf-8")

    assert runner.normalize_test_selectors(
        tmp_path,
        "tests:browser",
        ("tests/test_visualization_browser.py::test_page",),
    ) == ("tests/test_visualization_browser.py::test_page",)
    with pytest.raises(ValueError, match="does not belong"):
        runner.normalize_test_selectors(
            tmp_path,
            "tests:browser",
            ("tests/test_example.py::test_case",),
        )


def test_selected_phase_uses_only_the_normalized_pytest_nodes() -> None:
    """Catch targeted execution that silently falls back to full discovery."""

    selectors = (
        "tests/test_a.py::test_one",
        "skills/demo/tests/test_b.py::test_two",
    )
    command = runner._pytest_phase_command(
        "full",
        "tests:shared",
        verbose=False,
        jobs=2,
        cache_dir=Path("cache"),
        timing_path=None,
        selectors=selectors,
    )

    assert command[-2:] == list(selectors)


def test_selector_requires_one_pytest_task(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Catch ambiguous selectors without a task or on validator execution."""

    monkeypatch.setattr(runner, "_pytest_xdist_available", lambda: True)
    for arguments in (
        ["--selector", "tests/test_example.py", "--jobs", "1"],
        [
            "--task",
            "validators",
            "--selector",
            "tests/test_example.py",
            "--jobs",
            "1",
        ],
    ):
        with pytest.raises(SystemExit) as exc:
            runner.main(["--repo-root", str(tmp_path), *arguments])
        assert exc.value.code == 2


def test_native_tasks_use_their_exact_smoke_nodes() -> None:
    """Catch native probe tasks that bypass the timed canonical runner."""

    keyring = runner._pytest_phase_command(
        "full",
        "native:keyring",
        verbose=False,
        jobs=1,
        cache_dir=Path("cache"),
        timing_path=None,
    )
    scheduler = runner._pytest_phase_command(
        "full",
        "native:scheduler",
        verbose=False,
        jobs=1,
        cache_dir=Path("cache"),
        timing_path=None,
    )

    assert keyring[-1].endswith(
        "tests/test_officina_secret_store.py::test_default_backend_native_roundtrip_when_available"
    )
    assert scheduler[-1].endswith(
        "skills/recurring-tasks/_rtx/tests/test_scheduler_live_smoke.py"
    )
