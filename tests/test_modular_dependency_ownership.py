from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import re

import pytest
import yaml



ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "references" / "blueprint-schema" / "runtime_dependencies.json"
REPAIR = {
    "interface": "setup-python-environment.interface.repair-selected-packages",
    "version": 1,
}
REPAIR_SOURCE = {
    "blueprint": {
        "base": "repository-root",
        "path": "skills/setup-python-environment/blueprints/gateway.yaml",
    },
    "source": "setup-python-environment.source.gateway",
    "version": 1,
}
GOOGLE_OWNERS = {"connect-google", "cloud-files", "online-calendar", "email-client"}
RESIDUAL = {
    "bib-audit": ("bibtexparser",),
    "daily-plan": ("keyring", "rich"),
    "email-triage": ("keyring",),
    "list-manager": ("dateparser", "keyring", "rich"),
    "node-certify": ("cryptography", "keyring", "pyflakes", "pytest", "pytest-xdist"),
    "node-drift": ("cryptography", "keyring"),
    "pdf-to-markdown": ("marker-pdf",),
}
CORE_ONLY = {
    "distill-to-rutters",
    "recurring-tasks",
    "regenerate-blueprints",
    "relocate-nodes",
    "skill-maker",
}


def _manifest() -> dict[str, object]:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _package_names(module: dict[str, object]) -> set[str]:
    return {
        dependency["name"]
        for interface in module["interfaces"].values()
        for dependency in interface["dependencies"]
        if dependency["kind"] == "python-package"
    }


def _core_names() -> set[str]:
    requirements = json.loads((ROOT / "mcp-core.json").read_text(encoding="utf-8"))[
        "core_packages"
    ]
    return {
        re.split(r"[<>=!~]", requirement, maxsplit=1)[0].casefold()
        for requirement in requirements
    }


def _gateway(owner: str, name: str = "gateway.yaml") -> dict[str, object]:
    return yaml.safe_load(
        (ROOT / "skills" / owner / "blueprints" / name).read_text(encoding="utf-8")
    )


def _authored(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    return text.split("<!-- END BLUEPRINT INTERFACES -->", 1)[1]


def _task2_templates() -> dict[str, list[str]]:
    text = (ROOT / "skills" / "setup-python-environment" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    return {
        name: json.loads(payload)
        for name, payload in re.findall(
            r"<!-- command:([a-z-]+) -->\n```json\n([^`]+)```", text
        )
    }


def _expand(argv: list[str], executable: str, packages: tuple[str, ...]) -> list[str]:
    expanded: list[str] = []
    for token in argv:
        if token == "${canonical_executable}":
            expanded.append(executable)
        elif token == "${selected_packages}":
            expanded.extend(packages)
        else:
            expanded.append(token)
    return expanded


def test_exhaustive_projection_has_no_orphaned_python_package() -> None:
    manifest = _manifest()
    skills = manifest["skills"]
    pairs = {
        (owner, package)
        for owner, module in skills.items()
        for package in _package_names(module)
    }
    core_names = _core_names()
    core_pairs = {(owner, package) for owner, package in pairs if package.casefold() in core_names}
    google_pairs = {(owner, package) for owner, package in pairs if owner in GOOGLE_OWNERS}
    residual_pairs = {
        (owner, package)
        for owner, packages in RESIDUAL.items()
        for package in packages
    }

    assert len(pairs) == 33
    assert len(core_pairs) == 14
    assert len(google_pairs) == 4
    assert len(residual_pairs) == 15
    assert pairs == core_pairs | google_pairs | residual_pairs
    assert core_pairs.isdisjoint(google_pairs | residual_pairs)
    assert google_pairs.isdisjoint(residual_pairs)


def test_residual_declarations_and_optional_tiers_are_exact() -> None:
    skills = _manifest()["skills"]
    core_names = _core_names()

    for owner, expected in RESIDUAL.items():
        actual = tuple(
            sorted(
                (
                    package
                    for package in _package_names(skills[owner])
                    if package.casefold() not in core_names
                ),
                key=str.casefold,
            )
        )
        assert actual == expected
        assert skills[owner]["installation_tier"] == "optional"


def test_every_owner_gateway_declares_repair_before_feature_interfaces() -> None:
    for owner in RESIDUAL:
        gateway = _gateway(owner)
        assert [
            {
                "blueprint": dependency["blueprint"],
                "source": dependency["source"],
                "version": dependency["version"],
            }
            for dependency in gateway["dependencies"]
            if dependency.get("source") == REPAIR_SOURCE["source"]
        ] == [REPAIR_SOURCE]
        assert REPAIR in gateway["uses_interfaces"]
        for interface in gateway["interfaces"].values():
            assert REPAIR in interface["uses_interfaces"]

    triage = _gateway("email-triage", "instructions-triage.yaml")
    assert [
        {
            "blueprint": dependency["blueprint"],
            "source": dependency["source"],
            "version": dependency["version"],
        }
        for dependency in triage["dependencies"]
        if dependency.get("source") == REPAIR_SOURCE["source"]
    ] == [REPAIR_SOURCE]
    assert REPAIR in triage["uses_interfaces"]
    assert REPAIR in next(iter(triage["interfaces"].values()))["uses_interfaces"]


def test_authored_owner_order_and_exact_declarations() -> None:
    boundary_markers = {
        "bib-audit": "## Invocation",
        "daily-plan": "invoke `orchestrate`",
        "email-triage": "Use `email-triage.interface.triage`",
        "list-manager": "When this skill is used",
        "node-certify": "## Certification algorithm",
        "node-drift": "Use `node-drift._rtx.interface.drift-status`",
    }
    repair_marker = "`setup-python-environment.interface.repair-selected-packages`"

    for owner, boundary in boundary_markers.items():
        text = _authored(ROOT / "skills" / owner / "SKILL.md")
        repair_index = text.index(repair_marker)
        assert repair_index < text.index(boundary)
        paragraph = next(part for part in text.split("\n\n") if repair_marker in part)
        assert json.dumps(list(RESIDUAL[owner])) in paragraph
        assert "failure" in paragraph.casefold()

    detailed = (ROOT / "skills" / "email-triage" / "instructions" / "triage.md").read_text(
        encoding="utf-8"
    )
    assert detailed.index(repair_marker) < detailed.index(
        "email-triage._rtx.interface.fetch-filtered-envelopes"
    )
    assert json.dumps(list(RESIDUAL["email-triage"])) in detailed


def test_pdf_repair_is_only_in_marker_fallback() -> None:
    text = _authored(ROOT / "skills" / "pdf-to-markdown" / "SKILL.md")
    repair = text.index("`setup-python-environment.interface.repair-selected-packages`")
    source_done = text.index("If LaTeX source found anywhere: download, extract, done.")
    fallback = text.index("## Step 2 — PDF fallback")
    marker_probe = text.index("scripts-check-marker-models")

    assert source_done < fallback < repair < marker_probe
    assert json.dumps(list(RESIDUAL["pdf-to-markdown"])) in text


def test_list_setup_is_local_and_google_remains_explicit() -> None:
    blueprint = yaml.safe_load(
        (ROOT / "skills" / "list-manager" / "blueprint.yaml").read_text(encoding="utf-8")
    )
    assert blueprint["exports"]["list-manager.interface.setup"]["setup_requires_setup_of"] == []

    text = _authored(ROOT / "skills" / "list-manager" / "SKILL.md")
    repair = text.index("`setup-python-environment.interface.repair-selected-packages`")
    assert repair < text.index("When this skill is used")
    paragraph = next(part for part in text.split("\n\n") if "repair-selected-packages" in part)
    assert "local" in paragraph.casefold()
    assert "connect-google" not in paragraph
    assert "google connection setup" in paragraph.casefold()
    assert "cloud" in paragraph.casefold()


@dataclass
class _RepairHarness:
    installed: set[str] = field(default_factory=set)
    calls: list[tuple[str, list[str]]] = field(default_factory=list)
    installs: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)
    boundaries: list[str] = field(default_factory=list)
    canonical: str = "/tmp/Selected Python/python"

    def repair(self, owner: str, *, failure: str | None = None) -> bool:
        templates = _task2_templates()
        packages = RESIDUAL[owner]
        initial_argv = templates["initial-fingerprint"]
        self.calls.append((owner, initial_argv))
        if failure == "missing-python":
            return False
        initial = {
            "executable": self.canonical,
            "prefix": "/tmp/Selected Python",
            "base_prefix": "/tmp/Selected Python",
            "version": [3, 13],
        }
        for name, failure_name in (
            ("pip-check", "missing-pip"),
            ("target-check", "non-writable"),
        ):
            self.calls.append((owner, _expand(templates[name], self.canonical, packages)))
            if failure == failure_name:
                return False
        self.calls.append(
            (owner, _expand(templates["pip-preflight"], self.canonical, packages))
        )
        if failure == "pip-refusal":
            return False
        missing = tuple(package for package in packages if package.casefold() not in self.installed)
        if missing:
            self.calls.append(
                (owner, _expand(templates["pip-install"], self.canonical, packages))
            )
            self.installs.append((owner, missing))
            self.installed.update(package.casefold() for package in missing)
        self.calls.append((owner, templates["final-fingerprint"]))
        final = dict(initial)
        if failure == "fingerprint-drift":
            final["executable"] = "/tmp/Other Python/python"
        if final != initial:
            return False
        self.boundaries.append(owner)
        return True


@pytest.mark.parametrize("owner", tuple(RESIDUAL))
def test_each_owner_repairs_only_its_exact_residual_declaration(owner: str) -> None:
    harness = _RepairHarness()

    assert harness.repair(owner)
    assert harness.installs == [(owner, RESIDUAL[owner])]
    assert {called_owner for called_owner, _ in harness.calls} == {owner}
    assert harness.boundaries == [owner]
    assert all(argv[0] in {"python", harness.canonical} for _, argv in harness.calls)
    assert all(token not in {"python3", "py"} for _, argv in harness.calls for token in argv)


def test_shared_packages_are_reused_without_transferring_ownership() -> None:
    harness = _RepairHarness()

    assert harness.repair("node-drift")
    assert harness.repair("node-certify")
    assert harness.installs == [
        ("node-drift", ("cryptography", "keyring")),
        ("node-certify", ("pyflakes", "pytest", "pytest-xdist")),
    ]


@pytest.mark.parametrize("owner", tuple(RESIDUAL))
@pytest.mark.parametrize(
    "failure", ("missing-python", "missing-pip", "non-writable", "pip-refusal", "fingerprint-drift")
)
def test_every_owner_failure_stops_before_feature_use(owner: str, failure: str) -> None:
    harness = _RepairHarness(installed={package.casefold() for package in RESIDUAL[owner]})

    assert not harness.repair(owner, failure=failure)
    assert harness.installs == []
    assert harness.boundaries == []


def test_core_only_and_pdf_source_only_paths_do_not_repair() -> None:
    harness = _RepairHarness()
    skills = _manifest()["skills"]

    for owner in CORE_ONLY:
        assert not (
            _package_names(skills[owner]) - {
                package for package in _package_names(skills[owner]) if package.casefold() in _core_names()
            }
        )
    assert harness.calls == []
    assert harness.installs == []
    assert harness.boundaries == []
