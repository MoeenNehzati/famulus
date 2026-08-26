"""Specify the multi-Voyage interface exposed to command-line LLM callers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from officina.rutter import (
    LLMStep,
    Rutter,
    RutterRegistry,
    Terminal,
    VoyageDispenser,
    VoyageResult,
    voyage_dispenser_cli,
)


def _voyages(tmp_path: Path):
    definition = Rutter(
        id="dispenser-example",
        version=1,
        start="ask",
        evolutions={
            "ask": LLMStep(
                "Return an answer.",
                response_schema={
                    "type": "object",
                    "properties": {"outcome": {"const": "answered"}},
                    "required": ["outcome"],
                    "additionalProperties": False,
                },
                next_on_outcome="done",
            ),
            "done": Terminal(result=VoyageResult("complete", {})),
        },
    )
    registry = RutterRegistry({"example": definition}, tmp_path)
    voyages = {
        voyage_id: registry.create(
            "example",
            Path(f"{voyage_id}.reckoning.json"),
            {},
        )
        for voyage_id in ("worker-1", "worker-2")
    }
    return voyages


def _dispenser(tmp_path: Path) -> VoyageDispenser:
    voyages = _voyages(tmp_path)
    return VoyageDispenser(
        modes={
            "normal": {
                "description": "Run without diagnostic hooks.",
                "arguments": {},
            }
        },
        initiate_voyages=lambda mode, *, run_prefix: None,
        get_voyage_ids=lambda run_prefix=None: tuple(voyages),
        open_voyage=voyages.__getitem__,
        release_voyage=voyages.__delitem__,
    )


def test_dispenser_rejects_listing_before_voyages_are_initialized() -> None:
    """Treating an empty provider result as a valid collection hides required setup."""

    dispenser = VoyageDispenser(
        modes={
            "normal": {
                "description": "Run without diagnostic hooks.",
                "arguments": {},
            }
        },
        initiate_voyages=lambda mode, *, run_prefix: None,
        get_voyage_ids=lambda run_prefix=None: (),
        open_voyage=lambda voyage_id: None,
        release_voyage=lambda voyage_id: None,
    )

    with pytest.raises(ValueError, match="no initialized Voyages; invoke initiate first"):
        dispenser.get_voyage_ids()


def test_dispenser_exposes_modes_before_voyages_are_initialized() -> None:
    """Requiring initialization before discovery would prevent informed selection."""

    dispenser = VoyageDispenser(
        modes={
            "normal": {
                "description": "Run without diagnostic hooks.",
                "arguments": {},
            },
            "debug": {
                "description": "Attach diagnostic hooks.",
                "arguments": {
                    "gold_path": "Path to the diagnostic gold standard."
                },
            },
        },
        initiate_voyages=lambda mode, *, run_prefix: None,
        get_voyage_ids=lambda run_prefix=None: (),
        open_voyage=lambda voyage_id: None,
        release_voyage=lambda voyage_id: None,
    )

    assert dispenser.get_modes() == {
        "normal": {
            "description": "Run without diagnostic hooks.",
            "arguments": {},
        },
        "debug": {
            "description": "Attach diagnostic hooks.",
            "arguments": {
                "gold_path": "Path to the diagnostic gold standard."
            },
        },
    }


def test_cli_lists_modes_before_voyages_are_initialized(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CLI callers need structured choices rather than parsing prose help."""

    dispenser = VoyageDispenser(
        modes={
            "debug": {
                "description": "Attach diagnostic hooks.",
                "arguments": {},
            }
        },
        initiate_voyages=lambda mode, *, run_prefix: None,
        get_voyage_ids=lambda run_prefix=None: (),
        open_voyage=lambda voyage_id: None,
        release_voyage=lambda voyage_id: None,
    )

    assert voyage_dispenser_cli(dispenser, ["modes"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "default_mode": "debug",
        "modes": {
            "debug": {
                "description": "Attach diagnostic hooks.",
                "arguments": {},
            }
        },
    }


def test_cli_initiate_omits_mode_to_select_default(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Requiring a positional mode would make the declared default unusable."""

    selected_modes: list[str] = []
    voyage_ids: list[str] = []

    def initiate(mode: str, *, run_prefix: str) -> None:
        selected_modes.append(mode)
        voyage_ids.append(f"{run_prefix}-voyage-1")

    dispenser = VoyageDispenser(
        modes={
            "normal": {
                "description": "Run without diagnostic hooks.",
                "arguments": {},
            }
        },
        initiate_voyages=initiate,
        get_voyage_ids=lambda run_prefix=None: tuple(voyage_ids),
        open_voyage=lambda voyage_id: None,
        release_voyage=lambda voyage_id: None,
    )

    assert voyage_dispenser_cli(dispenser, ["initiate"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "voyage_ids": ["normal-voyage-1"]
    }
    assert selected_modes == ["normal"]


def test_cli_exposes_and_requires_selected_mode_arguments(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Debug initialization without its gold authority would create invalid Voyages."""

    initiated: list[tuple[str, str]] = []
    voyage_ids: list[str] = []

    def initiate(
        mode: str,
        *,
        run_prefix: str,
        inventory_gold_standard: str,
    ) -> None:
        initiated.append((mode, inventory_gold_standard))
        voyage_ids.append(f"{run_prefix}-voyage-1")

    dispenser = VoyageDispenser(
        modes={
            "default": {
                "description": "Run without hooks.",
                "arguments": {},
            },
            "debug": {
                "description": "Attach inventory diagnosis hooks.",
                "arguments": {
                    "inventory_gold_standard": (
                        "Path to the inventory gold-standard JSON file."
                    )
                },
            },
        },
        initiate_voyages=initiate,
        get_voyage_ids=lambda run_prefix=None: tuple(voyage_ids),
        open_voyage=lambda voyage_id: None,
        release_voyage=lambda voyage_id: None,
    )

    assert voyage_dispenser_cli(dispenser, ["modes"]) == 0
    discovered = json.loads(capsys.readouterr().out)
    assert discovered["modes"]["debug"]["arguments"] == {
        "inventory_gold_standard": (
            "Path to the inventory gold-standard JSON file."
        )
    }

    assert voyage_dispenser_cli(dispenser, ["initiate", "debug"]) == 2
    missing = json.loads(capsys.readouterr().out)
    assert missing["error"]["code"] == "usage-error"
    assert "--inventory-gold-standard" in missing["error"]["message"]

    assert voyage_dispenser_cli(
        dispenser,
        [
            "initiate",
            "debug",
            "--inventory-gold-standard",
            "/tmp/inventory-gold.json",
        ],
    ) == 0
    assert json.loads(capsys.readouterr().out) == {
        "voyage_ids": ["debug-voyage-1"]
    }
    assert initiated == [("debug", "/tmp/inventory-gold.json")]


def test_dispenser_initiates_one_declared_mode_and_returns_discovered_ids() -> None:
    """Dropping the selected mode would construct the wrong Rutter variants."""

    selected_modes: list[str] = []
    voyage_ids: list[str] = []

    def initiate(mode: str, *, run_prefix: str) -> None:
        selected_modes.append(mode)
        voyage_ids.extend(
            (f"{run_prefix}-voyage-1", f"{run_prefix}-voyage-2")
        )

    dispenser = VoyageDispenser(
        modes={
            "normal": {
                "description": "Run without diagnostic hooks.",
                "arguments": {},
            },
            "debug": {
                "description": "Attach diagnostic hooks.",
                "arguments": {},
            },
        },
        initiate_voyages=initiate,
        get_voyage_ids=lambda run_prefix=None: tuple(voyage_ids),
        open_voyage=lambda voyage_id: None,
        release_voyage=lambda voyage_id: None,
    )

    assert dispenser.initiate_voyages("debug") == (
        "debug-voyage-1",
        "debug-voyage-2",
    )
    assert selected_modes == ["debug"]


def test_dispenser_uses_first_declared_mode_when_initiate_omits_mode() -> None:
    """Choosing by sorting or a hidden constant would ignore author intent."""

    selected_modes: list[str] = []
    voyage_ids: list[str] = []

    def initiate(mode: str, *, run_prefix: str) -> None:
        selected_modes.append(mode)
        voyage_ids.append(f"{run_prefix}-voyage-1")

    dispenser = VoyageDispenser(
        modes={
            "normal": {
                "description": "Run without diagnostic hooks.",
                "arguments": {},
            },
            "debug": {
                "description": "Attach diagnostic hooks.",
                "arguments": {},
            },
        },
        initiate_voyages=initiate,
        get_voyage_ids=lambda run_prefix=None: tuple(voyage_ids),
        open_voyage=lambda voyage_id: None,
        release_voyage=lambda voyage_id: None,
    )

    assert dispenser.initiate() == ("normal-voyage-1",)
    assert selected_modes == ["normal"]


def test_dispenser_rejects_unknown_mode_without_initializing() -> None:
    """Accepting an undeclared label would leave hook selection undefined."""

    initiated: list[str] = []
    dispenser = VoyageDispenser(
        modes={
            "normal": {
                "description": "Run without diagnostic hooks.",
                "arguments": {},
            }
        },
        initiate_voyages=lambda mode, *, run_prefix: initiated.append(mode),
        get_voyage_ids=lambda run_prefix=None: (),
        open_voyage=lambda voyage_id: None,
        release_voyage=lambda voyage_id: None,
    )

    with pytest.raises(ValueError, match="unknown Voyage mode 'debug'"):
        dispenser.initiate("debug")
    assert initiated == []


def test_dispenser_rejects_reinitializing_existing_voyages() -> None:
    """Reinitialization could overwrite the mode bound into durable authority."""

    initiated: list[str] = []
    dispenser = VoyageDispenser(
        modes={
            "normal": {
                "description": "Run without diagnostic hooks.",
                "arguments": {},
            }
        },
        initiate_voyages=lambda mode, *, run_prefix: initiated.append(mode),
        get_voyage_ids=lambda run_prefix=None: (
            ("normal-voyage-1",)
            if run_prefix in {None, "normal"}
            else ()
        ),
        open_voyage=lambda voyage_id: None,
        release_voyage=lambda voyage_id: None,
    )

    with pytest.raises(ValueError, match="run prefix 'normal' is already initialized"):
        dispenser.initiate("normal")
    assert initiated == []


def test_dispenser_defaults_run_prefix_to_mode_and_isolates_initialization() -> None:
    """A prior mode's Voyages must not block a differently prefixed run."""

    voyages: dict[str, list[str]] = {}

    def initiate(mode: str, *, run_prefix: str) -> None:
        voyages.setdefault(run_prefix, []).append(f"{run_prefix}-voyage-001")

    def get_voyage_ids(run_prefix: str | None = None) -> tuple[str, ...]:
        if run_prefix is not None:
            return tuple(voyages.get(run_prefix, ()))
        return tuple(
            voyage_id
            for prefix_voyages in voyages.values()
            for voyage_id in prefix_voyages
        )

    dispenser = VoyageDispenser(
        modes={
            "normal": {"description": "Run without hooks.", "arguments": {}},
            "debug": {"description": "Attach hooks.", "arguments": {}},
        },
        initiate_voyages=initiate,
        get_voyage_ids=get_voyage_ids,
        open_voyage=lambda voyage_id: None,
        release_voyage=lambda voyage_id: None,
    )

    assert dispenser.initiate_voyages("normal") == ("normal-voyage-001",)
    assert dispenser.initiate_voyages("debug") == ("debug-voyage-001",)
    assert dispenser.get_voyage_ids() == (
        "normal-voyage-001",
        "debug-voyage-001",
    )
    assert dispenser.get_voyage_ids("debug") == ("debug-voyage-001",)

    with pytest.raises(ValueError, match="run prefix 'normal' is already initialized"):
        dispenser.initiate_voyages("normal")


def test_cli_accepts_custom_run_prefix_and_filters_list(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Ignoring list's prefix would mix independent controller assignments."""

    voyages = {
        "baseline": ["baseline-voyage-001"],
        "diagnostic": ["diagnostic-voyage-001", "diagnostic-voyage-002"],
    }

    def get_voyage_ids(run_prefix: str | None = None) -> tuple[str, ...]:
        if run_prefix is not None:
            return tuple(voyages.get(run_prefix, ()))
        return tuple(
            voyage_id
            for prefix_voyages in voyages.values()
            for voyage_id in prefix_voyages
        )

    dispenser = VoyageDispenser(
        modes={"normal": {"description": "Run normally.", "arguments": {}}},
        initiate_voyages=lambda mode, *, run_prefix: None,
        get_voyage_ids=get_voyage_ids,
        open_voyage=lambda voyage_id: None,
        release_voyage=lambda voyage_id: None,
    )

    assert voyage_dispenser_cli(dispenser, ["list"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "voyage_ids": [
            "baseline-voyage-001",
            "diagnostic-voyage-001",
            "diagnostic-voyage-002",
        ]
    }

    assert voyage_dispenser_cli(
        dispenser, ["list", "--run-prefix", "diagnostic"]
    ) == 0
    assert json.loads(capsys.readouterr().out) == {
        "run_prefix": "diagnostic",
        "voyage_ids": ["diagnostic-voyage-001", "diagnostic-voyage-002"],
    }


@pytest.mark.parametrize(
    "run_prefix",
    ["", ".", "..", "../debug", "a/b", "a\\b", "bad name", "a:b"],
)
def test_dispenser_rejects_unsafe_run_prefixes(run_prefix: str) -> None:
    """Allowing path syntax in a prefix could escape its owned run directory."""

    initiated: list[str] = []
    dispenser = VoyageDispenser(
        modes={"normal": {"description": "Run normally.", "arguments": {}}},
        initiate_voyages=lambda mode, *, run_prefix: initiated.append(run_prefix),
        get_voyage_ids=lambda run_prefix=None: (),
        open_voyage=lambda voyage_id: None,
        release_voyage=lambda voyage_id: None,
    )

    with pytest.raises(ValueError, match="invalid Voyage run prefix"):
        dispenser.initiate_voyages("normal", run_prefix=run_prefix)
    assert initiated == []


def test_cli_initiates_selected_mode_and_reports_ids(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A CLI-only mode label must reach the durable initialization callback."""

    voyage_ids: list[str] = []

    def initiate(mode: str, *, run_prefix: str) -> None:
        assert mode == "debug"
        voyage_ids.append(f"{run_prefix}-voyage-1")

    dispenser = VoyageDispenser(
        modes={
            "normal": {
                "description": "Run without diagnostic hooks.",
                "arguments": {},
            },
            "debug": {
                "description": "Attach diagnostic hooks.",
                "arguments": {},
            },
        },
        initiate_voyages=initiate,
        get_voyage_ids=lambda run_prefix=None: tuple(voyage_ids),
        open_voyage=lambda voyage_id: None,
        release_voyage=lambda voyage_id: None,
    )

    assert voyage_dispenser_cli(dispenser, ["initiate", "debug"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "voyage_ids": ["debug-voyage-1"]
    }


def test_dispenser_enumerates_every_authorized_voyage(tmp_path: Path) -> None:
    """Returning only one provider result would hide the second worker Voyage."""

    dispenser = _dispenser(tmp_path)

    assert dispenser.get_voyage_ids() == ("worker-1", "worker-2")


def test_dispenser_routes_operations_by_voyage_id(tmp_path: Path) -> None:
    """Ignoring voyage_id would advance the wrong worker's Reckoning."""

    dispenser = _dispenser(tmp_path)
    first = dispenser.get_status("worker-1")
    second = dispenser.get_status("worker-2")

    response = {"outcome": "answered"}
    assert dispenser.validate(
        "worker-2",
        response,
        responding_to=second.instruction.evolution_entry_id,
    ).valid
    dispenser.advance(
        "worker-2",
        response,
        responding_to=second.instruction.evolution_entry_id,
    )

    assert dispenser.get_status("worker-1") == first
    assert dispenser.get_status("worker-2").current_evolution.condition == "terminal"


def test_cli_requires_force_to_release_a_nonterminal_voyage(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A ready Voyage must survive release unless deletion is explicitly forced."""

    voyages = _voyages(tmp_path)
    released: list[str] = []

    def release(voyage_id: str) -> None:
        released.append(voyage_id)
        del voyages[voyage_id]

    dispenser = VoyageDispenser(
        modes={
            "normal": {
                "description": "Run without diagnostic hooks.",
                "arguments": {},
            }
        },
        initiate_voyages=lambda mode, *, run_prefix: None,
        get_voyage_ids=lambda run_prefix=None: tuple(voyages),
        open_voyage=voyages.__getitem__,
        release_voyage=release,
    )

    assert voyage_dispenser_cli(dispenser, ["release", "worker-1"]) == 5
    rejected = json.loads(capsys.readouterr().out)
    assert rejected["error"]["code"] == "not-terminal"
    assert released == []

    assert (
        voyage_dispenser_cli(dispenser, ["release", "worker-1", "--force"])
        == 0
    )
    assert json.loads(capsys.readouterr().out) == {
        "forced": True,
        "released": True,
        "voyage_id": "worker-1",
    }
    assert released == ["worker-1"]

    status = dispenser.get_status("worker-2")
    dispenser.advance(
        "worker-2",
        {"outcome": "answered"},
        responding_to=status.instruction.evolution_entry_id,
    )

    assert voyage_dispenser_cli(dispenser, ["release", "worker-2"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "released": True,
        "voyage_id": "worker-2",
    }
    assert released == ["worker-1", "worker-2"]
    assert voyages == {}


def test_forced_release_does_not_open_unreadable_voyage_state() -> None:
    """Forced abandonment must work when obsolete Voyage state cannot be loaded."""

    voyage_ids = ["obsolete-voyage-1"]

    def release(voyage_id: str) -> None:
        voyage_ids.remove(voyage_id)

    def fail_if_opened(voyage_id: str):
        raise RuntimeError("obsolete state")

    dispenser = VoyageDispenser(
        modes={
            "normal": {
                "description": "Run without diagnostic hooks.",
                "arguments": {},
            }
        },
        initiate_voyages=lambda mode, *, run_prefix: None,
        get_voyage_ids=lambda run_prefix=None: tuple(voyage_ids),
        open_voyage=fail_if_opened,
        release_voyage=release,
    )

    dispenser.release("obsolete-voyage-1", force=True)

    assert voyage_ids == []


def test_dispenser_rejects_an_id_outside_its_enumeration(tmp_path: Path) -> None:
    """Passing arbitrary IDs to the opener would bypass dispenser authorization."""

    dispenser = _dispenser(tmp_path)

    with pytest.raises(ValueError, match="unknown Voyage ID 'worker-3'"):
        dispenser.get_status("worker-3")


def test_cli_lists_voyages_and_projects_one_status(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A CLI that leaks Python values cannot be consumed through an LLM process."""

    dispenser = _dispenser(tmp_path)

    assert voyage_dispenser_cli(dispenser, ["list"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed == {"voyage_ids": ["worker-1", "worker-2"]}

    assert voyage_dispenser_cli(dispenser, ["status", "worker-2"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["voyage_id"] == "worker-2"
    assert shown["evolution"]["condition"] == "ready"
    assert shown["instruction"]["kind"] == "message"
    assert shown["instruction"]["instructions"]["text"] == "Return an answer."


def test_cli_help_explains_one_agent_per_voyage(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Generic argparse help would omit the required multi-agent ownership model."""

    dispenser = _dispenser(tmp_path)
    exit_code = voyage_dispenser_cli(dispenser, ["help"])
    help_text = json.loads(capsys.readouterr().out)["help"]

    assert exit_code == 0
    assert "one agent per Voyage" in help_text
    assert "modes" in help_text
    assert "initiate [mode]" in help_text
    assert "--run-prefix" in help_text
    assert "selected mode as the prefix" in help_text
    assert "omit the mode to use the default" in help_text
    assert "not-initialized" in help_text
    assert "list" in help_text
    assert "assigned voyage_id" in help_text
    assert "status" in help_text
    assert "validate" in help_text
    assert "advance" in help_text
    assert "release --force" in help_text
    assert "explicit reason" in help_text


def test_cli_validates_before_advancing_the_selected_voyage(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Advancing malformed JSON would mutate authority before public validation."""

    dispenser = _dispenser(tmp_path)
    entry_id = dispenser.get_status(
        "worker-2"
    ).instruction.evolution_entry_id
    response_file = tmp_path / "response.json"
    response_file.write_text('{"outcome":"wrong"}', encoding="utf-8")

    exit_code = voyage_dispenser_cli(
        dispenser,
        [
            "advance",
            "worker-2",
            "--response-file",
            str(response_file),
            "--responding-to",
            entry_id,
        ],
    )
    streams = capsys.readouterr()
    payload = json.loads(streams.out)

    assert exit_code == 4
    assert json.loads(streams.err) == payload
    assert payload["error"]["code"] == "invalid-response"
    assert dispenser.get_status("worker-2").current_evolution.condition == "ready"
