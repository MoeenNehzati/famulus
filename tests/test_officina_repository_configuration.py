"""Behavioral tests for the repository-owned Officina configuration."""

from __future__ import annotations

import os
import importlib
from pathlib import Path

import pytest

from officina.common.configured_schema import (
    ConfiguredSchemaError,
    validate_configuration,
)


def _repository_configuration_module():
    return importlib.import_module("officina.common.repository_configuration")


def _repository(tmp_path: Path, roots: tuple[str, ...] = ("skills", "src/officina")) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    for relative in roots:
        (repository / relative).mkdir(parents=True)
    rendered_roots = ", ".join(f'"{relative}"' for relative in roots)
    (repository / "officina.toml").write_text(
        "schema_version = 1\n\n"
        "[modules]\n"
        f"roots = [{rendered_roots}]\n",
        encoding="utf-8",
    )
    return repository


def test_loads_exact_absolute_repository_configuration(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    module = _repository_configuration_module()

    configuration = module.load_repository_configuration(repository / "officina.toml")

    assert configuration.schema_version == 1
    assert configuration.config_path == repository / "officina.toml"
    assert configuration.repository_root == repository
    assert configuration.module_roots == (
        repository / "skills",
        repository / "src/officina",
    )
    assert configuration.feedback_email is None


def test_loads_configured_feedback_email(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    config_path = repository / "officina.toml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8")
        + '\n[feedback]\nemail = "maintainer@example.com"\n',
        encoding="utf-8",
    )
    module = _repository_configuration_module()

    configuration = module.load_repository_configuration(config_path)

    assert configuration.feedback_email == "maintainer@example.com"


def test_resolution_ignores_cwd_and_ai_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repository(tmp_path)
    ambient = tmp_path / "ambient"
    ambient.mkdir()
    monkeypatch.chdir(ambient)
    monkeypatch.setenv("AI", str(ambient / "wrong-repository"))
    module = _repository_configuration_module()

    configuration = module.load_repository_configuration(repository / "officina.toml")

    assert configuration.repository_root == repository
    assert configuration.module_roots[0] == repository / "skills"


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ("schema_version = 2\n[modules]\nroots = [\"skills\"]\n", "schema_version"),
        (
            "schema_version = 1\nunknown = true\n[modules]\nroots = [\"skills\"]\n",
            "unknown",
        ),
        ("schema_version = 1\n[modules]\nroots = []\n", "roots"),
        (
            "schema_version = 1\n[modules]\nroots = [\"skills\", \"skills\"]\n",
            "duplicate",
        ),
        ("schema_version = 1\n[modules]\nroots = [\"/skills\"]\n", "relative"),
        ("schema_version = 1\n[modules]\nroots = [\"../skills\"]\n", "unsafe"),
        ("schema_version = 1\n[modules]\nroots = [\"skills//nested\"]\n", "unsafe"),
        ("schema_version = 1\n[modules]\nroots = [\"skills\\\\nested\"]\n", "unsafe"),
        ("schema_version = 1\n[modules]\nroots = [1]\n", "string"),
        (
            "schema_version = 1\n[modules]\nroots = [\"skills\"]\n"
            "[feedback]\nunknown = \"x\"\n",
            "unknown feedback",
        ),
        (
            "schema_version = 1\n[modules]\nroots = [\"skills\"]\n"
            "[feedback]\nemail = 1\n",
            "feedback.email",
        ),
        (
            "schema_version = 1\n[modules]\nroots = [\"skills\"]\n"
            "[feedback]\nemail = \"Name <person@example.com>\"\n",
            "feedback.email",
        ),
        (
            "schema_version = 1\n[modules]\nroots = [\"skills\"]\n"
            "[feedback]\nemail = \"one@example.com, two@example.com\"\n",
            "feedback.email",
        ),
        (
            "schema_version = 1\n[modules]\nroots = [\"skills\"]\n"
            "[feedback]\nemail = \"a@\"\n",
            "feedback.email",
        ),
        (
            "schema_version = 1\n[modules]\nroots = [\"skills\"]\n"
            "[feedback]\nemail = \"a(comment)@example.com\"\n",
            "feedback.email",
        ),
        (
            "schema_version = 1\n[modules]\nroots = [\"skills\"]\n"
            "[feedback]\nemail = \"a..b@example.com\"\n",
            "feedback.email",
        ),
        (
            "schema_version = 1\n[modules]\nroots = [\"skills\"]\n"
            "[feedback]\nemail = \"a@example..com\"\n",
            "feedback.email",
        ),
    ],
)
def test_rejects_malformed_repository_configuration(
    tmp_path: Path, document: str, message: str
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "skills").mkdir()
    config_path = repository / "officina.toml"
    config_path.write_text(document, encoding="utf-8")
    module = _repository_configuration_module()

    with pytest.raises(module.RepositoryConfigurationError, match=message):
        module.load_repository_configuration(config_path)


def test_rejects_relative_config_path(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    module = _repository_configuration_module()

    with pytest.raises(module.RepositoryConfigurationError, match="absolute"):
        module.load_repository_configuration(
            Path(os.path.relpath(repository / "officina.toml", Path.cwd()))
        )


def test_rejects_wrong_config_filename(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    module = _repository_configuration_module()
    alternate = repository / "repository.toml"
    alternate.write_bytes((repository / "officina.toml").read_bytes())

    with pytest.raises(
        module.RepositoryConfigurationError,
        match="unexpected repository configuration filename",
    ):
        module.load_repository_configuration(alternate)


def test_rejects_symlinked_module_root(tmp_path: Path) -> None:
    repository = _repository(tmp_path, roots=())
    module = _repository_configuration_module()
    outside = tmp_path / "outside"
    outside.mkdir()
    (repository / "skills").symlink_to(outside, target_is_directory=True)
    (repository / "officina.toml").write_text(
        "schema_version = 1\n[modules]\nroots = [\"skills\"]\n",
        encoding="utf-8",
    )

    with pytest.raises(module.RepositoryConfigurationError, match="symlink"):
        module.load_repository_configuration(repository / "officina.toml")


def test_rejects_symlinked_config_file(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    module = _repository_configuration_module()
    real_config = repository / "real.toml"
    (repository / "officina.toml").replace(real_config)
    (repository / "officina.toml").symlink_to(real_config)

    with pytest.raises(module.RepositoryConfigurationError, match="symlink"):
        module.load_repository_configuration(repository / "officina.toml")


def test_central_schema_accepts_repository_configuration_mapping() -> None:
    validate_configuration(
        {
            "schema_version": 1,
            "modules": {"roots": ["skills", "src/officina"]},
            "feedback": {"email": "maintainer@example.com"},
        }
    )


@pytest.mark.parametrize(
    "document",
    [
        {"schema_version": 1, "modules": {"roots": []}},
        {"schema_version": 1, "modules": {"roots": ["../skills"]}},
        {"schema_version": 1, "modules": {"roots": ["skills", "skills"]}},
        {"schema_version": 1, "modules": {"roots": ["skills"]}, "extra": True},
        {
            "schema_version": 1,
            "modules": {"roots": ["skills"]},
            "feedback": {"email": "Name <person@example.com>"},
        },
        {
            "schema_version": 1,
            "modules": {"roots": ["skills"]},
            "feedback": {"email": "one@example.com, two@example.com"},
        },
        {
            "schema_version": 1,
            "modules": {"roots": ["skills"]},
            "feedback": {"email": "a@"},
        },
        {
            "schema_version": 1,
            "modules": {"roots": ["skills"]},
            "feedback": {"email": "a(comment)@example.com"},
        },
        {
            "schema_version": 1,
            "modules": {"roots": ["skills"]},
            "feedback": {"email": "a..b@example.com"},
        },
        {
            "schema_version": 1,
            "modules": {"roots": ["skills"]},
            "feedback": {"email": "a@example..com"},
        },
        {
            "schema_version": 1,
            "modules": {"roots": ["skills"]},
            "feedback": {"email": "a@example.com\n"},
        },
    ],
)
def test_central_schema_rejects_invalid_repository_configuration_mapping(
    document: dict[str, object],
) -> None:
    with pytest.raises(ConfiguredSchemaError, match="invalid configuration"):
        validate_configuration(document)
