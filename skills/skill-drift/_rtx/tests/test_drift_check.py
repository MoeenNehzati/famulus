from __future__ import annotations

import json
import shutil
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "_check_drift_state.py"
SRC_ROOT = MODULE_PATH.parents[3] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
from officina.certification.records import certificate_public_key_root
from officina.blueprints.graph import BlueprintNode, InterfaceExport
from officina.certification.view import (
    CertificateCurrentnessReport,
    CertificateNodeCurrentness,
)
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


def test_status_payload_and_text_expose_structured_drift_and_worklist(
    tmp_path: Path,
) -> None:
    input_delta = checker.CertificateInputDelta(
        change="modified",
        path="skills/demo/worker.py",
        certified={
            "path": "skills/demo/worker.py",
            "digest": "sha256:" + "a" * 64,
            "git_provenance": "tracked",
        },
        current={
            "path": "skills/demo/worker.py",
            "digest": "sha256:" + "b" * 64,
            "git_provenance": "tracked",
        },
    )
    dependency_delta = checker.CertificateDependencyDelta(
        change="modified",
        relation="certified-under",
        target="skill-certifier.source.audit-interface",
        interface="skill-certifier.source.audit-interface.interface.audit",
        certified={"interface_hash": "sha256:" + "c" * 64},
        current={"interface_hash": "sha256:" + "d" * 64},
    )
    contract_dependency_delta = checker.CertificateDependencyDelta(
        change="modified",
        relation="references-cross-owner-contract",
        target="contract.source.owner",
        interface=None,
        certified={"node_hash": "sha256:" + "e" * 64},
        current={"node_hash": "sha256:" + "f" * 64},
    )
    facet = checker.CertificateFacetDrift(
        facet_id="demo.source.gateway.interface.run",
        facet_type="interface",
        local_hash_changed=True,
        declaration_changed=False,
        blueprint_path=None,
        input_files=(input_delta,),
        dependencies=(dependency_delta, contract_dependency_delta),
    )
    report = checker.ModuleDriftReport(
        skill="demo",
        source="path",
        package_root=tmp_path,
        skills_root=tmp_path / "skills",
        nodes=(
            checker.NodeDriftStatus(
                node_id="demo.source.gateway",
                current=False,
                concerns=("interface-hash-mismatch:demo.source.gateway.interface.run",),
                certificate_path=tmp_path / "demo.jsonl",
                facet_drift=(facet,),
            ),
        ),
        stale_worklist=("provider.source.gateway", "demo.source.gateway"),
    )

    payload = checker.build_payload((report,))
    text = checker.render_text((report,))

    node = payload["skills"][0]["nodes"][0]
    assert node["node_id"] == "demo.source.gateway"
    assert node["facet_drift"][0]["input_files"][0] == {
        "change": "modified",
        "path": "skills/demo/worker.py",
        "certified": input_delta.certified,
        "current": input_delta.current,
    }
    assert node["facet_drift"][0]["dependencies"][0][
        "interface"
    ] == "skill-certifier.source.audit-interface.interface.audit"
    assert node["facet_drift"][0]["dependencies"][1]["target"] == (
        "contract.source.owner"
    )
    assert payload["stale_worklist"] == [
        "provider.source.gateway",
        "demo.source.gateway",
    ]
    assert payload["skills"][0]["stale_worklist"] == payload["stale_worklist"]
    assert text.index("provider.source.gateway") < text.index("demo.source.gateway")
    assert "modified input skills/demo/worker.py" in text
    assert (
        "modified interface dependency "
        "skill-certifier.source.audit-interface.interface.audit"
    ) in text
    assert (
        "modified dependency references-cross-owner-contract "
        "contract.source.owner"
    ) in text


def test_render_text_dedupes_shared_drift_in_dependency_first_order(
    tmp_path: Path,
) -> None:
    provider_id = "provider.source.gateway"
    consumer_id = "consumer.source.gateway"
    provider = checker.NodeDriftStatus(
        node_id=provider_id,
        current=False,
        concerns=("node-hash-mismatch",),
        certificate_path=tmp_path / "provider.jsonl",
        facet_drift=(
            checker.CertificateFacetDrift(
                facet_id="provider.interface.run",
                facet_type="interface",
                local_hash_changed=True,
                declaration_changed=True,
                blueprint_path="skills/provider/blueprint.yaml",
            ),
        ),
    )
    consumer = checker.NodeDriftStatus(
        node_id=consumer_id,
        current=False,
        concerns=("dependency-mismatch",),
        certificate_path=tmp_path / "consumer.jsonl",
        facet_drift=(
            checker.CertificateFacetDrift(
                facet_id=consumer_id,
                facet_type="remainder",
                local_hash_changed=True,
                declaration_changed=True,
                blueprint_path="skills/consumer/blueprint.yaml",
            ),
        ),
    )
    first = checker.ModuleDriftReport(
        skill="consumer-one",
        source="path",
        package_root=tmp_path,
        skills_root=tmp_path / "skills",
        nodes=(consumer,),
        stale_worklist=(provider_id, consumer_id),
        dependency_nodes=(provider,),
    )
    second = checker.ModuleDriftReport(
        skill="consumer-two",
        source="path",
        package_root=tmp_path,
        skills_root=tmp_path / "skills",
        nodes=(consumer,),
        stale_worklist=(provider_id, consumer_id),
        dependency_nodes=(provider,),
    )

    text = checker.render_text((first, second))

    provider_heading = (
        "### provider.source.gateway / interface provider.interface.run"
    )
    consumer_heading = (
        "### consumer.source.gateway / remainder consumer.source.gateway"
    )
    assert text.count(provider_heading) == 1
    assert text.count(consumer_heading) == 1
    assert text.index(provider_heading) < text.index(consumer_heading)


def test_reports_preserve_facet_drift_and_dependency_first_worklist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph, states, _commit, _public_key_root, _backend, _key = (
        create_certified_fixture(tmp_path)
    )
    module_id = "demo-skill"
    source_id = graph.module_sources[module_id][0]
    external_id = graph.module_sources["skill-certifier"][0]
    states[module_id] = replace(
        states[module_id],
        dependency_hashes=(
            {
                "relation": "uses-source",
                "target": external_id,
                "version": graph.nodes[external_id].version,
                "node_hash": states[external_id].node_hash,
            },
        ),
    )
    facet = checker.CertificateFacetDrift(
        facet_id=external_id,
        facet_type="remainder",
        local_hash_changed=True,
        declaration_changed=True,
        blueprint_path="skills/demo-skill/blueprints/gateway.yaml",
    )
    currentness = checker.CertificateCurrentnessReport(
        nodes={
            node_id: checker.CertificateNodeCurrentness(
                node_id=node_id,
                current=node_id not in {module_id, external_id},
                concerns=("node-hash-mismatch",)
                if node_id in {module_id, external_id}
                else (),
                certificate=None,
                facet_drift=(facet,) if node_id == external_id else (),
            )
            for node_id in graph.nodes
        }
    )
    derived = checker._V4DerivedState(
        graph=graph,
        states=states,
        basis_hash="sha256:" + "e" * 64,
        currentness=currentness,
    )
    source = checker.SkillSource(
        source="path",
        package_root=tmp_path,
        skills_root=tmp_path / "skills",
    )
    monkeypatch.setattr(
        checker,
        "_derive_for_source",
        lambda *_args, **_kwargs: derived,
    )

    reports = checker.reports_for_scopes(
        (checker.RequestedScope(source, (module_id,)),),
        expected_schema_version=4,
    )

    assert reports[0].stale_worklist == (external_id, module_id)
    assert {node.node_id for node in reports[0].nodes} == {
        module_id,
        source_id,
    }
    assert [node.node_id for node in reports[0].dependency_nodes] == [external_id]
    assert reports[0].dependency_nodes[0].facet_drift == (facet,)


def test_reports_share_one_canonical_repository_worklist_across_modules(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph, states, _commit, _public_key_root, _backend, _key = (
        create_certified_fixture(tmp_path)
    )
    module_ids = ("demo-skill", "skill-certifier")
    provider_id = graph.module_sources["skill-certifier"][0]
    states[module_ids[0]] = replace(
        states[module_ids[0]],
        dependency_hashes=(
            *states[module_ids[0]].dependency_hashes,
            {
                "relation": "uses-source",
                "target": provider_id,
                "version": graph.nodes[provider_id].version,
                "node_hash": states[provider_id].node_hash,
            },
        ),
    )
    stale_ids = {*module_ids, provider_id}
    currentness = checker.CertificateCurrentnessReport(
        nodes={
            node_id: checker.CertificateNodeCurrentness(
                node_id=node_id,
                current=node_id not in stale_ids,
                concerns=("node-hash-mismatch",) if node_id in stale_ids else (),
                certificate=None,
            )
            for node_id in graph.nodes
        }
    )
    derived = checker._V4DerivedState(
        graph=graph,
        states=states,
        basis_hash="sha256:" + "e" * 64,
        currentness=currentness,
    )
    source = checker.SkillSource(
        source="path",
        package_root=tmp_path,
        skills_root=tmp_path / "skills",
    )
    monkeypatch.setattr(
        checker,
        "_derive_for_source",
        lambda *_args, **_kwargs: derived,
    )

    reports = checker.reports_for_scopes(
        (
            checker.RequestedScope(
                source,
                tuple(reversed(module_ids)),
            ),
        ),
        expected_schema_version=4,
    )
    expected = checker.certificate_stale_worklist(
        graph,
        states,
        currentness,
        tuple(
            node_id
            for module_id in module_ids
            for node_id in (module_id, *graph.module_sources[module_id])
        ),
    )

    assert expected == (provider_id, *sorted(module_ids))
    assert {report.repository_stale_worklist for report in reports} == {expected}
    assert checker.build_payload(reports)["stale_worklist"] == list(expected)
    assert checker.render_text(reports).index(expected[0]) < (
        checker.render_text(reports).index(expected[1])
    )


def test_top_level_worklist_qualifies_identical_ids_from_distinct_repositories(
    tmp_path: Path,
) -> None:
    node_id = "demo.source.gateway"
    roots = (tmp_path / "one", tmp_path / "two")
    reports = tuple(
        checker.ModuleDriftReport(
            skill=f"demo-{index}",
            source="path",
            package_root=root,
            skills_root=root / "skills",
            nodes=(
                checker.NodeDriftStatus(
                    node_id=node_id,
                    current=False,
                    concerns=("node-hash-mismatch",),
                    certificate_path=root / "certificate.jsonl",
                    local_hash_changed=True,
                ),
            ),
            stale_worklist=(node_id,),
            repository_stale_worklist=(node_id,),
        )
        for index, root in enumerate(roots)
    )

    payload = checker.build_payload(reports)
    text = checker.render_text(reports)

    qualified = [f"{root.resolve().as_posix()}::{node_id}" for root in roots]
    assert payload["stale_worklist"] == qualified
    assert [
        entry["package_root"] for entry in payload["repository_stale_worklists"]
    ] == [root.resolve().as_posix() for root in roots]
    assert all(identifier in text for identifier in qualified)
    assert all(f"### {identifier} / node {node_id}" in text for identifier in qualified)


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
    expected_schema_root = (
        tmp_path.resolve() / "references" / "blueprint-schema"
        if schema_version == 6
        else None
    )
    assert observed == [
        (
            tmp_path.resolve(),
            {
                "expected_schema_version": schema_version,
                "schema_root": expected_schema_root,
                "allow_non_atomic": False,
            },
        )
    ]


def test_public_v6_drift_uses_live_schema_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[dict[str, object]] = []
    derived = SimpleNamespace(
        graph="graph",
        states={},
        certification_basis_hash="sha256:" + "b" * 64,
        currentness="currentness",
    )

    def capture(_root: Path, **kwargs: object):
        observed.append(kwargs)
        return derived

    monkeypatch.setattr(
        checker,
        "derive_repository_certification_state",
        capture,
    )
    source = checker.SkillSource(
        source="override",
        package_root=tmp_path,
        skills_root=tmp_path / "skills",
    )

    result = checker._derive_for_source(source, expected_schema_version=6)

    assert result.graph == "graph"
    assert observed[0]["schema_root"] == (
        tmp_path / "references" / "blueprint-schema"
    )


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
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_home))

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
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_home))

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
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_home))

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
    args = SimpleNamespace(skills_root=None, repo_root=None)

    with pytest.raises(checker.DriftCheckError) as captured:
        checker.requested_skill_sources(args)

    message = str(captured.value)
    assert "installed blueprint graph has no registered nodes" in message
    assert "v4 nodes" not in message


def test_explicit_repository_root_bypasses_installed_source_discovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        checker,
        "observed_skill_sources",
        lambda: (_ for _ in ()).throw(
            AssertionError("explicit repository root reached installed discovery")
        ),
    )
    args = SimpleNamespace(skills_root=None, repo_root=checker.REPO_ROOT)

    sources = checker.requested_skill_sources(args)

    assert sources == [
        checker.SkillSource(
            source="override",
            package_root=checker.REPO_ROOT,
            skills_root=checker.REPO_ROOT / "skills",
        )
    ]


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
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_home))
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
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_home))

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
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_home))

    exit_code = checker.main(["status", "--json"])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "installed_plugins.json" in captured.err
    assert "--skill-root, --skills-root, or --repo-root" in captured.err


def test_semantic_stale_vertices_uses_conservative_source_fallback(
    tmp_path: Path,
) -> None:
    source_id = "demo.source.gateway"
    interface_id = f"{source_id}.interface.run"
    graph = checker.RepositoryBlueprintGraph(
        nodes={
            "demo": BlueprintNode(
                "demo", "module", 1, tmp_path, tmp_path / "module.yaml", None, {}
            ),
            source_id: BlueprintNode(
                source_id,
                "behavioral_source",
                1,
                tmp_path,
                tmp_path / "source.yaml",
                None,
                {},
            ),
        },
        node_edges=(),
        exports={},
        export_edges=(),
        helper_edges=(),
        certification_edges=(),
        source_modules={source_id: "demo"},
        source_interfaces={
            interface_id: InterfaceExport(
                interface_id,
                1,
                "run",
                "demo",
                {},
                source_node_id=source_id,
                source_interface_id=interface_id,
            )
        },
        module_parents={"demo": None},
    )
    currentness = CertificateCurrentnessReport(
        nodes={
            source_id: CertificateNodeCurrentness(
                source_id,
                False,
                ("certificate-missing",),
                None,
            )
        }
    )

    assert checker.semantic_stale_vertices(
        graph, currentness, (source_id,)
    ) == ("demo", source_id, interface_id)


def test_status_dag_file_writes_dag_and_stale_vertices(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = checker.SkillSource(
        source="override",
        package_root=tmp_path,
        skills_root=tmp_path / "skills",
    )
    scope = checker.RequestedScope(source=source, skill_names=("demo",))
    derived = checker._V4DerivedState(
        graph=checker.RepositoryBlueprintGraph(
            nodes={},
            node_edges=(),
            exports={},
            export_edges=(),
            helper_edges=(),
            certification_edges=(),
        ),
        states={},
        basis_hash="sha256:test",
        currentness=CertificateCurrentnessReport(nodes={}),
    )
    monkeypatch.setattr(checker, "requested_scopes", lambda _args: (scope,))
    monkeypatch.setattr(checker, "reports_for_scopes", lambda _scopes: [])
    monkeypatch.setattr(checker, "_derive_for_source", lambda _source: derived)
    monkeypatch.setattr(
        checker,
        "build_dependency_dag",
        lambda _graph, _states, repository: {
            "schema_version": "officina.certification-dependency-dag/v1",
            "repository": str(repository),
            "nodes": [],
        },
    )
    monkeypatch.setattr(
        checker, "semantic_stale_vertices", lambda *_args: ("demo",)
    )
    dag_file = tmp_path / "dag.json"

    assert checker.main(
        [
            "status",
            "--json",
            "--repo-root",
            str(tmp_path),
            "--dag-file",
            str(dag_file),
        ]
    ) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["dag_file"] == dag_file.resolve().as_posix()
    assert payload["stale_vertices"] == ["demo"]
    assert json.loads(dag_file.read_text(encoding="utf-8"))["nodes"] == []
