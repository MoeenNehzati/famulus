from pathlib import Path

import yaml


SKILL_ROOT = Path(__file__).resolve().parents[1]


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_default_interface_routes_only_to_triage_v2() -> None:
    root = _load_yaml(SKILL_ROOT / "blueprint.yaml")
    default = _load_yaml(SKILL_ROOT / ".SKILL.md.blueprint.yaml")

    llm_interfaces = [
        (entry["interface"], entry["version"])
        for entry in root["interfaces"]
        if ".llm." in entry["interface"]
    ]
    assert llm_interfaces == [
        ("email-triage.llm.default", 2),
        ("email-triage.llm.triage", 2),
    ]
    assert default["version"] == 2
    assert default["uses_interfaces"] == [
        {"interface": "email-triage.llm.triage", "version": 2}
    ]

    body = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    authored = body.split("<!-- END BLUEPRINT INTERFACES -->", 1)[1]
    assert "email-triage.llm.triage" in authored
    assert "update-personal-preferences" not in authored
    assert "choose" not in authored.lower()


def test_preference_management_files_are_removed() -> None:
    removed = [
        SKILL_ROOT / "llm_interfaces" / "update-personal-preferences.md",
        SKILL_ROOT / "llm_interfaces" / ".update-personal-preferences.md.blueprint.yaml",
        SKILL_ROOT / "references" / "personal-preferences.md",
        SKILL_ROOT / "references" / ".personal-preferences.md.blueprint.yaml",
    ]

    assert [path for path in removed if path.exists()] == []


def test_frontmatter_discovers_only_email_triage_requests() -> None:
    body = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    frontmatter = body.split("---", 2)[1].lower()

    assert "description: use when" in frontmatter
    assert "triage email" in frontmatter
    assert "process the inbox" in frontmatter
    assert "preferences" not in frontmatter
    for action in ("add", "change", "remove", "review", "reset"):
        assert action not in frontmatter


def test_triage_contract_has_no_preference_source_or_read() -> None:
    triage = _load_yaml(
        SKILL_ROOT / "llm_interfaces" / ".triage.md.blueprint.yaml"
    )

    assert triage["version"] == 2
    assert triage["behavior_sources"] == []
    assert all(
        entry.get("path") != "references/personal-preferences.md"
        for entry in triage["direct_io"]["reads"]
    )


def test_canonical_triage_workflow_is_retained() -> None:
    body = (SKILL_ROOT / "llm_interfaces" / "triage.md").read_text(
        encoding="utf-8"
    )

    assert "Personal preferences" not in body
    assert "personal-preferences.md" not in body
    for marker in (
        "## Step 1",
        "## Step 3",
        "## Step 4",
        "## Step 5",
        "## Step 6",
        "## Step 7",
        "email-triage.machine.fetch-filtered-envelopes",
        "email-client.llm.default`'s `mail-read` interface",
        "email-triage.machine.scripts-log-decision",
        "email-triage.machine.scripts-mark-failure",
        "email-triage.machine.scripts-update-watermark",
    ):
        assert marker in body


def test_triage_uses_mail_read_interface_without_raw_invocation_template() -> None:
    body = (SKILL_ROOT / "llm_interfaces" / "triage.md").read_text(
        encoding="utf-8"
    )

    assert "Use `email-client.llm.default`'s `mail-read` interface" in body
    assert "mail-read -a" not in body
    assert "<account> <ID>" not in body
