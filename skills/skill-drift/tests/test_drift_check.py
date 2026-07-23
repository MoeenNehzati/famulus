from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "_rtx" / "_check_drift_state.py"
SRC_ROOT = MODULE_PATH.parents[3] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
TEST_ROOT = MODULE_PATH.parents[3] / "tests"
if str(TEST_ROOT) not in sys.path:
    sys.path.insert(0, str(TEST_ROOT))

from officina.common.certificate_records import certificate_public_key_root
from v4_certification_fixtures import create_certified_fixture

SPEC = importlib.util.spec_from_file_location("skill_check_drift_state", MODULE_PATH)
checker = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = checker
SPEC.loader.exec_module(checker)

from _skill_sources import dedupe_skill_sources


def test_drift_does_not_expose_legacy_audit_health_readers() -> None:
    for name in (
        "AUDIT_RECORD_NAME",
        "HealthCheck",
        "check_typed_skill",
        "compute_audit_hashes",
        "read_record",
        "secure_load_target_key",
        "typed_hash_report_for_skill",
        "check_pooled_review",
        "record_digest_matches",
    ):
        assert not hasattr(checker, name)


def _certified(repo: Path):
    graph, _states, _commit, public_key_root, _backend, _key = (
        create_certified_fixture(repo)
    )
    return graph, public_key_root


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _make_marker_skill(package_root: Path, name: str) -> Path:
    skill_root = package_root / "skills" / name
    skill_root.mkdir(parents=True, exist_ok=True)
    (skill_root / "SKILL.md").write_text(f"{name}\n", encoding="utf-8")
    return skill_root


def _make_unsupported_module(package_root: Path, name: str) -> Path:
    skill_root = _make_marker_skill(package_root, name)
    (skill_root / "blueprint.yaml").write_text(
        "schema_version: 3\n"
        "blueprint_type: skill\n"
        f"id: {name}\n",
        encoding="utf-8",
    )
    return skill_root


def test_public_key_only_drift_is_current_for_exact_nodes(tmp_path: Path) -> None:
    graph, public_key_root = _certified(tmp_path)
    target_ids = tuple(sorted(graph.nodes))

    report = checker._check_v4_repository(
        tmp_path,
        target_ids,
        public_key_root=public_key_root,
    )

    assert report.current
    assert set(report.nodes) == set(target_ids)
    assert all(status.current for status in report.nodes.values())


def test_missing_certificate_is_precisely_stale(tmp_path: Path) -> None:
    graph, public_key_root = _certified(tmp_path)
    target = "demo-skill"
    checker.certificate_log_path(graph.nodes[target]).unlink()

    report = checker._check_v4_repository(
        tmp_path,
        (target,),
        public_key_root=public_key_root,
    )

    assert not report.current
    assert report.nodes[target].concerns == ("missing-certificate-log",)


def test_suspect_certificate_log_is_precisely_stale(tmp_path: Path) -> None:
    graph, public_key_root = _certified(tmp_path)
    target = "demo-skill"
    with checker.certificate_log_path(graph.nodes[target]).open("ab") as stream:
        stream.write(b"{}\n")

    report = checker._check_v4_repository(
        tmp_path,
        (target,),
        public_key_root=public_key_root,
    )

    assert not report.current
    assert report.nodes[target].concerns == ("suspect-certificate-log",)


def test_hash_report_uses_same_graph_and_basis_without_reading_legacy_state(
    tmp_path: Path,
) -> None:
    graph, _public_key_root = _certified(tmp_path)
    source = checker.SkillSource(
        source="test",
        package_root=tmp_path,
        skills_root=tmp_path / "skills",
    )
    scope = checker.RequestedScope(source, ("demo-skill",))

    reports, failures = checker.hash_reports_for_scopes((scope,))

    assert failures == []
    assert len(reports) == 1
    hashes = reports[0].hashes
    assert hashes["certification_basis"].startswith("sha256:")
    assert set(hashes["nodes"]) == {
        node_id
        for node_id, node in graph.nodes.items()
        if node.skill_root == tmp_path / "skills" / "demo-skill"
    }
    assert all(
        node["node_hash"].startswith("sha256:")
        for node in hashes["nodes"].values()
    )


def test_exact_cli_status_and_hash_routes_are_read_only(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _graph, public_key_root = _certified(tmp_path)
    default_key_root = certificate_public_key_root(tmp_path)
    default_key_root.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(public_key_root, default_key_root)
    writes: list[object] = []
    monkeypatch.setattr(
        checker,
        "write_markdown_report",
        lambda *_args, **_kwargs: writes.append(object()),
    )
    skill_root = tmp_path / "skills" / "demo-skill"

    status_code = checker.main(
        ["status", "--skill-root", str(skill_root), "--json"]
    )
    status = json.loads(capsys.readouterr().out)
    hash_code = checker.main(
        ["compute-hashes", "--skill-root", str(skill_root), "--json"]
    )
    hashes = json.loads(capsys.readouterr().out)

    assert status_code == 0
    assert hash_code == 0
    assert status["skills"][0]["status"] == "certificate-current"
    assert hashes["skills"][0]["hashes"]["nodes"]
    assert writes == []


def test_v4_drift_propagates_explicit_non_atomic_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph, public_key_root = _certified(tmp_path)
    real_derive = checker.derive_repository_certification_state
    observed: list[bool] = []

    def derive_with_fallback(*args: object, **kwargs: object):
        observed.append(kwargs["allow_non_atomic"])
        return real_derive(*args, **kwargs)

    monkeypatch.setattr(
        checker,
        "derive_repository_certification_state",
        derive_with_fallback,
    )

    report = checker._check_v4_repository(
        tmp_path,
        ("demo-skill",),
        public_key_root=public_key_root,
        allow_non_atomic=True,
    )

    assert report.nodes["demo-skill"].current
    assert observed == [True]


def test_default_public_key_location_is_certifier_owned(tmp_path: Path) -> None:
    assert certificate_public_key_root(tmp_path) == (
        tmp_path
        / "skills"
        / "skill-certifier"
        / ".certificates"
        / "public-keys"
    )


def test_installed_sources_ignore_codex_plugin_cache_without_active_registry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    codex_home = tmp_path / "codex-home"
    claude_home = tmp_path / "claude-home"
    _make_marker_skill(codex_home, "direct-skill")
    _make_marker_skill(
        codex_home / "plugins" / "cache" / "market" / "stale" / "1.0",
        "stale-skill",
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("CLAUDE_HOME", str(claude_home))

    sources = checker.observed_skill_sources()

    assert [(source.source, source.skills_root) for source in sources] == [
        ("codex", (codex_home / "skills").resolve())
    ]


def test_installed_sources_use_only_registry_named_plugin_versions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    codex_home = tmp_path / "codex-home"
    claude_home = tmp_path / "claude-home"
    active = claude_home / "plugins" / "cache" / "market" / "demo" / "2.0"
    stale = claude_home / "plugins" / "cache" / "market" / "demo" / "1.0"
    _make_marker_skill(active, "active-skill")
    _make_marker_skill(stale, "stale-skill")
    _write_json(
        claude_home / "plugins" / "installed_plugins.json",
        {
            "version": 2,
            "plugins": {
                "demo@market": [
                    {
                        "version": "2.0",
                        "scope": "user",
                        "installPath": str(active),
                    }
                ]
            },
        },
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("CLAUDE_HOME", str(claude_home))

    sources = checker.observed_skill_sources()

    assert [
        (source.source, source.skills_root, source.plugin_id, source.plugin_version)
        for source in sources
    ] == [
        ("claude", (active / "skills").resolve(), "demo@market", "2.0")
    ]


def test_installed_source_deduplication_rejects_conflicting_plugin_identity(
    tmp_path: Path,
) -> None:
    root = tmp_path / "plugin" / "skills"
    root.mkdir(parents=True)
    first = checker.SkillSource(
        source="claude",
        package_root=root.parent,
        skills_root=root,
        plugin_id="first@market",
        plugin_version="1",
    )
    second = checker.SkillSource(
        source="claude",
        package_root=root.parent,
        skills_root=root,
        plugin_id="second@market",
        plugin_version="2",
    )

    with pytest.raises(checker.SkillSourceDiscoveryError, match="metadata conflict"):
        dedupe_skill_sources([first, second])


def test_active_plugin_with_malformed_version_metadata_fails_with_remediation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    codex_home = tmp_path / "codex-home"
    claude_home = tmp_path / "claude-home"
    active = claude_home / "plugins" / "cache" / "market" / "demo" / "unknown"
    _make_marker_skill(active, "active-skill")
    _write_json(
        claude_home / "plugins" / "installed_plugins.json",
        {
            "version": 2,
            "plugins": {
                "demo@market": [
                    {
                        "version": 7,
                        "scope": "user",
                        "installPath": str(active),
                    }
                ]
            },
        },
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("CLAUDE_HOME", str(claude_home))

    with pytest.raises(checker.SkillSourceDiscoveryError) as captured:
        checker.observed_skill_sources()

    message = str(captured.value)
    assert "demo@market" in message
    assert '"version": 7' in message
    assert (
        "repair installed_plugins.json or pass --skill-root, --skills-root, "
        "or --repo-root"
    ) in message


@pytest.mark.parametrize(
    "command",
    [
        ["status", "--all", "--json"],
        ["compute-hashes", "--json"],
    ],
)
def test_unsupported_active_plugin_never_reaches_certification_derivation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    command: list[str],
) -> None:
    codex_home = tmp_path / "codex-home"
    claude_home = tmp_path / "claude-home"
    active = claude_home / "plugins" / "cache" / "market" / "demo" / "7"
    _make_unsupported_module(active, "active-skill")
    _write_json(
        claude_home / "plugins" / "installed_plugins.json",
        {
            "version": 2,
            "plugins": {
                "demo@market": [
                    {
                        "version": "7",
                        "scope": "user",
                        "installPath": str(active),
                    }
                ]
            },
        },
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("CLAUDE_HOME", str(claude_home))
    monkeypatch.setattr(
        checker,
        "derive_repository_certification_state",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unsupported active plugin reached certification derivation")
        ),
    )

    exit_code = checker.main(command)

    captured = capsys.readouterr()
    assert exit_code == 2
    assert 'plugin "demo@market" version "7"' in captured.err
    assert str(active) in captured.err
    assert (
        "repair installed_plugins.json or pass --skill-root, --skills-root, "
        "or --repo-root"
    ) in captured.err


def test_active_v4_plugin_ignores_stale_cached_unsupported_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    codex_home = tmp_path / "codex-home"
    claude_home = tmp_path / "claude-home"
    active = claude_home / "plugins" / "cache" / "market" / "demo" / "2"
    stale = claude_home / "plugins" / "cache" / "market" / "demo" / "1"
    _graph, public_key_root = _certified(active)
    default_key_root = certificate_public_key_root(active)
    default_key_root.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(public_key_root, default_key_root)
    _make_unsupported_module(stale, "stale-skill")
    _write_json(
        claude_home / "plugins" / "installed_plugins.json",
        {
            "version": 2,
            "plugins": {
                "demo@market": [
                    {
                        "version": "2",
                        "scope": "user",
                        "installPath": str(active),
                    }
                ]
            },
        },
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("CLAUDE_HOME", str(claude_home))

    exit_code = checker.main(["compute-hashes", "--json"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 0
    observed = {report["skill"] for report in payload["skills"]}
    assert "demo-skill" in observed
    assert "stale-skill" not in observed
    assert "stale-skill" not in captured.out


def test_malformed_plugin_registry_fails_with_remediation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    codex_home = tmp_path / "codex-home"
    claude_home = tmp_path / "claude-home"
    _write_json(
        claude_home / "plugins" / "installed_plugins.json",
        {"version": 1, "plugins": {}},
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("CLAUDE_HOME", str(claude_home))

    exit_code = checker.main(["status", "--json"])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "installed_plugins.json" in captured.err
    assert "--skill-root, --skills-root, or --repo-root" in captured.err
