from __future__ import annotations

import sys
from pathlib import Path

import yaml

from .. import _gcal_client as gcal


REPO_SRC = Path(__file__).resolve().parents[4] / "src"
if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))

from officina.blueprints.process_binding import select_authored_argv_pattern


RUNTIME_ROOT = Path(__file__).resolve().parents[1]
CALENDAR_BLUEPRINT = RUNTIME_ROOT / "blueprints" / "rtx-gcal-client.yaml"
EXPECTED_SHAPES = {
    "token": "token",
    "calendars": "calendars",
    "create-calendar": (
        "create-calendar --summary TEXT [--description TEXT] [--color-id ID] "
        "[--timezone TZ]"
    ),
    "agenda": (
        "agenda [--calendar ID] [--all-calendars] [--from ISO] [--to ISO] "
        "[--days N]"
    ),
    "search": (
        "search QUERY [--calendar ID] [--all-calendars] [--from ISO] [--to ISO] "
        "[--days N]"
    ),
    "get": "get --event-id ID [--calendar ID]",
    "create": (
        "create --summary TEXT --start ISO --end ISO [--calendar ID] "
        "[--description TEXT] [--location TEXT] [--timezone TZ] [--all-day]"
    ),
    "update": (
        "update --event-id ID [--calendar ID] [--summary TEXT] "
        "[--description TEXT] [--location TEXT] [--start ISO] [--end ISO] "
        "[--timezone TZ]"
    ),
    "delete": "delete --event-id ID [--calendar ID]",
    "move": "move --event-id ID --to CALENDAR_ID [--from CALENDAR_ID]",
}


def calendar_interface() -> dict[str, object]:
    blueprint = yaml.safe_load(CALENDAR_BLUEPRINT.read_text(encoding="utf-8"))
    return blueprint["interfaces"][
        "online-calendar._rtx.source.rtx-gcal-client.interface.scripts-gcal"
    ]


def test_canonical_process_metadata_describes_every_supported_mode() -> None:
    interface = calendar_interface()
    binding = interface["process_binding"]
    patterns = {pattern["name"]: pattern for pattern in binding["patterns"]}

    assert interface["usage"] == "<mode shown below>"
    assert patterns.keys() == {
        "token-or-calendars",
        "create-calendar",
        "agenda",
        "search",
        "get",
        "create",
        "update",
        "delete",
        "move",
    }
    combined_notes = patterns["token-or-calendars"]["notes"]
    assert f"`{EXPECTED_SHAPES['token']}`" in combined_notes
    assert f"`{EXPECTED_SHAPES['calendars']}`" in combined_notes
    for name, shape in EXPECTED_SHAPES.items():
        if name not in {"token", "calendars"}:
            assert f"`{shape}`" in patterns[name]["notes"]


def test_documented_modes_pass_binding_and_runtime_parsers() -> None:
    patterns = calendar_interface()["process_binding"]["patterns"]
    minimal_argv = {
        "token": ["token"],
        "calendars": ["calendars"],
        "create-calendar": ["create-calendar", "--summary", "Example"],
        "agenda": ["agenda"],
        "search": ["search", "query"],
        "get": ["get", "--event-id", "event-1"],
        "create": [
            "create",
            "--summary",
            "Example",
            "--start",
            "2026-08-05T09:00:00-04:00",
            "--end",
            "2026-08-05T10:00:00-04:00",
        ],
        "update": ["update", "--event-id", "event-1", "--summary", "New"],
        "delete": ["delete", "--event-id", "event-1"],
        "move": ["move", "--event-id", "event-1", "--to", "calendar-2"],
    }
    parser = gcal.build_parser()

    assert minimal_argv.keys() == EXPECTED_SHAPES.keys()
    for name, argv in minimal_argv.items():
        _, pattern_name = select_authored_argv_pattern(
            patterns,
            argv,
            stdin_requested=False,
        )
        expected_pattern = (
            "token-or-calendars" if name in {"token", "calendars"} else name
        )
        assert pattern_name == expected_pattern
        parsed = parser.parse_args(argv)
        assert parsed.cmd == name
    assert parser.parse_args(minimal_argv["update"]).summary == "New"
