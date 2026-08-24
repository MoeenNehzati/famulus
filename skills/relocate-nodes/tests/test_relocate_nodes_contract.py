from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
import subprocess
import sys
from types import ModuleType

import pytest
import yaml


SKILL_ROOT = Path(__file__).resolve().parents[1]
RTX_ROOT = SKILL_ROOT / "_rtx"
REPO_ROOT = SKILL_ROOT.parents[1]
ADAPTER_PATH = RTX_ROOT / "_relocate_nodes.py"
RUNTIME_PACKAGE = "relocate_nodes_contract_runtime"


def _load_adapter():
    return _load_runtime_module(f"{RUNTIME_PACKAGE}._relocate_nodes", ADAPTER_PATH)


def _load_runtime_module(name: str, path: Path):
    package = ModuleType(RUNTIME_PACKAGE)
    package.__path__ = [str(RTX_ROOT)]
    sys.modules.setdefault(RUNTIME_PACKAGE, package)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load relocate-nodes runtime from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _synthetic_repository(root: Path) -> dict[str, str]:
    """Create an isolated registered relocation fixture and diverse callers."""

    _write(
        root / "officina.toml",
        'schema_version = 1\n\n[modules]\nroots = ["skills", "src/officina"]\n',
    )
    _write(
        root / "skills/relocate-source/blueprint.yaml",
        yaml.safe_dump(
            {
                "schema_version": 6,
                "id": "relocate-source",
                "node_type": "module",
                "children": {"_rtx": {}},
                "sources": {
                    "relocate-source.source.gateway": {
                        "blueprint": {
                            "base": "module-root",
                            "path": "blueprints/gateway.yaml",
                        }
                    }
                },
                "exports": {
                    "relocate-source.interface.default": {
                        "source_interface": "relocate-source.source.gateway.interface.default",
                        "access": {
                            "allow_all_modules": False,
                            "allowed_callers": ["relocate-consumer"],
                        },
                    }
                },
            },
            sort_keys=False,
        ),
    )
    _write(
        root / "skills/relocate-source/blueprints/gateway.yaml",
        yaml.safe_dump(
            {
                "schema_version": 6,
                "id": "relocate-source.source.gateway",
                "node_type": "behavioral_source",
                "interfaces": {
                    "relocate-source.source.gateway.interface.default": {
                        "version": 1,
                        "description": "Use the synthetic fixture.",
                    }
                },
            },
            sort_keys=False,
        ),
    )
    _write(
        root / "skills/relocate-source/_rtx/blueprint.yaml",
        yaml.safe_dump(
            {
                "schema_version": 6,
                "id": "relocate-source._rtx",
                "node_type": "module",
                "children": {},
            },
            sort_keys=False,
        ),
    )
    _write(root / "skills/relocate-source/_rtx/client.py", "VALUE = 1\n")
    _write(
        root / "skills/relocate-consumer/blueprint.yaml",
        yaml.safe_dump(
            {
                "schema_version": 6,
                "id": "relocate-consumer",
                "node_type": "module",
                "children": {},
                "dependencies": ["relocate-source"],
            },
            sort_keys=False,
        ),
    )
    _write(
        root / "caller.py",
        "from relocate_fixture.old import VALUE\n",
    )
    _write(
        root / "consumer.yaml",
        "schema_version: 6\nid: fixture.consumer\nnode_type: behavioral_source\n"
        "uses_interfaces:\n- interface: relocate-source.interface.default\n  version: 1\n",
    )
    _write(
        root / "scripts/use-relocation.sh",
        "dispatcher --caller-skill relocate-source relocate-source.interface.default\n",
    )
    nonstructural = {
        "literal.py": (
            'PATH = Path("skills") / "relocate-source"\n'
            'NAME = "relocate-source"\n'
        ),
        "notes.md": "Use relocate-source for fixtures.\n",
        "proof.tex": "\\texttt{relocate-source}\n",
        ".config/relocate-source/config.json": '{"namespace":"relocate-source"}\n',
    }
    for relative, text in nonstructural.items():
        _write(root / relative, text)
    _write(root / "src/officina/relocation_support.py", "VALUE = 1\n")
    return nonstructural


def _semantic_decision(
    occurrence: dict[str, object], *, disposition: str
) -> dict[str, object]:
    """Convert one report occurrence into a complete reviewed selector."""

    context = str(occurrence["context"])
    match = str(occurrence["match"])
    decision = {
        key: occurrence[key]
        for key in (
            "occurrence_id",
            "mapping_kind",
            "mapping_id",
            "path",
            "original_digest",
            "byte_start",
            "byte_end",
            "ordinal",
            "match",
        )
    }
    decision.update(
        {
            "count": 1,
            "disposition": disposition,
            "text": context,
            "reason": "Reviewed isolated synthetic namespace policy.",
        }
    )
    if disposition == "rewrite":
        decision["replacement"] = context.replace(
            match, str(occurrence["candidate"]), 1
        )
    return decision


def test_relocate_nodes_has_one_private_runtime_route() -> None:
    parent = yaml.safe_load((SKILL_ROOT / "blueprint.yaml").read_text(encoding="utf-8"))
    child = yaml.safe_load((RTX_ROOT / "blueprint.yaml").read_text(encoding="utf-8"))

    assert parent["children"] == {"_rtx": {}}
    assert parent["namespace_exports"]["_rtx"]["surface"]["only"] == {
        "relocate-nodes._rtx.interface.relocate": 2
    }
    assert set(child["exports"]) == {"relocate-nodes._rtx.interface.relocate"}
    assert child["gateway"] == {"language": "Python>=3.11", "path": "__init__.py"}


def test_adapter_requires_manifest() -> None:
    adapter = _load_adapter()

    with pytest.raises(SystemExit):
        adapter.Interface().build_parser().parse_args([])


def test_runtime_declares_authorized_graph_synchronizer_and_repository_dependencies() -> None:
    source = yaml.safe_load(
        (RTX_ROOT / "blueprints/rtx-relocate-nodes.yaml").read_text(encoding="utf-8")
    )

    assert source["uses_interfaces"] == [
        {"interface": "blueprints.interface.graph", "version": 1},
        {"interface": "configuration.interface.repository", "version": 1},
        {"interface": "skill-maker._rtx.interface.sync-blueprints", "version": 1},
    ]
    assert source["interfaces"][
        "relocate-nodes._rtx.source.rtx-relocate-nodes.interface.relocate"
    ]["version"] == 2


def test_adapter_declares_exact_synchronizer_dispatch() -> None:
    adapter = _load_adapter()
    call = adapter.Interface.dispatches["sync-blueprints"]

    assert call.caller_module_id == "relocate-nodes._rtx"
    assert call.target_module_id == "skill-maker._rtx"
    assert call.interface == "sync-blueprints"
    assert call.version == 1


def test_registered_route_is_the_only_live_relocation_entrypoint() -> None:
    documentation = (REPO_ROOT / "docs/officina/source-relocation.md").read_text(
        encoding="utf-8"
    )

    assert not (REPO_ROOT / "scripts/relocate_officina_sources.py").exists()
    assert "dispatcher --caller-skill relocate-nodes" in documentation
    assert "relocate-nodes._rtx.interface.relocate" in documentation
    assert "relocate_officina_sources.py" not in documentation


def test_authored_workflow_requires_one_reviewed_schema_v3_relocation() -> None:
    """The public skill recipe prevents segment moves and incomplete adjudication."""

    skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    normalized_skill = " ".join(skill.replace("`", "").split())
    examples = re.findall(r"```yaml\n(.*?)```", skill, flags=re.DOTALL)
    manifests = [yaml.safe_load(example) for example in examples]
    nested = next(
        (
            value
            for value in manifests
            if isinstance(value, dict)
            and value.get("relocations")
            and value["relocations"][0].get("from") == "skills/a/b/c"
        ),
        None,
    )

    assert nested is not None
    assert nested["schema_version"] == 3
    assert nested["relocations"] == [
        {"from": "skills/a/b/c", "to": "skills/a/d/e"}
    ]
    required_contract = (
        "Preflight mechanical closure before adjudication",
        "Review every occurrence ID across every reported file type",
        "persisted state, compatibility, or behavior",
        "nonempty reason",
        "Never use blind global substitution",
        "exact_rewrites cannot account for semantic occurrences",
        "Rerun preflight",
        "Apply the reviewed manifest",
        "target-side postflight",
    )
    for clause in required_contract:
        assert clause in normalized_skill

    gateway = yaml.safe_load(
        (SKILL_ROOT / "blueprints/gateway.yaml").read_text(encoding="utf-8")
    )
    contract = gateway["interfaces"][
        "relocate-nodes.source.gateway.interface.default"
    ]["contract"]
    assert contract["interaction"]["mode"] == "interactive"
    warnings = [next(iter(warning.values())) for warning in contract["caller_warnings"]]
    assert any(
        "per-file atomic" in warning
        and "repository-wide transaction" in warning
        for warning in warnings
    )


@pytest.mark.parametrize(
    "legacy",
    (
        REPO_ROOT / "src/officina/refactor",
        REPO_ROOT / "scripts/relocate_officina_sources.py",
        REPO_ROOT / "refactors/officina-source-relocation.yaml",
    ),
)
def test_legacy_relocation_surfaces_are_absent(legacy: Path) -> None:
    assert not legacy.exists()


def test_runtime_engine_validates_manifest_with_its_adjacent_schema(
    tmp_path: Path,
) -> None:
    engine = _load_runtime_module(
        f"{RUNTIME_PACKAGE}._relocation_engine",
        RTX_ROOT / "_relocation_engine.py",
    )
    manifest = tmp_path / "relocation.yaml"
    manifest.write_text("schema_version: 3\n", encoding="utf-8")

    loaded = engine.load_manifest(manifest)

    assert loaded.relocations == ()


def test_runtime_contract_declares_per_file_not_repository_atomicity() -> None:
    """Authored mutation safety must acknowledge possible partial publication."""

    blueprint = yaml.safe_load(
        (RTX_ROOT / "blueprints/rtx-relocate-nodes.yaml").read_text(encoding="utf-8")
    )
    safety = next(iter(blueprint["interfaces"].values()))["contract"]["execution"][
        "mutation_safety"
    ]

    assert "per_effect_only" in safety["atomicity"]
    assert "possible" in safety["partial_effects_on_failure"]


def test_adapter_rejects_report_path_inside_selected_repository(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    adapter = _load_adapter()
    root = tmp_path / "repository"
    root.mkdir()
    manifest = tmp_path / "relocation.yaml"
    manifest.write_text("schema_version: 3\n", encoding="utf-8")
    report = root / "report.json"
    args = adapter.Interface().build_parser().parse_args(
        ["--root", str(root), "--manifest", str(manifest), "--report", str(report)]
    )

    assert adapter.Interface().run(args) == 2
    assert not report.exists()
    assert "report path must be outside selected repository" in capsys.readouterr().err


def test_adapter_gates_apply_but_not_preflight_on_unaccounted_occurrences(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An unresolved semantic address permits reporting but prevents every write."""

    adapter = _load_adapter()
    root = tmp_path / "repository"
    root.mkdir()
    (root / "old.txt").write_text("retire old.txt\n", encoding="utf-8")
    manifest = tmp_path / "relocation.yaml"
    manifest.write_text(
        "schema_version: 3\nrelocations:\n- from: old.txt\n  to: new.txt\n",
        encoding="utf-8",
    )
    report = tmp_path / "report.json"
    interface = adapter.Interface()
    preflight = interface.build_parser().parse_args(
        ["--root", str(root), "--manifest", str(manifest), "--report", str(report)]
    )

    assert interface.run(preflight) == 0
    assert report.is_file()
    assert (root / "old.txt").is_file()
    report.unlink()
    capsys.readouterr()

    apply = interface.build_parser().parse_args(
        [
            "--root",
            str(root),
            "--manifest",
            str(manifest),
            "--report",
            str(report),
            "--apply",
        ]
    )
    assert interface.run(apply) == 2
    assert not report.exists()
    assert (root / "old.txt").is_file()
    assert not (root / "new.txt").exists()
    assert "unaccounted semantic occurrences" in capsys.readouterr().err


@pytest.mark.parametrize("preserve_config_namespace", (True, False))
def test_node_relocation_end_to_end_isolated_from_source_checkout(
    tmp_path: Path,
    preserve_config_namespace: bool,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """One manifest closes, adjudicates, applies, and postflights in a temp repo."""

    tracked = (
        ADAPTER_PATH,
        RTX_ROOT / "_relocation_engine.py",
        SKILL_ROOT / "SKILL.md",
    )
    source_bytes = {path: path.read_bytes() for path in tracked}
    # famulus-raw-git: category=validator-isolation; reason=the isolated E2E snapshots source-worktree status to prove it stays unchanged
    source_status = subprocess.run(
        ["git", "status", "--short"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    repository = tmp_path / "repository"
    nonstructural = _synthetic_repository(repository)
    manifest_path = tmp_path / "relocation.yaml"
    report_path = tmp_path / "relocation-report.json"
    manifest: dict[str, object] = {
        "schema_version": 3,
        "relocations": [
            {
                "from": "skills/relocate-source",
                "to": "skills/relocate-target",
                "python_modules": [
                    {"from": "relocate_fixture.old", "to": "relocate_fixture.new"}
                ],
            }
        ],
    }
    manifest_path.write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
    )
    interface = _load_adapter().Interface()
    monkeypatch.setattr(interface, "_synchronize", lambda repository, *, check: None)

    def invoke(*, apply: bool = False) -> tuple[int, dict[str, object]]:
        arguments = [
            "--root",
            str(repository),
            "--manifest",
            str(manifest_path),
            "--report",
            str(report_path),
        ]
        if apply:
            arguments.append("--apply")
        result = interface.run(interface.build_parser().parse_args(arguments))
        capsys.readouterr()
        report = (
            json.loads(report_path.read_text(encoding="utf-8"))
            if report_path.exists()
            else {}
        )
        return result, report

    result, first = invoke()

    assert result == 0
    assert (repository / "notes.md").read_text(encoding="utf-8") == nonstructural[
        "notes.md"
    ]
    assert "skills/relocate-target/blueprint.yaml" in first["writes"]
    engine = _load_runtime_module(
        f"{RUNTIME_PACKAGE}._relocation_engine", RTX_ROOT / "_relocation_engine.py"
    )
    changes = engine.plan_relocation(repository, engine.load_manifest(manifest_path))
    assert yaml.safe_load(
        changes.read_text("skills/relocate-target/blueprint.yaml")
    )["id"] == "relocate-target"
    assert changes.read_text("caller.py") == "from relocate_fixture.new import VALUE\n"
    assert yaml.safe_load(
        changes.read_text("skills/relocate-consumer/blueprint.yaml")
    )["dependencies"] == ["relocate-target"]
    assert "relocate-target.interface.default" in changes.read_text("consumer.yaml")
    assert (
        changes.read_text("scripts/use-relocation.sh")
        == "dispatcher --caller-skill relocate-target relocate-target.interface.default\n"
    )
    for relative, text in nonstructural.items():
        assert changes.read_text(relative) == text
    occurrence_paths = {item["path"] for item in first["semantic_occurrences"]}
    assert {
        "literal.py",
        "notes.md",
        "proof.tex",
        ".config/relocate-source/config.json",
    } <= occurrence_paths

    manifest["semantic_decisions"] = [
        _semantic_decision(
            occurrence,
            disposition=(
                "preserve"
                if preserve_config_namespace
                and occurrence["path"] == ".config/relocate-source/config.json"
                else "rewrite"
            ),
        )
        for occurrence in first["semantic_occurrences"]
    ]
    manifest_path.write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
    )

    result, decided = invoke()
    assert result == 0
    assert decided["unaccounted_semantic_occurrences"] == []
    result, _ = invoke(apply=True)
    assert result == 0
    result, postflight = invoke()
    assert result == 0
    assert postflight["writes"] == []
    assert postflight["deletes"] == []
    assert postflight["unaccounted_semantic_occurrences"] == []
    config = (repository / ".config/relocate-source/config.json").read_text(
        encoding="utf-8"
    )
    if preserve_config_namespace:
        assert config == nonstructural[".config/relocate-source/config.json"]
        assert any(
            item["path"] == ".config/relocate-source/config.json"
            for item in postflight["semantic_occurrences"]
        )
    else:
        assert config == '{"namespace":"relocate-target"}\n'
        assert all(
            item["match"] != "relocate-source"
            for item in postflight["semantic_occurrences"]
        )

    assert {path: path.read_bytes() for path in tracked} == source_bytes
    # famulus-raw-git: category=validator-isolation; reason=the isolated E2E verifies source-worktree status stayed unchanged
    assert subprocess.run(
        ["git", "status", "--short"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout == source_status
