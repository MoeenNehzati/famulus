"""Smoke tests for skills/skill-maker/validators/blueprints.py."""
from __future__ import annotations

import importlib.util
from pathlib import Path

_VALIDATOR = (
    Path(__file__).resolve().parents[1]
    / "skills" / "skill-maker" / "validators" / "blueprints.py"
)
_spec = importlib.util.spec_from_file_location("blueprints", _VALIDATOR)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def _make_template(tmp_path: Path) -> None:
    t = tmp_path / "references" / "blueprint"
    t.mkdir(parents=True)
    (t / "template.yaml").write_text("# blueprint template\n")


def _default_llm() -> dict:
    return {
        "default": {
            "version": 1,
            "description": "Primary LLM-facing skill instructions.",
            "binding": {"kind": "skill_file", "path": "SKILL.md"},
            "directly_reads": ["SKILL.md"],
            "directly_executes": [],
            "directly_writes": [],
            "direct_io": {
                "reads": [
                    {
                        "medium": "prompt",
                        "access": "read",
                        "content": "document",
                        "format": "text",
                        "sensitivity": "user-private",
                    }
                ],
                "writes": [
                    {
                        "medium": "prompt",
                        "access": "write",
                        "content": "response",
                        "format": "markdown",
                        "sensitivity": "derived-private",
                    }
                ],
                "network": [],
            },
            "owns_filesystem": [],
        }
    }


def _taxonomy() -> dict:
    return {
        "role": "automation",
        "kind": "analyzer",
    }


def _empty_direct_io() -> dict:
    return {
        "direct_io": {
            "reads": [],
            "writes": [],
            "network": [],
        }
    }


def _empty_ownership() -> dict:
    return {"owns_filesystem": []}


def test_no_skills_passes(tmp_path: Path) -> None:
    (tmp_path / "skills").mkdir()
    _make_template(tmp_path)
    assert _mod.validate(tmp_path) == []


def test_skill_without_blueprint_flagged(tmp_path: Path) -> None:
    _make_template(tmp_path)
    skill = tmp_path / "skills" / "my-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: my-skill\n---\nBody.\n")
    errors = _mod.validate(tmp_path)
    assert any("missing blueprint.yaml" in e for e in errors)


def test_skill_with_blueprint_but_no_contract_flagged(tmp_path: Path) -> None:
    _make_template(tmp_path)
    skill = tmp_path / "skills" / "my-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: my-skill\n---\nBody.\n")
    (skill / "blueprint.yaml").write_text("name: my-skill\n")
    errors = _mod.validate(tmp_path)
    assert any("contract block" in e for e in errors)


def test_machine_interface_without_dependencies_flagged_by_schema() -> None:
    schema = _mod._load_schema()
    assert schema is not None
    blueprint = {
        "category": "workflow-general-assistant",
        **_taxonomy(),
        "interfaces": {
            "machine": {
                "scan": {
                    "version": 1,
                    "runtime": {
                        "kind": "python_machine_interface",
                        "entrypoint": "_rtx/_handoff_scan.py:Interface",
                    },
                }
            }
        },
    }

    errors = _mod._validate_blueprint_schema(Path("blueprint.yaml"), blueprint, schema)

    assert any("dependencies" in error and "required" in error for error in errors)


def test_script_interfaces_are_rejected_by_schema() -> None:
    schema = _mod._load_schema()
    assert schema is not None
    blueprint = {
        "category": "workflow-general-assistant",
        **_taxonomy(),
        "script_interfaces": {
            "scan": {
                "id": "scan",
                "command": ["python3", "_rtx/_handoff_scan.py"],
            }
        },
    }

    errors = _mod._validate_blueprint_schema(Path("blueprint.yaml"), blueprint, schema)

    assert any("script_interfaces" in error and "Additional properties" in error for error in errors)


def test_role_and_kind_are_required_by_schema() -> None:
    schema = _mod._load_schema()
    assert schema is not None
    blueprint = {
        "category": "workflow-general-assistant",
        "interfaces": {"llm": _default_llm()},
    }

    errors = _mod._validate_blueprint_schema(Path("blueprint.yaml"), blueprint, schema)

    assert any("'role' is a required property" in error for error in errors)
    assert any("'kind' is a required property" in error for error in errors)


def test_role_and_kind_reject_unknown_values_by_schema() -> None:
    schema = _mod._load_schema()
    assert schema is not None
    blueprint = {
        "category": "workflow-general-assistant",
        "role": "personal-productivity",
        "kind": "helper",
        "interfaces": {"llm": _default_llm()},
    }

    errors = _mod._validate_blueprint_schema(Path("blueprint.yaml"), blueprint, schema)

    assert any("role" in error and "is not one of" in error for error in errors)
    assert any("kind" in error and "is not one of" in error for error in errors)


def test_top_level_dependency_and_interface_version_are_rejected_by_schema() -> None:
    schema = _mod._load_schema()
    assert schema is not None
    blueprint = {
        "category": "workflow-general-assistant",
        **_taxonomy(),
        "interface_version": 1,
        "depends_on": {"other-skill": {"major_version": 1}},
        "interfaces": {"llm": _default_llm()},
    }

    errors = _mod._validate_blueprint_schema(Path("blueprint.yaml"), blueprint, schema)

    assert any("interface_version" in error and "Additional properties" in error for error in errors)
    assert any("depends_on" in error and "Additional properties" in error for error in errors)


def test_interface_version_is_required_by_schema() -> None:
    schema = _mod._load_schema()
    assert schema is not None
    blueprint = {
        "category": "workflow-general-assistant",
        **_taxonomy(),
        "interfaces": {
            "llm": {
                "default": {
                    "description": "Primary LLM-facing skill instructions.",
                    "binding": {"kind": "skill_file", "path": "SKILL.md"},
                    "directly_reads": ["SKILL.md"],
                    "directly_executes": [],
                    "directly_writes": [],
                    **_empty_direct_io(),
                    **_empty_ownership(),
                }
            }
        },
    }

    errors = _mod._validate_blueprint_schema(Path("blueprint.yaml"), blueprint, schema)

    assert any("interfaces.llm.default" in error and "'version' is a required property" in error for error in errors)


def test_direct_io_is_required_by_schema() -> None:
    schema = _mod._load_schema()
    assert schema is not None
    blueprint = {
        "category": "workflow-general-assistant",
        **_taxonomy(),
        "interfaces": {
            "machine": {
                "scan": {
                    "version": 1,
                    "runtime": {
                        "kind": "python_machine_interface",
                        "entrypoint": "_rtx/_handoff_scan.py:Interface",
                    },
                    "dependencies": [],
                    "directly_reads": [],
                    "directly_executes": ["_rtx/_handoff_scan.py"],
                    "directly_writes": [],
                }
            },
            "llm": _default_llm(),
        },
    }

    errors = _mod._validate_blueprint_schema(Path("blueprint.yaml"), blueprint, schema)

    assert any("direct_io" in error and "required" in error for error in errors)


def test_direct_io_rejects_unknown_medium_by_schema() -> None:
    schema = _mod._load_schema()
    assert schema is not None
    blueprint = {
        "category": "workflow-general-assistant",
        **_taxonomy(),
        "interfaces": {
            "llm": {
                "default": {
                    "description": "Primary LLM-facing skill instructions.",
                    "binding": {"kind": "skill_file", "path": "SKILL.md"},
                    "directly_reads": ["SKILL.md"],
                    "directly_executes": [],
                    "directly_writes": [],
                    "direct_io": {
                        "reads": [
                            {
                                "medium": "remote",
                                "access": "read",
                                "content": "document",
                                "sensitivity": "user-private",
                            }
                        ],
                        "writes": [],
                        "network": [],
                    },
                }
            }
        },
    }

    errors = _mod._validate_blueprint_schema(Path("blueprint.yaml"), blueprint, schema)

    assert any("direct_io.reads.0.medium" in error and "is not one of" in error for error in errors)


def test_direct_io_rejects_field_level_content_values() -> None:
    blueprint = {
        "interfaces": {
            "llm": {
                "default": {
                    "direct_io": {
                        "reads": [
                            {
                                "medium": "prompt",
                                "access": "read",
                                "content": "email-subject",
                                "sensitivity": "user-private",
                            }
                        ],
                        "writes": [],
                        "network": [],
                    }
                }
            }
        }
    }

    errors = _mod._validate_direct_io_content_granularity(Path("blueprint.yaml"), blueprint)

    assert errors == [
        "blueprint.yaml: llm interface 'default' direct_io.reads.0.content "
        "uses field-level value 'email-subject'; use a coarser aggregate content value"
    ]


def test_owns_filesystem_is_required_by_schema() -> None:
    schema = _mod._load_schema()
    assert schema is not None
    blueprint = {
        "category": "workflow-general-assistant",
        **_taxonomy(),
        "interfaces": {
            "llm": {
                "default": {
                    "description": "Primary LLM-facing skill instructions.",
                    "binding": {"kind": "skill_file", "path": "SKILL.md"},
                    "directly_reads": ["SKILL.md"],
                    "directly_executes": [],
                    "directly_writes": [],
                    **_empty_direct_io(),
                }
            }
        },
    }

    errors = _mod._validate_blueprint_schema(Path("blueprint.yaml"), blueprint, schema)

    assert any("owns_filesystem" in error and "required" in error for error in errors)


def test_filesystem_ownership_restricts_write_and_read_access() -> None:
    blueprint_path = Path("/repo/skills/owner-skill/blueprint.yaml")
    blueprint = {
        "interfaces": {
            "machine": {
                "owner": {
                    "owns_filesystem": [
                        {
                            "match": "exact",
                            "path": "$repo/data/private.yaml",
                            "allowed_readers": ["owner-skill.machine.allowed-reader"],
                        }
                    ],
                    "direct_io": {
                        "reads": [],
                        "writes": [
                            {
                                "medium": "local-filesystem",
                                "access": "write",
                                "content": "source",
                                "sensitivity": "user-private",
                                "path": "$repo/data/private.yaml",
                            }
                        ],
                        "network": [],
                    },
                },
                "allowed-reader": {
                    "owns_filesystem": [],
                    "direct_io": {
                        "reads": [
                            {
                                "medium": "local-filesystem",
                                "access": "read",
                                "content": "source",
                                "sensitivity": "user-private",
                                "path": "$repo/data/private.yaml",
                            }
                        ],
                        "writes": [],
                        "network": [],
                    },
                },
                "intruder": {
                    "owns_filesystem": [],
                    "direct_io": {
                        "reads": [
                            {
                                "medium": "local-filesystem",
                                "access": "read",
                                "content": "source",
                                "sensitivity": "user-private",
                                "path": "$repo/data/private.yaml",
                            }
                        ],
                        "writes": [
                            {
                                "medium": "local-filesystem",
                                "access": "write",
                                "content": "source",
                                "sensitivity": "user-private",
                                "path": "$repo/data/private.yaml",
                            }
                        ],
                        "network": [],
                    },
                },
            }
        }
    }

    errors = _mod._validate_filesystem_ownership({blueprint_path: blueprint})

    assert errors == [
        "/repo/skills/owner-skill/blueprint.yaml: owner-skill.machine.intruder "
        "direct_io.reads.0.path '$repo/data/private.yaml' is owned by "
        "owner-skill.machine.owner; add this interface to allowed_readers or read "
        "through an authorized interface",
        "/repo/skills/owner-skill/blueprint.yaml: owner-skill.machine.intruder "
        "direct_io.writes.0.path '$repo/data/private.yaml' is owned by "
        "owner-skill.machine.owner; only the owner may write it",
    ]


def test_filesystem_ownership_rejects_invalid_regex() -> None:
    blueprint_path = Path("/repo/skills/owner-skill/blueprint.yaml")
    blueprint = {
        "interfaces": {
            "machine": {
                "owner": {
                    "owns_filesystem": [
                        {
                            "match": "regex",
                            "path": "[",
                            "allowed_readers": [],
                        }
                    ],
                    "direct_io": {"reads": [], "writes": [], "network": []},
                }
            }
        }
    }

    errors = _mod._validate_filesystem_ownership({blueprint_path: blueprint})

    assert len(errors) == 1
    assert "owns_filesystem regex '[' is invalid" in errors[0]


def test_filesystem_ownership_allows_cross_skill_reader_and_blocks_writer() -> None:
    owner_path = Path("/repo/skills/owner-skill/blueprint.yaml")
    reader_path = Path("/repo/skills/reader-skill/blueprint.yaml")
    owner_blueprint = {
        "interfaces": {
            "machine": {
                "owner": {
                    "owns_filesystem": [
                        {
                            "match": "regex",
                            "path": "\\$repo/data/private/.*\\.yaml",
                            "allowed_readers": ["reader-skill.machine.reader"],
                        }
                    ],
                    "direct_io": {
                        "reads": [],
                        "writes": [
                            {
                                "medium": "local-filesystem",
                                "access": "write",
                                "content": "source",
                                "sensitivity": "user-private",
                                "path": "$repo/data/private/item.yaml",
                            }
                        ],
                        "network": [],
                    },
                }
            }
        }
    }
    reader_blueprint = {
        "interfaces": {
            "machine": {
                "reader": {
                    "owns_filesystem": [],
                    "direct_io": {
                        "reads": [
                            {
                                "medium": "local-filesystem",
                                "access": "read",
                                "content": "source",
                                "sensitivity": "user-private",
                                "path": "$repo/data/private/item.yaml",
                            }
                        ],
                        "writes": [],
                        "network": [],
                    },
                },
                "writer": {
                    "owns_filesystem": [],
                    "direct_io": {
                        "reads": [],
                        "writes": [
                            {
                                "medium": "local-filesystem",
                                "access": "write",
                                "content": "source",
                                "sensitivity": "user-private",
                                "path": "$repo/data/private/item.yaml",
                            }
                        ],
                        "network": [],
                    },
                },
            }
        }
    }

    errors = _mod._validate_filesystem_ownership(
        {owner_path: owner_blueprint, reader_path: reader_blueprint}
    )

    assert errors == [
        "/repo/skills/reader-skill/blueprint.yaml: reader-skill.machine.writer "
        "direct_io.writes.0.path '$repo/data/private/item.yaml' is owned by "
        "owner-skill.machine.owner; only the owner may write it"
    ]


def test_filesystem_ownership_rejects_unknown_allowed_reader() -> None:
    blueprint_path = Path("/repo/skills/owner-skill/blueprint.yaml")
    blueprint = {
        "interfaces": {
            "machine": {
                "owner": {
                    "owns_filesystem": [
                        {
                            "match": "exact",
                            "path": "$repo/data/private.yaml",
                            "allowed_readers": ["missing-skill.machine.reader"],
                        }
                    ],
                    "direct_io": {"reads": [], "writes": [], "network": []},
                }
            }
        }
    }

    errors = _mod._validate_filesystem_ownership({blueprint_path: blueprint})

    assert errors == [
        "/repo/skills/owner-skill/blueprint.yaml: owner-skill.machine.owner "
        "owns_filesystem allows unknown reader 'missing-skill.machine.reader'"
    ]


def test_filesystem_ownership_rejects_overlapping_owners() -> None:
    blueprint_path = Path("/repo/skills/owner-skill/blueprint.yaml")
    blueprint = {
        "interfaces": {
            "machine": {
                "owner": {
                    "owns_filesystem": [
                        {
                            "match": "exact",
                            "path": "$repo/data/private.yaml",
                            "allowed_readers": [],
                        }
                    ],
                    "direct_io": {"reads": [], "writes": [], "network": []},
                },
                "second-owner": {
                    "owns_filesystem": [
                        {
                            "match": "regex",
                            "path": "\\$repo/data/.*\\.yaml",
                            "allowed_readers": [],
                        }
                    ],
                    "direct_io": {"reads": [], "writes": [], "network": []},
                },
            }
        }
    }

    errors = _mod._validate_filesystem_ownership({blueprint_path: blueprint})

    assert errors == [
        "/repo/skills/owner-skill/blueprint.yaml: owner-skill.machine.second-owner "
        "owns_filesystem overlaps with owner-skill.machine.owner; filesystem "
        "ownership must have one writer authority"
    ]


def test_machine_interface_dependency_objects_pass_schema() -> None:
    schema = _mod._load_schema()
    assert schema is not None
    blueprint = {
        "category": "workflow-general-assistant",
        **_taxonomy(),
        "interfaces": {
            "machine": {
                "scan": {
                    "version": 1,
                    "runtime": {
                        "kind": "python_machine_interface",
                        "entrypoint": "_rtx/_handoff_scan.py:Interface",
                    },
                    "dependencies": [
                        {
                            "kind": "python",
                            "name": "PyYAML",
                            "reason": "Reads YAML files.",
                        },
                        {
                            "kind": "binary",
                            "name": "curl",
                            "reason": "Fetches remote JSON.",
                        },
                    ],
                    "directly_reads": [],
                    "directly_executes": ["_rtx/_handoff_scan.py"],
                    "directly_writes": [],
                    **_empty_direct_io(),
                    **_empty_ownership(),
                }
            },
            "llm": _default_llm(),
        },
    }

    errors = _mod._validate_blueprint_schema(Path("blueprint.yaml"), blueprint, schema)

    assert errors == []


def test_llm_interface_uses_interfaces_pass_schema() -> None:
    schema = _mod._load_schema()
    assert schema is not None
    blueprint = {
        "category": "workflow-general-assistant",
        **_taxonomy(),
        "interfaces": {
            "machine": {
                "scan": {
                    "version": 1,
                    "runtime": {
                        "kind": "python_machine_interface",
                        "entrypoint": "_rtx/_handoff_scan.py:Interface",
                    },
                    "dependencies": [],
                    "directly_reads": [],
                    "directly_executes": ["_rtx/_handoff_scan.py"],
                    "directly_writes": [],
                    **_empty_direct_io(),
                    **_empty_ownership(),
                }
            },
            "llm": {
                "default": {
                    "version": 1,
                    "description": "Primary LLM-facing skill instructions.",
                    "binding": {"kind": "skill_file", "path": "SKILL.md"},
                    "uses_interfaces": [
                        {"interface": "my-skill.machine.scan", "version": 1}
                    ],
                    "directly_reads": ["SKILL.md"],
                    "directly_executes": [],
                    "directly_writes": [],
                    **_empty_direct_io(),
                    **_empty_ownership(),
                }
            },
        },
    }

    errors = _mod._validate_blueprint_schema(Path("blueprint.yaml"), blueprint, schema)

    assert errors == []


def test_machine_uses_interfaces_rejects_llm_targets_by_schema() -> None:
    schema = _mod._load_schema()
    assert schema is not None
    blueprint = {
        "category": "workflow-general-assistant",
        **_taxonomy(),
        "interfaces": {
            "machine": {
                "scan": {
                    "version": 1,
                    "runtime": {
                        "kind": "python_machine_interface",
                        "entrypoint": "_rtx/_handoff_scan.py:Interface",
                    },
                    "dependencies": [],
                    "uses_interfaces": [
                        {"interface": "my-skill.llm.default", "version": 1}
                    ],
                    "directly_reads": ["SKILL.md"],
                    "directly_executes": [],
                    "directly_writes": [],
                    **_empty_direct_io(),
                    **_empty_ownership(),
                }
            },
            "llm": _default_llm(),
        },
    }

    errors = _mod._validate_blueprint_schema(Path("blueprint.yaml"), blueprint, schema)

    assert any("uses_interfaces.0.interface" in error and "does not match" in error for error in errors)


def test_interface_uses_allow_same_skill_llm_to_machine_and_cross_skill_llm() -> None:
    alpha_path = Path("/repo/skills/alpha-skill/blueprint.yaml")
    beta_path = Path("/repo/skills/beta-skill/blueprint.yaml")
    alpha = {
        "interfaces": {
            "machine": {
                "helper": {"version": 2},
            },
            "llm": {
                "default": {
                    "version": 1,
                    "uses_interfaces": [
                        {"interface": "alpha-skill.machine.helper", "version": 2},
                        {"interface": "beta-skill.llm.default", "version": 3},
                    ],
                }
            },
        }
    }
    beta = {
        "interfaces": {
            "llm": {
                "default": {"version": 3},
            }
        }
    }

    errors = _mod._validate_interface_uses({alpha_path: alpha, beta_path: beta})

    assert errors == []


def test_interface_uses_reject_cross_skill_llm_to_machine() -> None:
    alpha_path = Path("/repo/skills/alpha-skill/blueprint.yaml")
    beta_path = Path("/repo/skills/beta-skill/blueprint.yaml")
    alpha = {
        "interfaces": {
            "llm": {
                "default": {
                    "version": 1,
                    "uses_interfaces": [
                        {"interface": "beta-skill.machine.helper", "version": 1},
                    ],
                }
            }
        }
    }
    beta = {
        "interfaces": {
            "machine": {
                "helper": {"version": 1},
            }
        }
    }

    errors = _mod._validate_interface_uses({alpha_path: alpha, beta_path: beta})

    assert errors == [
        "/repo/skills/alpha-skill/blueprint.yaml: alpha-skill.llm.default "
        "uses_interfaces.0.interface targets beta-skill.machine.helper; LLM "
        "interfaces may only use same-skill machine interfaces or LLM interfaces"
    ]


def test_interface_uses_reject_unknown_and_stale_versions() -> None:
    alpha_path = Path("/repo/skills/alpha-skill/blueprint.yaml")
    beta_path = Path("/repo/skills/beta-skill/blueprint.yaml")
    alpha = {
        "interfaces": {
            "machine": {
                "run": {
                    "version": 1,
                    "uses_interfaces": [
                        {"interface": "beta-skill.machine.helper", "version": 1},
                        {"interface": "missing-skill.machine.nope", "version": 1},
                    ],
                }
            }
        }
    }
    beta = {
        "interfaces": {
            "machine": {
                "helper": {"version": 2},
            }
        }
    }

    errors = _mod._validate_interface_uses({alpha_path: alpha, beta_path: beta})

    assert errors == [
        "/repo/skills/alpha-skill/blueprint.yaml: alpha-skill.machine.run "
        "uses_interfaces.0 pins beta-skill.machine.helper version 1, but target "
        "version is 2",
        "/repo/skills/alpha-skill/blueprint.yaml: alpha-skill.machine.run "
        "uses_interfaces.1.interface targets unknown interface "
        "'missing-skill.machine.nope'",
    ]


def test_llm_default_is_required_by_schema() -> None:
    schema = _mod._load_schema()
    assert schema is not None
    blueprint = {
        "category": "workflow-general-assistant",
        **_taxonomy(),
        "interfaces": {"machine": {}, "llm": {}},
    }

    errors = _mod._validate_blueprint_schema(Path("blueprint.yaml"), blueprint, schema)

    assert any("interfaces.llm" in error and "'default' is a required property" in error for error in errors)


def test_direct_effect_roots_are_required_by_schema() -> None:
    schema = _mod._load_schema()
    assert schema is not None
    blueprint = {
        "category": "workflow-general-assistant",
        **_taxonomy(),
        "interfaces": {
            "machine": {
                "scan": {
                    "runtime": {
                        "kind": "python_machine_interface",
                        "entrypoint": "_rtx/_handoff_scan.py:Interface",
                    },
                    "dependencies": [],
                }
            },
            "llm": _default_llm(),
        },
    }

    errors = _mod._validate_blueprint_schema(Path("blueprint.yaml"), blueprint, schema)

    assert any("directly_reads" in error and "required" in error for error in errors)
    assert any("directly_executes" in error and "required" in error for error in errors)
    assert any("directly_writes" in error and "required" in error for error in errors)


def test_direct_effect_roots_reject_parent_traversal_by_schema() -> None:
    schema = _mod._load_schema()
    assert schema is not None
    blueprint = {
        "category": "workflow-general-assistant",
        **_taxonomy(),
        "interfaces": {
            "machine": {
                "scan": {
                    "runtime": {
                        "kind": "python_machine_interface",
                        "entrypoint": "_rtx/_handoff_scan.py:Interface",
                    },
                    "dependencies": [],
                    "directly_reads": ["../secret.txt"],
                    "directly_executes": ["_rtx/_handoff_scan.py"],
                    "directly_writes": [],
                    **_empty_direct_io(),
                    **_empty_ownership(),
                }
            },
            "llm": _default_llm(),
        },
    }

    errors = _mod._validate_blueprint_schema(Path("blueprint.yaml"), blueprint, schema)

    assert any("directly_reads.0" in error and "does not match" in error for error in errors)


def test_python_module_runtime_is_rejected_by_schema() -> None:
    schema = _mod._load_schema()
    assert schema is not None
    blueprint = {
        "category": "workflow-general-assistant",
        **_taxonomy(),
        "interfaces": {
            "machine": {
                "scan": {
                    "runtime": {"kind": "python_module", "module": "_rtx._handoff_scan"},
                    "dependencies": [],
                }
            }
        },
    }

    errors = _mod._validate_blueprint_schema(Path("blueprint.yaml"), blueprint, schema)

    assert any("python_machine_interface" in error for error in errors)


def test_command_runtime_is_rejected_by_schema() -> None:
    schema = _mod._load_schema()
    assert schema is not None
    blueprint = {
        "category": "workflow-general-assistant",
        **_taxonomy(),
        "interfaces": {
            "machine": {
                "scan": {
                    "runtime": {"kind": "command", "argv": ["python3", "_rtx/_tool.py"]},
                    "dependencies": [],
                }
            }
        },
    }

    errors = _mod._validate_blueprint_schema(Path("blueprint.yaml"), blueprint, schema)

    assert any("python_machine_interface" in error for error in errors)


def test_route_smoke_supported_flag_is_rejected_by_schema() -> None:
    schema = _mod._load_schema()
    assert schema is not None
    blueprint = {
        "category": "workflow-general-assistant",
        **_taxonomy(),
        "interfaces": {
            "machine": {
                "scan": {
                    "runtime": {
                        "kind": "python_machine_interface",
                        "entrypoint": "_rtx/scan.py:Scan",
                    },
                    "route_smoke": {"argv": [], "supported": True},
                    "dependencies": [],
                }
            }
        },
    }

    errors = _mod._validate_blueprint_schema(Path("blueprint.yaml"), blueprint, schema)

    assert any("route_smoke" in error and "Additional properties" in error for error in errors)


def test_missing_jsonschema_is_reported_as_validator_error(monkeypatch) -> None:
    schema = _mod._load_schema()
    assert schema is not None
    monkeypatch.setattr(_mod, "jsonschema", None)

    errors = _mod._validate_blueprint_schema(Path("blueprint.yaml"), {}, schema)

    assert errors == [
        "blueprint.yaml: cannot validate blueprint schema because required "
        "Python package `jsonschema` is not installed"
    ]
