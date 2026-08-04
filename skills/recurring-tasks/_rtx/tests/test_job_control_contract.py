from pathlib import Path

import pytest
import yaml


SOURCE_BLUEPRINT = (
    Path(__file__).resolve().parents[1] / "blueprints" / "rtx-job-control.yaml"
)
INTERFACE_PREFIX = "recurring-tasks-rtx.source.rtx-job-control.interface."


@pytest.mark.parametrize("operation", ["disable", "enable"])
def test_job_edit_usage_includes_declared_optional_arguments(operation: str) -> None:
    source = yaml.safe_load(SOURCE_BLUEPRINT.read_text(encoding="utf-8"))
    interface = source["interfaces"][f"{INTERFACE_PREFIX}scripts-{operation}"]
    arguments = interface["contract"]["arguments"]

    assert arguments["jobs-file"]["required"] is False
    assert arguments["no-sync"]["required"] is False
    assert interface["usage"] == "<name> [--jobs-file FILE] [--no-sync]"
