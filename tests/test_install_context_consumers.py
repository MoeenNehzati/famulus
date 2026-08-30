from __future__ import annotations

import importlib
import importlib.util
import ast
import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest
import yaml

from officina.common.famulus_paths import resolve_famulus_paths
from officina.install.context import (
    load_or_create_development_installation_id,
    resolve_installation_context,
)
from officina.recurring import runtime as recurring_runtime
from officina.recurring.runtime import ManagedSchedule, _bounded_environment
from officina.wakeup import policies, store
from officina.wakeup.providers.claude import ClaudeAdapter
from officina.wakeup.providers.codex import CodexAdapter


ROOT = Path(__file__).resolve().parents[1]
MILESTONE_WRITER = ROOT / "skills" / "milestone-logging" / "_rtx" / "_milestone_writer.py"
AGENT_TIMELINE = ROOT / "skills" / "milestone-logging" / "_rtx" / "_agent_timeline.py"

_CONSUMER_ROOTS = (
    "skills/milestone-logging/_rtx/_milestone_writer.py",
    "skills/milestone-logging/_rtx/_agent_timeline.py",
    "skills/connect-google/_rtx",
    "skills/cloud-files/_rtx",
    "skills/online-calendar/_rtx",
    "skills/email-client/_rtx",
    "skills/email-triage/_rtx",
    "skills/list-manager/_rtx",
    "skills/find-handoff-candidates/_rtx",
    "skills/node-drift/_rtx",
    "src/officina/wakeup",
    "src/officina/credentials/google.py",
    "skills/recurring-tasks/_rtx/_assistant_desktop_notify.py",
)
_PATH_TERM = (
    "Path.home(", "expanduser(", "XDG_", "APPDATA", "LOCALAPPDATA",
    "CODEX_HOME", "CLAUDE_CONFIG_DIR", "CLAUDE_HOME", "ASSISTANT_LOGS",
    "LLM_WAKEUP_HOME", "resolve_famulus_paths(",
)
_INVENTORY = {
    ("skills/milestone-logging/_rtx/_milestone_writer.py", "<module>"): "process override",
    ("skills/milestone-logging/_rtx/_agent_timeline.py", "<module>"): "development-isolated",
    ("skills/recurring-tasks/_rtx/_assistant_desktop_notify.py", "_default_log_path"): "development-isolated",
    ("skills/recurring-tasks/_rtx/_assistant_desktop_notify.py", "_ensure_linux_gui_env"): "process override",
    ("skills/recurring-tasks/_rtx/_assistant_desktop_notify.py", "_notify_linux"): "process override",
    ("skills/connect-google/_rtx/_client_config.py", "run_client_status"): "development-isolated",
    ("skills/connect-google/_rtx/_client_config.py", "run_install_client"): "development-isolated",
    ("skills/connect-google/_rtx/_loopback_oauth.py", "run_authorize_services"): "development-isolated",
    ("skills/cloud-files/_rtx/_oauth_bootstrap.py", "<module>"): "development-isolated",
    ("skills/cloud-files/_rtx/_oauth_bootstrap.py", "main"): "process override",
    ("skills/cloud-files/_rtx/_drive_gateway.py", "default_config_path"): "development-isolated",
    ("skills/cloud-files/_rtx/_drive_gateway.py", "default_credentials_path"): "development-isolated",
    ("skills/cloud-files/_rtx/_drive_gateway.py", "load_config"): "development-isolated",
    ("skills/cloud-files/_rtx/_drive_gateway.py", "get_access_token"): "development-isolated",
    ("skills/cloud-files/_rtx/_ensure_oauth.py", "use_google_credential_file"): "process override",
    ("skills/cloud-files/_rtx/_ensure_oauth.py", "_existing_binding_subject"): "process override",
    ("skills/cloud-files/_rtx/_ensure_oauth.py", "main"): "process override",
    ("skills/online-calendar/_rtx/_oauth_bootstrap.py", "<module>"): "development-isolated",
    ("skills/online-calendar/_rtx/_gcal_client.py", "get_access_token"): "development-isolated",
    ("skills/online-calendar/_rtx/_ensure_oauth.py", "use_google_credential_file"): "process override",
    ("skills/online-calendar/_rtx/_ensure_oauth.py", "main"): "process override",
    ("skills/email-client/_rtx/_email_accounts.py", "<module>"): "development-isolated",
    ("skills/email-client/_rtx/_email_accounts.py", "accounts_use_google_credential_file"): "process override",
    ("skills/email-client/_rtx/_email_accounts.py", "cmd_use_google_credential_file"): "process override",
    ("skills/email-client/_rtx/_oauth_tokens.py", "get_gmail_access_token"): "development-isolated",
    ("skills/list-manager/_rtx/_yaml_store.py", "_cloud_lock_dir"): "development-isolated",
    ("skills/list-manager/_rtx/_yaml_store.py", "_cloud_cache_dir"): "development-isolated",
    ("skills/find-handoff-candidates/_rtx/_codex_parser.py", "home_dir"): "development-isolated",
    ("skills/find-handoff-candidates/_rtx/_claude_parser.py", "home_dir"): "development-isolated",
    ("skills/node-drift/_rtx/_check_drift_state.py", "requested_scopes"): "process override",
    ("skills/node-drift/_rtx/_skill_sources/_codex_skill_source.py", "sources"): "development-isolated",
    ("skills/node-drift/_rtx/_skill_sources/_claude_skill_source.py", "_plugin_sources"): "process override",
    ("skills/node-drift/_rtx/_skill_sources/_claude_skill_source.py", "sources"): "development-isolated",
    ("src/officina/wakeup/store.py", "data_dir"): "process override",
    ("src/officina/wakeup/doctor.py", "_provider_executable"): "process override",
    ("src/officina/wakeup/claude_codex_service.py", "_provider_executable"): "process override",
    ("src/officina/wakeup/providers/codex.py", "transcript_root"): "development-isolated",
    ("src/officina/wakeup/providers/codex.py", "indexed_sessions"): "development-isolated",
    ("src/officina/wakeup/providers/codex.py", "cwd"): "process override",
    ("src/officina/wakeup/providers/codex.py", "executable_candidates"): "development-isolated",
    ("src/officina/wakeup/providers/claude.py", "transcript_root"): "development-isolated",
    ("src/officina/wakeup/providers/claude.py", "cwd"): "process override",
    ("src/officina/wakeup/providers/claude.py", "executable_candidates"): "development-isolated",
    ("src/officina/credentials/google.py", "canonical_client_path"): "development-isolated",
    ("src/officina/credentials/google.py", "_credentials_registry_path"): "development-isolated",
    ("src/officina/credentials/google.py", "_credential_files_dir"): "development-isolated",
    ("src/officina/credentials/google.py", "load_credential_file"): "process override",
}
for _module in (
    "_failure_sentinel.py", "_failure_clearer.py", "_watermark_writer.py",
    "_watermark_floor.py", "_write_metrics.py", "_envelope_gate.py",
    "_finalize_run.py",
):
    _INVENTORY[(f"skills/email-triage/_rtx/{_module}", "default_state_dir")] = "development-isolated"
for _module in ("_decision_sink.py", "_log_compactor.py"):
    _INVENTORY[(f"skills/email-triage/_rtx/{_module}", "triage_log_path")] = "development-isolated"


def _scanned_consumers() -> set[tuple[str, str]]:
    files: set[Path] = set()
    for relative in _CONSUMER_ROOTS:
        path = ROOT / relative
        if path.is_file():
            files.add(path)
        else:
            files.update(
                child for child in path.rglob("*.py")
                if "tests" not in child.parts and "blueprints" not in child.parts
            )
    found: set[tuple[str, str]] = set()
    for path in files:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        functions = [
            node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        for line_number, line in enumerate(source.splitlines(), 1):
            if not any(term in line for term in _PATH_TERM):
                continue
            enclosing = [
                node for node in functions
                if node.lineno <= line_number <= (node.end_lineno or node.lineno)
            ]
            name = min(enclosing, key=lambda node: (node.end_lineno or node.lineno) - node.lineno).name if enclosing else "<module>"
            found.add((path.relative_to(ROOT).as_posix(), name))
    return found - {
        ("skills/find-handoff-candidates/_rtx/_claude_parser.py", "<module>"),
        ("skills/find-handoff-candidates/_rtx/_codex_parser.py", "<module>"),
        ("src/officina/wakeup/store.py", "<module>"),
    }


def _load_source(name: str, relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


DEVELOPMENT_ACTIVATION = _load_source(
    "task6_consumer_development_activation",
    "skills/dev-activation/_rtx/_development_activation.py",
)


def _development_env(tmp_path: Path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    host = tmp_path / "host"
    host.mkdir()
    base = {
        "HOME": str(host),
        "PATH": os.environ.get("PATH", ""),
        "DISPLAY": ":17",
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/17/bus",
        "HTTPS_PROXY": "http://proxy.invalid:8080",
        "SSL_CERT_FILE": "/etc/ssl/certs/ca-certificates.crt",
    }
    installation_id = load_or_create_development_installation_id(
        checkout, platform=sys.platform, home=host, environ=base
    )
    context = resolve_installation_context(
        mode="development",
        source_root=checkout,
        development_root=checkout,
        platform=sys.platform,
        home=host,
        environ=base,
        installation_id=installation_id,
    )
    return DEVELOPMENT_ACTIVATION.build_activation_environment(
        checkout, environ=base, platform=sys.platform
    ), checkout, context


def _standard_env(tmp_path: Path) -> tuple[dict[str, str], Path]:
    home = tmp_path / "standard-home"
    home.mkdir(parents=True)
    env = {
        "HOME": str(home),
        "PATH": os.environ.get("PATH", ""),
        "CODEX_HOME": str(home / ".codex"),
        "CLAUDE_CONFIG_DIR": str(home / ".claude"),
    }
    if sys.platform == "win32":
        env.update(
            {
                "USERPROFILE": str(home),
                "LOCALAPPDATA": str(home / "AppData" / "Local"),
                "APPDATA": str(home / "AppData" / "Roaming"),
            }
        )
    return env, home


def _load_package_source(package: str, relative: str):
    path = ROOT / relative
    package_module = types.ModuleType(package)
    package_module.__path__ = [str(path.parent)]
    sys.modules[package] = package_module
    spec = importlib.util.spec_from_file_location(f"{package}.{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("consumer", "classification"),
    sorted(_INVENTORY.items()),
    ids=lambda value: ":".join(value) if isinstance(value, tuple) else value,
)
def test_named_path_consumer_inventory(
    consumer: tuple[str, str], classification: str
) -> None:
    assert consumer in _scanned_consumers()
    assert classification in {
        "standard",
        "development-isolated",
        "process override",
        "leak",
    }


def test_named_path_inventory_equals_scoped_production_scan() -> None:
    scanned = _scanned_consumers()
    assert set(_INVENTORY) == scanned, (
        f"unscanned={sorted(set(_INVENTORY) - scanned)!r}; "
        f"unclassified={sorted(scanned - set(_INVENTORY))!r}"
    )


def test_milestone_follows_selected_home_and_process_override(tmp_path: Path) -> None:
    env, checkout, _ = _development_env(tmp_path)
    env.update({"CODEX_SESSION_ID": "consumer-test", "CODEX_THREAD_ID": "thread"})
    result = subprocess.run(
        [sys.executable, str(MILESTONE_WRITER), "--path"],
        env=env,
        cwd=checkout,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        check=True,
    )
    assert Path(result.stdout.strip()).is_relative_to(checkout / ".famulus")

    override = tmp_path / "process-only-logs"
    env["ASSISTANT_LOGS"] = str(override)
    result = subprocess.run(
        [sys.executable, str(MILESTONE_WRITER), "--path"],
        env=env,
        cwd=checkout,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        check=True,
    )
    assert Path(result.stdout.strip()).is_relative_to(override)
    activation = checkout / ".famulus" / "activation.json"
    assert not activation.exists() or "ASSISTANT_LOGS" not in activation.read_text()

    standard_env, standard_home = _standard_env(tmp_path / "standard")
    standard_env.update({"CODEX_SESSION_ID": "consumer-test", "CODEX_THREAD_ID": "thread"})
    result = subprocess.run(
        [sys.executable, str(MILESTONE_WRITER), "--path"],
        env=standard_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        check=True,
    )
    assert Path(result.stdout.strip()).is_relative_to(standard_home)


def test_agent_timeline_reads_only_the_selected_log_root(tmp_path: Path) -> None:
    env, checkout, _ = _development_env(tmp_path)
    selected_logs = checkout / ".famulus" / "home" / ".assistant-logs"
    day = selected_logs / "2026-08-22"
    day.mkdir(parents=True)
    (day / "selected.session.jsonl").write_text("{}\n", encoding="utf-8")
    host_logs = tmp_path / "host" / ".assistant-logs" / "2026-08-22"
    host_logs.mkdir(parents=True)
    (host_logs / "host.session.jsonl").write_text("{}\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(AGENT_TIMELINE), "--list"],
        env=env,
        cwd=checkout,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        check=True,
    )
    assert "selected" in result.stdout
    assert "host" not in result.stdout

    standard_env, standard_home = _standard_env(tmp_path / "standard-timeline")
    standard_day = standard_home / ".assistant-logs" / "2026-08-22"
    standard_day.mkdir(parents=True)
    (standard_day / "standard.session.jsonl").write_text("{}\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(AGENT_TIMELINE), "--list"],
        env=standard_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        check=True,
    )
    assert "standard" in result.stdout
    assert "selected" not in result.stdout


@pytest.mark.parametrize(
    ("relative", "function"),
    [
        pytest.param("skills/email-triage/_rtx/_failure_sentinel.py", "default_state_dir", id="triage-failure"),
        pytest.param("skills/email-triage/_rtx/_failure_clearer.py", "default_state_dir", id="triage-clear"),
        pytest.param("skills/email-triage/_rtx/_watermark_writer.py", "default_state_dir", id="triage-watermark"),
        pytest.param("skills/email-triage/_rtx/_watermark_floor.py", "default_state_dir", id="triage-floor"),
        pytest.param("skills/email-triage/_rtx/_write_metrics.py", "default_state_dir", id="triage-metrics"),
        pytest.param("skills/email-triage/_rtx/_envelope_gate.py", "default_state_dir", id="triage-envelope"),
        pytest.param("skills/email-triage/_rtx/_finalize_run.py", "default_state_dir", id="triage-finalize"),
        pytest.param("skills/list-manager/_rtx/_yaml_store.py", "_cloud_lock_dir", id="list-locks"),
    ],
)
def test_famulus_state_consumers_use_explicit_selected_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative: str, function: str
) -> None:
    env, checkout, _ = _development_env(tmp_path)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.syspath_prepend(str((ROOT / relative).parent))
    if Path(relative).name == "_finalize_run.py":
        module = _load_package_source("consumer_email_triage", relative)
    else:
        module = _load_source("consumer_" + Path(relative).stem, relative)
    selected = getattr(module, function)()
    assert selected.is_relative_to(checkout / ".famulus")


def test_wakeup_state_and_provider_discovery_use_selected_homes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env, checkout, _ = _development_env(tmp_path)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("LLM_WAKEUP_HOME", raising=False)
    monkeypatch.delenv("LLM_WAKEUP_CODEX_DIR", raising=False)
    monkeypatch.delenv("LLM_WAKEUP_CLAUDE_DIR", raising=False)

    assert store.data_dir().is_relative_to(checkout / ".famulus")
    assert policies._read_policies() == {}
    assert CodexAdapter().transcript_root() == Path(env["CODEX_HOME"]) / "sessions"
    assert ClaudeAdapter().transcript_root() == Path(env["CLAUDE_CONFIG_DIR"]) / "projects"


def test_wakeup_provider_process_overrides_remain_ephemeral(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env, checkout, _ = _development_env(tmp_path)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    codex_transcripts = tmp_path / "override-codex"
    claude_transcripts = tmp_path / "override-claude"
    monkeypatch.setenv("LLM_WAKEUP_CODEX_DIR", str(codex_transcripts))
    monkeypatch.setenv("LLM_WAKEUP_CLAUDE_DIR", str(claude_transcripts))
    monkeypatch.setenv("LLM_WAKEUP_CODEX_BIN", "/host/bin/codex")
    monkeypatch.setenv("LLM_WAKEUP_CLAUDE_BIN", "/host/bin/claude")
    assert CodexAdapter().transcript_root() == codex_transcripts
    assert ClaudeAdapter().transcript_root() == claude_transcripts
    assert CodexAdapter().executable_override() == "/host/bin/codex"
    assert ClaudeAdapter().executable_override() == "/host/bin/claude"
    assert all(path.is_relative_to(checkout / ".famulus") for path in CodexAdapter().executable_candidates())
    assert all(path.is_relative_to(checkout / ".famulus") for path in ClaudeAdapter().executable_candidates())


def test_handoff_and_skill_drift_use_selected_assistant_homes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env, checkout, _ = _development_env(tmp_path)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    codex_parser = _load_source("consumer_handoff_codex", "skills/find-handoff-candidates/_rtx/_codex_parser.py")
    claude_parser = _load_source("consumer_handoff_claude", "skills/find-handoff-candidates/_rtx/_claude_parser.py")
    assert Path(codex_parser.CodexParser().home_dir()) == Path(env["CODEX_HOME"])
    assert Path(claude_parser.ClaudeParser().home_dir()) == Path(env["CLAUDE_CONFIG_DIR"])

    monkeypatch.syspath_prepend(str(ROOT / "skills/node-drift/_rtx"))
    for home in (Path(env["CODEX_HOME"]), Path(env["CLAUDE_CONFIG_DIR"])):
        skill = home / "skills" / "selected"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("selected\n", encoding="utf-8")
    codex_source = importlib.import_module("_skill_sources._codex_skill_source")
    claude_source = importlib.import_module("_skill_sources._claude_skill_source")
    codex_sources = codex_source.sources()
    claude_sources = claude_source.sources()
    assert codex_sources and all(source.skills_root.is_relative_to(checkout / ".famulus") for source in codex_sources)
    assert claude_sources and all(source.skills_root.is_relative_to(checkout / ".famulus") for source in claude_sources)

    standard_env, standard_home = _standard_env(tmp_path / "standard-discovery")
    for key, value in standard_env.items():
        monkeypatch.setenv(key, value)
    for home in (standard_home / ".codex", standard_home / ".claude"):
        skill = home / "skills" / "standard"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("standard\n", encoding="utf-8")
    assert Path(codex_parser.CodexParser().home_dir()) == standard_home / ".codex"
    assert Path(claude_parser.ClaudeParser().home_dir()) == standard_home / ".claude"
    standard_codex = codex_source.sources()
    standard_claude = claude_source.sources()
    assert standard_codex and standard_codex[0].skills_root == (standard_home / ".codex/skills").resolve()
    assert standard_claude and standard_claude[0].skills_root == (standard_home / ".claude/skills").resolve()


def test_generic_child_isolation_preserves_transport_and_host_executables(tmp_path: Path) -> None:
    env, checkout, _ = _development_env(tmp_path)
    host = tmp_path / "host"
    for name in (".gitconfig", ".ssh", ".host-dotfile"):
        (host / name).mkdir() if name == ".ssh" else (host / name).write_text("canary")
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json,os,pathlib,shutil; print(json.dumps({'home':str(pathlib.Path.home()),'git':(pathlib.Path.home()/'.gitconfig').exists(),'ssh':(pathlib.Path.home()/'.ssh').exists(),'path':os.environ.get('PATH'),'display':os.environ.get('DISPLAY'),'bus':os.environ.get('DBUS_SESSION_BUS_ADDRESS'),'proxy':os.environ.get('HTTPS_PROXY'),'cert':os.environ.get('SSL_CERT_FILE'),'git_exe':shutil.which('git')}))",
        ],
        env=env,
        cwd=checkout,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        check=True,
    )
    observed = json.loads(probe.stdout)
    assert Path(observed["home"]).is_relative_to(checkout / ".famulus")
    assert observed["git"] is False and observed["ssh"] is False
    assert observed["path"] == env["PATH"]
    assert observed["display"] == ":17"
    assert observed["bus"] == "unix:path=/run/user/17/bus"
    assert observed["proxy"] == "http://proxy.invalid:8080"
    assert observed["cert"] == "/etc/ssl/certs/ca-certificates.crt"
    assert observed["git_exe"]


def test_desktop_notification_log_uses_selected_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env, checkout, _ = _development_env(tmp_path)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("ASSISTANT_NOTIFY_LOG", raising=False)
    notify = _load_source("consumer_notify", "skills/recurring-tasks/_rtx/_assistant_desktop_notify.py")
    assert notify._default_log_path().is_relative_to(checkout / ".famulus")


def test_managed_default_jobs_do_not_schedule_legacy_notification_helper() -> None:
    jobs = yaml.safe_load(
        (ROOT / "src/officina/recurring/default_jobs.yaml").read_text(encoding="utf-8")
    )["jobs"]
    assert jobs
    assert all("assistant_desktop_notify" not in job["command"] for job in jobs)


class _SecretBackend:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def store(self, namespace: str, key: str, value: str) -> None:
        self.values[(namespace, key)] = value

    def lookup(self, namespace: str, key: str) -> str | None:
        return self.values.get((namespace, key))

    def clear(self, namespace: str, key: str) -> bool:
        return self.values.pop((namespace, key), None) is not None


@pytest.mark.parametrize("mode", ["standard", "development"])
def test_service_loaders_and_writers_are_context_isolated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    if mode == "development":
        env, checkout, _ = _development_env(tmp_path)
        selected_home = Path(env["HOME"])
        foreign_home = tmp_path / "host"
        selected_boundary = checkout / ".famulus"
    else:
        env, selected_home = _standard_env(tmp_path)
        foreign_home = tmp_path / "foreign-home"
        foreign_home.mkdir()
        selected_boundary = selected_home
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    foreign_canaries = [
        foreign_home / ".config/cloud-files/config.json",
        foreign_home / ".config/online-calendar/config.json",
        foreign_home / ".config/email-client/accounts.json",
        foreign_home / ".local/state/famulus/email-triage/status.json",
        foreign_home / ".local/state/famulus/list-manager/locks/review.lock",
    ]
    foreign_environment = {"HOME": str(foreign_home)}
    if sys.platform == "win32":
        foreign_environment.update(
            {
                "USERPROFILE": str(foreign_home),
                "LOCALAPPDATA": str(foreign_home / "AppData" / "Local"),
                "APPDATA": str(foreign_home / "AppData" / "Roaming"),
            }
        )
    foreign_google_root = resolve_famulus_paths(
        platform=sys.platform,
        home=foreign_home,
        environ=foreign_environment,
    ).config_root / "connect-google"
    foreign_canaries.extend(
        [
            foreign_google_root / "client.json",
            foreign_google_root / "credentials.json",
            foreign_google_root / "credentials/host-canary.json",
        ]
    )
    for path in foreign_canaries:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("host-canary\n", encoding="utf-8")

    cloud_config = selected_home / ".config/cloud-files/config.json"
    cloud_config.parent.mkdir(parents=True)
    cloud_config.write_text(
        json.dumps({"remote_llm_root": "selected/", "timeout_seconds": 17}),
        encoding="utf-8",
    )
    calendar_config = selected_home / ".config/online-calendar/config.json"
    calendar_config.parent.mkdir(parents=True)
    calendar_config.write_text(json.dumps({"credential_id": "google:selected"}), encoding="utf-8")

    suffix = f"_{mode}"
    cloud = _load_source("consumer_cloud" + suffix, "skills/cloud-files/_rtx/_drive_gateway.py")
    cloud_bootstrap = _load_source(
        "consumer_cloud_bootstrap" + suffix,
        "skills/cloud-files/_rtx/_oauth_bootstrap.py",
    )
    cloud_binding = _load_source(
        "consumer_cloud_binding" + suffix,
        "skills/cloud-files/_rtx/_ensure_oauth.py",
    )
    calendar = _load_source("consumer_calendar" + suffix, "skills/online-calendar/_rtx/_gcal_client.py")
    calendar_bootstrap = _load_source(
        "consumer_calendar_bootstrap" + suffix,
        "skills/online-calendar/_rtx/_oauth_bootstrap.py",
    )
    calendar_binding = _load_source(
        "consumer_calendar_binding" + suffix,
        "skills/online-calendar/_rtx/_ensure_oauth.py",
    )
    monkeypatch.syspath_prepend(str(ROOT / "skills/email-client/_rtx"))
    email = _load_source("consumer_email" + suffix, "skills/email-client/_rtx/_email_accounts.py")
    monkeypatch.syspath_prepend(str(ROOT / "skills/list-manager/_rtx"))
    lists = _load_source("consumer_lists" + suffix, "skills/list-manager/_rtx/_yaml_store.py")
    triage = _load_source("consumer_triage" + suffix, "skills/email-triage/_rtx/_failure_sentinel.py")

    assert cloud.load_config().remote_llm_root == "selected/"
    assert calendar._load_service_config(Path.home()) == {"credential_id": "google:selected"}
    assert cloud_bootstrap.CLIENT_PATH.is_relative_to(selected_boundary)
    assert cloud_bootstrap.CREDS_PATH.is_relative_to(selected_boundary)
    assert Path(calendar_bootstrap.CLIENT_PATH).is_relative_to(selected_boundary)
    assert Path(calendar_bootstrap.CREDS_PATH).is_relative_to(selected_boundary)
    email.save({"selected": {"email": "selected@example.test"}})
    assert email.load()["selected"]["email"] == "selected@example.test"
    triage.main(["selected failure"])
    assert json.loads(triage.STATUS_FILE.read_text())["message"] == "selected failure"
    with lists.file_lock(lists.cloud_lock_path("review")):
        pass

    from officina.credentials import google as google_credentials

    backend = _SecretBackend()
    client_source = tmp_path / f"client-{mode}.json"
    client_source.write_text(
        json.dumps(
            {
                "installed": {
                    "client_id": "selected-client",
                    "project_id": "selected-project",
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "client_secret": "selected-secret",
                    "redirect_uris": ["http://localhost"],
                }
            }
        ),
        encoding="utf-8",
    )
    connect = _load_source("consumer_connect" + suffix, "skills/connect-google/_rtx/_client_config.py")
    connect.install_client(client_source, selected_home, replace=False, secret_backend=backend)
    descriptor = google_credentials.create_credential_file(
        subject="selected-subject",
        account="selected@example.test",
        client_id="selected-client",
        token_uri="https://oauth2.googleapis.com/token",
        granted_services=(),
        granted_scopes=frozenset(),
        refresh_token="selected-refresh",
        home=selected_home,
        platform=sys.platform,
        unique_id="01234567",
        secret_backend=backend,
    )
    assert google_credentials.load_credential_file(descriptor.path).subject == "selected-subject"
    granted_scopes = frozenset().union(*google_credentials.SERVICE_SCOPES.values())
    stored = google_credentials.store_google_credential(
        subject="selected-subject",
        account="selected@example.test",
        client_id="selected-client",
        token_uri="https://oauth2.googleapis.com/token",
        granted_scopes=granted_scopes,
        refresh_token="selected-refresh",
        home=selected_home,
        platform=sys.platform,
        secret_backend=backend,
        refresh_ref_factory=lambda: "google-refresh:" + "a" * 32,
    )
    assert google_credentials.load_credential(
        stored.credential_id, home=selected_home, platform=sys.platform
    ).subject == "selected-subject"
    cloud_binding.use_google_credential(
        credential_id=stored.credential_id, home=selected_home, platform=sys.platform
    )
    calendar_binding.use_google_credential(
        credential_id=stored.credential_id, home=selected_home, platform=sys.platform
    )
    email.accounts_use_google_credential(
        nickname="selected",
        credential_id=stored.credential_id,
        home=selected_home,
        platform=sys.platform,
    )
    assert cloud.load_config().credential_id == stored.credential_id
    assert calendar._load_service_config(selected_home)["credential_id"] == stored.credential_id
    assert email.load()["selected"]["credential_id"] == stored.credential_id

    selected_paths = [
        cloud.default_config_path(), calendar._config_path(Path.home()),
        email.ACCOUNTS_FILE, triage.STATUS_FILE, lists.cloud_lock_path("review"),
        connect.canonical_client_path(selected_home), descriptor.path,
        google_credentials._credentials_registry_path(home=selected_home, platform=sys.platform),
    ]
    assert all(path.is_relative_to(selected_boundary) for path in selected_paths)
    assert all(path.read_text() == "host-canary\n" for path in foreign_canaries)


def test_standard_consumers_resolve_normal_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env, home = _standard_env(tmp_path)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("LLM_WAKEUP_CODEX_DIR", raising=False)
    monkeypatch.delenv("LLM_WAKEUP_CLAUDE_DIR", raising=False)
    cloud = _load_source("standard_cloud", "skills/cloud-files/_rtx/_drive_gateway.py")
    calendar = _load_source("standard_calendar", "skills/online-calendar/_rtx/_gcal_client.py")
    notify = _load_source("standard_notify", "skills/recurring-tasks/_rtx/_assistant_desktop_notify.py")
    assert cloud.default_config_path().is_relative_to(home)
    assert calendar._config_path(Path.home()).is_relative_to(home)
    assert notify._default_log_path().is_relative_to(home)
    assert CodexAdapter().transcript_root() == home / ".codex" / "sessions"
    assert ClaudeAdapter().transcript_root() == home / ".claude" / "projects"


def test_recurring_email_triage_environment_is_context_selected_and_sanitized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env, checkout, context = _development_env(tmp_path)
    hostile = {
        **env,
        "GOOGLE_ACCESS_TOKEN": "ambient-secret",
        "AWS_SECRET_ACCESS_KEY": "ambient-secret",
        "EMAIL_CLIENT_PASSWORD": "ambient-secret",
    }
    backend = Path(sys.executable).resolve()
    scheduled = _bounded_environment(
        context,
        {"claude": backend, "codex": backend},
        None,
        sys.platform,
        hostile,
        "release-test",
    )
    schedule = ManagedSchedule(
        descriptor_path=context.paths.recurring_config_root / "schedule-descriptor.json",
        runtime_root=context.paths.runtime_root,
        runtime_resolver=context.paths.runtime_root / "bootstrap/resolvers/v1/launch.py",
        bootstrap_python=None,
        installation_id=context.installation_id,
        jobs_file=context.paths.recurring_config_root / "jobs.yaml",
        log_root=context.paths.recurring_state_root / "logs",
        config_root=context.paths.recurring_config_root,
        state_root=context.paths.recurring_state_root,
        native_registration_root=context.paths.recurring_state_root / "native",
        default_backend="codex",
        backend_executables={"claude": backend, "codex": backend},
        environment=scheduled,
        launcher_bin=context.paths.user_bin,
    )
    schedule.descriptor_path.parent.mkdir(parents=True, mode=0o700)
    schedule.descriptor_path.write_text(
        json.dumps(recurring_runtime._payload(schedule), indent=2) + "\n",
        encoding="utf-8",
    )
    schedule.descriptor_path.chmod(0o600)
    monkeypatch.setattr(recurring_runtime, "_expected_schedule", lambda **kwargs: schedule)
    loaded = recurring_runtime.load_managed_schedule(
        runtime_root=schedule.runtime_root,
        descriptor_path=schedule.descriptor_path,
        environ=hostile,
    )
    serialized = json.loads(schedule.descriptor_path.read_text())["environment"]
    assert serialized == loaded.environment
    assert not {"GOOGLE_ACCESS_TOKEN", "AWS_SECRET_ACCESS_KEY", "EMAIL_CLIENT_PASSWORD"}.intersection(serialized)

    host_status = tmp_path / "host" / ".local/state/famulus/email-triage/status.json"
    host_status.parent.mkdir(parents=True, exist_ok=True)
    host_status.write_text("host-canary\n", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import runpy,sys;"
                f"sys.path.insert(0,{str(ROOT / 'src')!r});"
                "sys.argv=['mark_failure.py','descriptor-selected'];"
                f"runpy.run_path({str(ROOT / 'skills/email-triage/_rtx/_failure_sentinel.py')!r},run_name='__main__')"
            ),
        ],
        env=loaded.environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        check=True,
    )
    assert result.returncode == 0
    status = context.paths.email_triage_state_root / "status.json"
    assert json.loads(status.read_text())["message"] == "descriptor-selected"
    assert status.is_relative_to(checkout / ".famulus")
    assert host_status.read_text() == "host-canary\n"


def test_installer_manifest_has_no_credential_ownership(tmp_path: Path) -> None:
    state_record = _load_source("consumer_manifest", "skills/install-assistant-tools/_rtx/_state_record.py")
    manifest = state_record.Manifest(tmp_path / "install-manifest.json")
    manifest.bind_context(mode="standard", installation_id="standard")
    payload = json.loads(manifest.path.read_text(encoding="utf-8"))
    assert payload["entries"] == []
    assert "credential" not in json.dumps(payload).casefold()


@pytest.mark.parametrize("mode", ["standard", "development"])
def test_real_installer_apply_manifest_excludes_google_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    if mode == "development":
        env, _, context = _development_env(tmp_path)
        selected_home = Path(env["HOME"])
    else:
        env, selected_home = _standard_env(tmp_path)
        context = resolve_installation_context(
            mode="standard",
            source_root=ROOT,
            development_root=None,
            platform=sys.platform,
            home=selected_home,
            environ=env,
        )
    installer = _load_package_source(
        f"consumer_installer_{mode}",
        "skills/install-assistant-tools/_rtx/_phase_entry.py",
    )
    monkeypatch.setattr(installer, "_build_managed_runtime_candidate", lambda **kwargs: 0)
    monkeypatch.setattr(installer, "_record_managed_runtime_state", lambda **kwargs: None)
    monkeypatch.setattr(installer.dev_link, "run", lambda **kwargs: None)
    monkeypatch.setattr(
        installer,
        "diagnose_installation",
        lambda **kwargs: installer.DiagnosticReport.healthy_for(context),
    )

    status = installer.apply(
        context=context,
        choices=installer.ApplyChoices(
            home=selected_home,
            shell_rc=selected_home / ".bashrc",
        ),
        environ=env,
    )

    assert status == 0
    manifest_path = context.paths.install_state_root / "install-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    forbidden_roots = (
        context.paths.config_root / "connect-google",
        selected_home / ".config/cloud-files",
        selected_home / ".config/online-calendar",
        selected_home / ".config/email-client",
    )
    for entry in manifest["entries"]:
        artifact = Path(entry["path"])
        assert not any(
            artifact == root or artifact.is_relative_to(root) for root in forbidden_roots
        )
    assert "credential" not in json.dumps(manifest).casefold()
