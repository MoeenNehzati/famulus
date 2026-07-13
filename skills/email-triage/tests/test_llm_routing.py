from pathlib import Path

import yaml


SKILL_ROOT = Path(__file__).resolve().parents[1]


def _normalized(text: str) -> str:
    return " ".join(text.lower().split())


def test_default_interface_routes_by_preference_intent() -> None:
    body = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")

    assert "email-triage.llm.update-personal-preferences" in body
    assert "email-triage.llm.triage" in body
    assert "add, change, remove, review, or reset" in body
    assert "Every other" in body


def test_named_interfaces_are_separate_instruction_files() -> None:
    triage = SKILL_ROOT / "llm_interfaces" / "triage.md"
    update = SKILL_ROOT / "llm_interfaces" / "update-personal-preferences.md"

    assert triage.is_file()
    assert update.is_file()
    assert "## Step 1" in triage.read_text(encoding="utf-8")
    assert "sole writer" in update.read_text(encoding="utf-8")


def test_personal_preferences_source_is_present_and_empty() -> None:
    preferences = SKILL_ROOT / "references" / "personal-preferences.md"

    assert preferences.is_file()
    assert preferences.read_bytes() == b""


def test_frontmatter_discovers_preference_management_requests() -> None:
    body = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    frontmatter = body.split("---", 2)[1].lower()

    assert "description: use when" in frontmatter
    assert "email-triage preferences" in frontmatter
    for action in ("add", "change", "remove", "review", "reset"):
        assert action in frontmatter


def test_triage_preferences_are_scoped_and_canonical_rules_win() -> None:
    body = _normalized((SKILL_ROOT / "llm_interfaces" / "triage.md").read_text(encoding="utf-8"))

    assert "classification judgment, item wording, and report presentation" in body
    assert "canonical rule" in body
    assert "follow the canonical rule" in body


def test_unreadable_preferences_stop_before_classification() -> None:
    body = (SKILL_ROOT / "llm_interfaces" / "triage.md").read_text(encoding="utf-8").lower()

    assert "unreadable" in body
    assert "stop" in body
    assert "before classification" in body
    assert "path" in body


def test_destructive_preference_changes_require_confirmation() -> None:
    body = (SKILL_ROOT / "llm_interfaces" / "update-personal-preferences.md").read_text(
        encoding="utf-8"
    ).lower()

    assert "reset, removal, or other destructive rewrite" in body
    assert "obtain confirmation before writing" in body


def test_preference_write_failure_preserves_content_and_never_claims_success() -> None:
    body = _normalized(
        (SKILL_ROOT / "llm_interfaces" / "update-personal-preferences.md").read_text(
            encoding="utf-8"
        )
    )

    assert "atomic" in body
    assert "prior content" in body
    assert "report the failure" in body
    assert "never claim" in body
    assert "saved" in body


def test_successful_preference_write_reports_hash_change_and_stale_audit() -> None:
    body = _normalized(
        (SKILL_ROOT / "llm_interfaces" / "update-personal-preferences.md").read_text(
            encoding="utf-8"
        )
    )

    assert "successful write" in body
    assert "bound-file hash changed" in body
    assert "audit" in body
    assert "stale" in body


def test_preference_review_is_read_only() -> None:
    body = (SKILL_ROOT / "llm_interfaces" / "update-personal-preferences.md").read_text(
        encoding="utf-8"
    ).lower()

    assert "review" in body
    assert "read-only" in body
    assert "without writing" in body


def test_updater_owns_preferences_and_triage_is_declared_reader() -> None:
    update_sidecar = yaml.safe_load(
        (SKILL_ROOT / "llm_interfaces" / ".update-personal-preferences.md.blueprint.yaml").read_text(
            encoding="utf-8"
        )
    )
    ownership = update_sidecar["owns_filesystem"]

    assert ownership == [
        {
            "match": "exact",
            "path": "references/personal-preferences.md",
            "allowed_readers": ["email-triage.llm.triage"],
            "reason": "The preference-update interface is the sole writer of user-level email-triage behavior.",
        }
    ]
    assert "manag" in update_sidecar["behavior_sources"][0]["reason"].lower()


def test_triage_uses_mail_read_interface_without_raw_invocation_template() -> None:
    body = (SKILL_ROOT / "llm_interfaces" / "triage.md").read_text(encoding="utf-8")

    assert "Use `email-client.llm.default`'s `mail-read` interface" in body
    assert "mail-read -a" not in body
    assert "<account> <ID>" not in body
