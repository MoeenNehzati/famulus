"""File a reviewed Famulus feedback report as a public GitHub issue."""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import urllib.parse
from pathlib import Path
from typing import Any

from officina.common import toml_io
from officina.configuration.repository import (
    RepositoryConfigurationError,
    load_repository_configuration,
)
from officina.runtime.python_machine_interface import PythonMachineInterface

COMMAND_NAME = "gh"
ACCOUNT_PATTERN = re.compile(r"account\s+(\S+)")
# GitHub accepts long prefilled issue URLs, but proxies and browsers start
# truncating well before its own limit, so the body is dropped past this size
# rather than silently delivered incomplete.
MAX_URL_LENGTH = 6000
NOT_INSTALLED_REMEDIATION = (
    "The GitHub CLI is not installed, so the report cannot be filed directly. "
    "Install it from https://cli.github.com/ or through your usual package "
    "manager, then sign in with `gh auth login` and choose GitHub.com."
)
NOT_AUTHENTICATED_REMEDIATION = (
    "The GitHub CLI is installed but not signed in, so the report cannot be "
    "filed directly. Sign in with `gh auth login`, choose GitHub.com, and "
    "confirm with `gh auth status`."
)


def _die(message: str) -> None:
    print(f"Error: {message}", file=sys.stderr)
    raise SystemExit(1)


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _which(name: str) -> str | None:
    return shutil.which(name)


def _run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        **kwargs,
    )


def _feedback_config() -> Any:
    config = _repository_root() / toml_io.repository_config_filename()
    try:
        return load_repository_configuration(config)
    except RepositoryConfigurationError as error:
        _die(f"unusable repository configuration {config}: {error}")


def feedback_address() -> str | None:
    """Return the configured feedback email address, when one is configured."""
    return _feedback_config().feedback_email


def feedback_repository() -> str:
    """Return the `owner/name` feedback repository from the owning configuration."""
    configuration = _feedback_config()
    repository = configuration.feedback_github_repo
    if repository is None:
        _die(f"missing feedback.github_repo in {configuration.config_path}")
    return repository


def delivery_route() -> dict[str, Any]:
    """Report which delivery route is available without contacting the network."""
    command = _which(COMMAND_NAME)
    if command is None:
        return {
            "route": "url",
            "command_installed": False,
            "command_authenticated": False,
            "account": None,
            "remediation": NOT_INSTALLED_REMEDIATION,
        }
    status = _run([COMMAND_NAME, "auth", "status"])
    authenticated = status.returncode == 0
    account = None
    if authenticated:
        match = ACCOUNT_PATTERN.search(f"{status.stdout}\n{status.stderr}")
        account = match.group(1) if match else None
    return {
        "route": "command" if authenticated else "url",
        "command_installed": True,
        "command_authenticated": authenticated,
        "account": account,
        "remediation": None if authenticated else NOT_AUTHENTICATED_REMEDIATION,
    }


def prefilled_url(repository: str, title: str, body: str) -> dict[str, Any]:
    """Build a prefilled new-issue URL, dropping a body that would not survive it."""
    base = f"https://github.com/{repository}/issues/new?"
    full = base + urllib.parse.urlencode({"title": title, "body": body})
    if len(full) <= MAX_URL_LENGTH:
        return {"url": full, "body_included": True}
    return {
        "url": base + urllib.parse.urlencode({"title": title}),
        "body_included": False,
    }


def file_issue(
    repository: str, title: str, body_file: Path, *, authenticated: bool
) -> dict[str, Any]:
    """File the report as an issue, or return the URL route when that is unavailable."""
    if not body_file.is_file():
        _die(f"missing report file: {body_file}")
    body = body_file.read_text(encoding="utf-8")
    if not authenticated or _which(COMMAND_NAME) is None:
        result = prefilled_url(repository, title, body)
        remediation = (
            NOT_INSTALLED_REMEDIATION
            if _which(COMMAND_NAME) is None
            else NOT_AUTHENTICATED_REMEDIATION
        )
        return {
            "route": "url",
            "url": result["url"],
            "repository": repository,
            "body_included": result["body_included"],
            "remediation": remediation,
        }
    completed = _run(
        [
            COMMAND_NAME,
            "issue",
            "create",
            "--repo",
            repository,
            "--title",
            title,
            "--body-file",
            str(body_file),
        ]
    )
    if completed.returncode != 0:
        _die(
            "filing the issue failed and was not retried: "
            f"{(completed.stderr or completed.stdout).strip()}"
        )
    url = completed.stdout.strip().splitlines()[-1] if completed.stdout.strip() else ""
    if not url.startswith("https://"):
        _die(f"the issue was not confirmed by a returned URL: {completed.stdout.strip()}")
    return {
        "route": "command",
        "url": url,
        "repository": repository,
        "body_included": True,
        "remediation": None,
    }


class CheckRoute(PythonMachineInterface):
    prog = "check-route"

    def run(self, args: argparse.Namespace) -> int:
        payload = {
            "repository": feedback_repository(),
            "email": feedback_address(),
            **delivery_route(),
        }
        print(json.dumps(payload, sort_keys=True))
        return 0


class FileIssue(PythonMachineInterface):
    prog = "file-issue"

    def build_parser(self) -> argparse.ArgumentParser:
        parser = super().build_parser()
        parser.add_argument("--title", required=True)
        parser.add_argument("--body-file", required=True)
        return parser

    def run(self, args: argparse.Namespace) -> int:
        repository = feedback_repository()
        route = delivery_route()
        payload = file_issue(
            repository,
            args.title,
            Path(args.body_file),
            authenticated=route["command_authenticated"],
        )
        print(json.dumps({**payload, "account": route["account"]}, sort_keys=True))
        return 0
