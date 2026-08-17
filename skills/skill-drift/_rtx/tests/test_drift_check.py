from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "_check_drift_state.py"
SRC_ROOT = MODULE_PATH.parents[3] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
from officina.certification.records import certificate_public_key_root
from test_support.v4_certification_fixtures import create_certified_fixture
from .. import _check_drift_state as checker

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
        expected_schema_version=4,
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
        expected_schema_version=4,
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
        expected_schema_version=4,
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

    reports, failures = checker.hash_reports_for_scopes(
        (scope,),
        expected_schema_version=4,
    )

    assert failures == []
    assert len(reports) == 1
    hashes = reports[0].hashes
    assert hashes["certification_basis"].startswith("sha256:")
    assert set(hashes["nodes"]) == {
        node_id
        for node_id, node in graph.nodes.items()
        if node.module_root == tmp_path / "skills" / "demo-skill"
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
    writes: list[object] = []
    source = checker.SkillSource(
        source="path",
        package_root=tmp_path,
        skills_root=tmp_path / "skills",
    )
    scope = checker.RequestedScope(source, ("demo",))
    status_report = checker.ModuleDriftReport(
        skill="demo",
        source="path",
        package_root=tmp_path,
        skills_root=tmp_path / "skills",
        nodes=(
            checker.NodeDriftStatus(
                node_id="demo",
                current=True,
                concerns=(),
                certificate_path=tmp_path / "demo.jsonl",
            ),
        ),
    )
    hash_report = checker.SkillHashReport(
        skill="demo",
        source="path",
        package_root=tmp_path,
        skills_root=tmp_path / "skills",
        hashes={"nodes": {"demo": {"node_hash": "sha256:" + "a" * 64}}},
    )
    monkeypatch.setattr(checker, "requested_scopes", lambda _args: (scope,))
    monkeypatch.setattr(
        checker,
        "reports_for_scopes",
        lambda _scopes: [status_report],
    )
    monkeypatch.setattr(
        checker,
        "hash_reports_for_scopes",
        lambda _scopes: ([hash_report], []),
    )
    monkeypatch.setattr(
        checker,
        "write_markdown_report",
        lambda *_args, **_kwargs: writes.append(object()),
    )

    status_code = checker.main(["status", "demo", "--json"])
    status = json.loads(capsys.readouterr().out)
    hash_code = checker.main(["compute-hashes", "demo", "--json"])
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
        expected_schema_version=4,
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


@pytest.mark.parametrize("schema_version", (5, 6))
def test_drift_derivation_delegates_schema_selection_to_canonical_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    schema_version: int,
) -> None:
    observed = []
    derived = SimpleNamespace(
        graph="graph",
        states={},
        source_commit="a" * 40,
        certification_basis_hash="sha256:" + "b" * 64,
        certifier_identity={},
    )

    def capture(root, **kwargs):
        observed.append((root, kwargs))
        return derived

    monkeypatch.setattr(
        checker,
        "derive_repository_certification_state",
        capture,
    )

    result = checker._v4_repository_state(
        tmp_path,
        expected_schema_version=schema_version,
    )

    assert result[0] == "graph"
    assert observed == [
        (
            tmp_path.resolve(),
            {
                "expected_schema_version": schema_version,
                "schema_root": None,
                "allow_non_atomic": False,
            },
        )
    ]


def test_drift_node_selection_uses_exact_global_module_id() -> None:
    graph = SimpleNamespace(
        nodes={
            "demo": SimpleNamespace(),
            "demo.source.gateway": SimpleNamespace(),
            "demo-rtx": SimpleNamespace(),
            "demo-rtx.source.runtime": SimpleNamespace(),
        },
        module_sources={
            "demo": ("demo.source.gateway",),
            "demo-rtx": ("demo-rtx.source.runtime",),
        },
    )

    assert checker._module_node_ids(graph, "demo") == (
        "demo",
        "demo.source.gateway",
    )
    assert checker._module_node_ids(graph, "demo-rtx") == (
        "demo-rtx",
        "demo-rtx.source.runtime",
    )


def test_v5_explicit_child_id_is_reportable_without_a_physical_skill_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = checker.SkillSource(
        source="test",
        package_root=tmp_path,
        skills_root=tmp_path / "skills",
    )
    scope = checker.RequestedScope(source, ("demo-rtx",))
    module = SimpleNamespace(node_type="module")
    runtime = SimpleNamespace(node_type="behavioral_source")
    derived = checker._V4DerivedState(
        graph=SimpleNamespace(
            nodes={
                "demo-rtx": module,
                "demo-rtx.source.runtime": runtime,
            },
            module_sources={
                "demo-rtx": ("demo-rtx.source.runtime",),
            },
        ),
        states={
            "demo-rtx": SimpleNamespace(
                node_hash="sha256:" + "a" * 64,
                dependency_hashes=(),
            ),
            "demo-rtx.source.runtime": SimpleNamespace(
                node_hash="sha256:" + "b" * 64,
                dependency_hashes=(),
            ),
        },
        basis_hash="sha256:" + "c" * 64,
        currentness=SimpleNamespace(
            nodes={
                node_id: SimpleNamespace(current=True, concerns=())
                for node_id in (
                    "demo-rtx",
                    "demo-rtx.source.runtime",
                )
            },
        ),
    )
    observed_versions: list[int] = []

    def derive(_source, *, expected_schema_version):
        observed_versions.append(expected_schema_version)
        return derived

    monkeypatch.setattr(checker, "_derive_for_source", derive)
    monkeypatch.setattr(
        checker,
        "certificate_log_path",
        lambda node: tmp_path / f"{node.node_type}.jsonl",
    )

    status_reports = checker.reports_for_scopes(
        (scope,),
        expected_schema_version=5,
    )
    hash_reports, failures = checker.hash_reports_for_scopes(
        (scope,),
        expected_schema_version=5,
    )

    assert [report.skill for report in status_reports] == ["demo-rtx"]
    assert set(hash_reports[0].hashes["nodes"]) == {
        "demo-rtx",
        "demo-rtx.source.runtime",
    }
    assert failures == []
    assert observed_versions == [5, 5]


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
        checker.dedupe_skill_sources([first, second])


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


def test_empty_active_plugin_graph_uses_schema_neutral_diagnostic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = checker.SkillSource(
        source="claude",
        package_root=tmp_path,
        skills_root=tmp_path / "skills",
        plugin_id="demo@market",
        plugin_version="2",
    )
    monkeypatch.setattr(checker, "observed_skill_sources", lambda: [source])
    monkeypatch.setattr(
        checker,
        "load_repository_blueprint_graph",
        lambda *_args, **_kwargs: SimpleNamespace(nodes={}),
    )
    args = SimpleNamespace(skills_root=None, repo_root=checker.REPO_ROOT)

    with pytest.raises(checker.DriftCheckError) as captured:
        checker.requested_skill_sources(args)

    message = str(captured.value)
    assert "installed blueprint graph has no registered nodes" in message
    assert "v4 nodes" not in message


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


def test_active_v4_plugin_is_rejected_after_canonical_v5_cutover(
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
    assert exit_code == 2
    assert captured.out == ""
    assert "schema_version 6" in captured.err
    assert "stale-skill" not in captured.err


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
