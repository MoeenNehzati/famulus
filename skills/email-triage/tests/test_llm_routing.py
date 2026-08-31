from pathlib import Path

import yaml


SKILL_ROOT = Path(__file__).resolve().parents[1]


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _source_for_interface(
    root: dict, source_interface: str
) -> tuple[dict, dict]:
    source_id, _, _ = source_interface.rpartition(".interface.")
    locator = root["sources"][source_id]["blueprint"]
    assert locator["base"] == "module-root"
    source = _load_yaml(SKILL_ROOT / locator["path"])
    assert source["id"] == source_id
    return source, source["interfaces"][source_interface]


def _source_for_export(root: dict, export_id: str) -> tuple[dict, dict]:
    source_interface = root["exports"][export_id]["source_interface"]
    return _source_for_interface(root, source_interface)


def test_default_interface_routes_to_triage_and_declares_generated_interfaces() -> None:
    root = _load_yaml(SKILL_ROOT / "blueprint.yaml")
    _default, default_interface = _source_for_export(
        root, "email-triage.interface.default"
    )

    markdown_interfaces = []
    for export_id, export in root["exports"].items():
        if "source_interface" not in export:
            continue
        source, interface = _source_for_interface(root, export["source_interface"])
        if source["gateway"]["language"] == "Markdown":
            markdown_interfaces.append((export_id, interface["version"]))
    assert markdown_interfaces == [
        ("email-triage.interface.default", 2),
        ("email-triage.interface.triage", 2),
    ]
    assert default_interface["version"] == 2
    declared = {
        (entry["interface"], entry["version"])
        for entry in default_interface["uses_interfaces"]
    }
    body = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    generated_block = body.split("<!-- BEGIN BLUEPRINT INTERFACES -->", 1)[
        1
    ].split("<!-- END BLUEPRINT INTERFACES -->", 1)[0]
    executable_block = generated_block.split("Executable Interfaces:", 1)[1].split(
        "Instruction Interfaces:", 1
    )[0]
    generated_interface_ids = {
        line.split("`", 2)[1]
        for line in executable_block.splitlines()
        if line.startswith("- `")
    }
    generated_executable_interfaces = {
        (interface_id, version)
        for interface_id, version in declared
        if interface_id in generated_interface_ids
    }
    triage_route = (
        "email-triage.source.instructions-triage.interface.triage",
        2,
    )
    repair_route = (
        "setup-python-environment.interface.repair-selected-packages",
        1,
    )
    process_routes = {
        ("email-triage._rtx.interface.fetch-filtered-envelopes", 1),
        ("email-triage._rtx.interface.scripts-clear-failure", 1),
        ("email-triage._rtx.interface.scripts-filter-envelopes", 1),
        ("email-triage._rtx.interface.scripts-finalize-triage", 1),
        ("email-triage._rtx.interface.scripts-get-cutoff", 1),
        ("email-triage._rtx.interface.scripts-log-decision", 1),
        ("email-triage._rtx.interface.scripts-mark-failure", 1),
        ("email-triage._rtx.interface.scripts-prune-log", 1),
        ("email-triage._rtx.interface.scripts-update-watermark", 1),
        ("email-triage._rtx.interface.scripts-write-metrics", 1),
    }

    assert generated_interface_ids == {
        interface_id for interface_id, _version in process_routes
    }
    assert generated_executable_interfaces == process_routes
    assert declared == process_routes | {triage_route, repair_route}
    assert f"`{triage_route[0]}@{triage_route[1]}`" in generated_block
    assert f"`{repair_route[0]}@{repair_route[1]}`" in generated_block

    authored = body.split("<!-- END BLUEPRINT INTERFACES -->", 1)[1]
    assert "email-triage.interface.triage" in authored
    assert "update-personal-preferences" not in authored
    assert "choose" not in authored.lower()


def test_preference_management_files_are_removed() -> None:
    root = _load_yaml(SKILL_ROOT / "blueprint.yaml")
    removed = [
        SKILL_ROOT / "instructions" / "update-personal-preferences.md",
        SKILL_ROOT / "instructions" / ".update-personal-preferences.md.blueprint.yaml",
        SKILL_ROOT / "references" / "personal-preferences.md",
        SKILL_ROOT / "references" / ".personal-preferences.md.blueprint.yaml",
    ]

    assert [path for path in removed if path.exists()] == []
    assert all("personal-preferences" not in source for source in root["sources"])


def test_frontmatter_discovers_only_email_triage_requests() -> None:
    body = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    metadata = yaml.safe_load(body.split("---", 2)[1])
    description = metadata["description"].lower()

    assert description.startswith("use when")
    assert "inbox-level email triage" in description
    assert "processing" in description
    assert "preferences" not in description
    for action in ("add", "change", "remove", "review", "reset"):
        assert action not in description


def test_triage_contract_has_no_preference_source_or_read() -> None:
    root = _load_yaml(SKILL_ROOT / "blueprint.yaml")
    triage_source, triage = _source_for_export(
        root, "email-triage.interface.triage"
    )

    assert triage["version"] == 2
    assert [
        (
            dependency["blueprint"]["base"],
            dependency["blueprint"]["path"],
            dependency["source"],
            dependency["version"],
        )
        for dependency in triage_source["dependencies"]
    ] == [
        (
            "repository-root",
            "skills/setup-python-environment/blueprints/gateway.yaml",
            "setup-python-environment.source.gateway",
            1,
        )
    ]
    assert all(
        entry.get("path") != "references/personal-preferences.md"
        for entry in triage["contract"]["direct_io"]["reads"]
    )


def test_canonical_triage_workflow_is_retained() -> None:
    body = (SKILL_ROOT / "instructions" / "triage.md").read_text(
        encoding="utf-8"
    )
    normalized_body = " ".join(body.split())

    assert "Personal preferences" not in body
    assert "personal-preferences.md" not in body
    for marker in (
        "email-triage._rtx.interface.fetch-filtered-envelopes",
        "email-client.interface.default`'s `mail-read` interface",
        "email-triage._rtx.interface.scripts-log-decision",
        "email-triage._rtx.interface.scripts-mark-failure",
        "email-triage._rtx.interface.scripts-finalize-triage",
    ):
        assert marker in body
    assert "Steps 1 and 2 explicitly parallelize unrelated reads" in normalized_body


def test_triage_workflow_retains_exact_ordered_steps() -> None:
    body = (SKILL_ROOT / "instructions" / "triage.md").read_text(
        encoding="utf-8"
    )
    step_headings = [
        line
        for line in body.splitlines()
        if line.startswith("## Step ")
    ]

    assert step_headings == [
        "## Step 1 — Fetch new envelopes (run in parallel per account)",
        "## Step 2 — Read email bodies in batches",
        "## Step 3 — Read both destination lists via `list-manager.interface.default`",
        "## Step 4 — Add action items, deduplicating",
        "## Step 5 — Collect metrics and report",
        "## Step 6 — Finalize the run (metrics + watermark), then prune log",
    ]


def test_triage_uses_mail_read_interface_without_raw_invocation_template() -> None:
    body = (SKILL_ROOT / "instructions" / "triage.md").read_text(
        encoding="utf-8"
    )

    assert "Use `email-client.interface.default`'s `mail-read` interface" in body
    assert "mail-read -a" not in body
    assert "<account> <ID>" not in body
