from pathlib import Path

import yaml


SOURCE_BLUEPRINT = (
    Path(__file__).resolve().parents[1] / "blueprints" / "rtx-source-fetcher.yaml"
)
INTERFACE_ID = (
    "pdf-to-markdown-rtx.source.rtx-source-fetcher.interface."
    "scripts-fetch-arxiv-source"
)


def test_optional_output_directory_is_optional_in_public_usage() -> None:
    source = yaml.safe_load(SOURCE_BLUEPRINT.read_text(encoding="utf-8"))
    interface = source["interfaces"][INTERFACE_ID]

    assert interface["contract"]["arguments"]["output-dir"]["required"] is False
    assert interface["usage"] == "<arxiv-id> [<output-dir>]"
