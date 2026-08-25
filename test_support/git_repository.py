"""Deterministic Git repository fixture for ordinary tests."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
from typing import Mapping

from officina.git.provenance import run_git


def isolated_git_environment(
    additions: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return a child environment without ambient Git repository routing."""

    environment = os.environ.copy()
    for name in tuple(environment):
        if name.startswith("GIT_"):
            environment.pop(name, None)
    environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    if additions is not None:
        environment.update(additions)
    return environment


@dataclass(frozen=True)
class GitTestRepository:
    """A test repository with fixed local identity and portability settings."""

    root: Path

    @classmethod
    def create(
        cls,
        root: Path,
        *,
        branch: str = "main",
        filemode: bool = True,
    ) -> "GitTestRepository":
        target = Path(root)
        target.mkdir(parents=True, exist_ok=False)
        return cls._initialize(target, branch=branch, filemode=filemode)

    @classmethod
    def initialize_existing_empty(
        cls,
        root: Path,
        *,
        branch: str = "main",
        filemode: bool = True,
    ) -> "GitTestRepository":
        """Initialize an existing empty directory such as pytest's tmp_path."""

        target = Path(root)
        if not target.is_dir():
            raise FileNotFoundError(target)
        if any(target.iterdir()):
            raise ValueError(f"Git test repository target must be empty: {target}")
        return cls._initialize(target, branch=branch, filemode=filemode)

    @classmethod
    def _initialize(
        cls,
        target: Path,
        *,
        branch: str,
        filemode: bool,
    ) -> "GitTestRepository":
        repository = cls(target.resolve())
        repository.git("init", "--initial-branch", branch, "--quiet")
        repository.git("config", "user.name", "Famulus Tests")
        repository.git(
            "config",
            "user.email",
            "famulus-tests@example.invalid",
        )
        repository.git("config", "core.autocrlf", "false")
        repository.git(
            "config",
            "core.filemode",
            "true" if filemode else "false",
        )
        return repository

    def git(
        self,
        *args: str,
        check: bool = True,
        input_bytes: bytes | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        """Run Git through the production ambient-isolation boundary."""

        return run_git(
            self.root,
            *args,
            check=check,
            input_bytes=input_bytes,
        )
