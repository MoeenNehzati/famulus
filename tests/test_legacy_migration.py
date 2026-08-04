"""v1-manifest to v2-manifest migration acceptance (Task 2 of the macOS
acceptance v5 rebase plan).

Skipped: as of this writing,
`skills/install-assistant-tools/_rtx/_state_record.py::MANIFEST_VERSION`
is still `1` and there is no `load_manifest` migration entry point, no
`manifest_version` attribute, and no `all_paths()` accessor on `Manifest`
-- manifest-v2 (ownership-aware entries, the prerequisite for both this
migration test and the uninstall/purge invariants skipped in
tests/test_install_lifecycle.py) has not landed. The test body below is
written against the API manifest-v2 is expected to expose, so it is ready
to un-skip and run the moment that follow-up lands, rather than needing to
be written from scratch then.
"""
from __future__ import annotations

import json

import pytest


# famulus-skip: category=capability-unavailable; reason=requires manifest-v2 (load_manifest/MANIFEST_VERSION==2), not landed yet; alternate=none -- real coverage lands with the manifest-v2 follow-up
@pytest.mark.skip(reason="requires manifest-v2 (load_manifest/MANIFEST_VERSION==2), not landed yet")
def test_v1_manifest_migrates_to_v2_losslessly(tmp_path):
    from skills.install_assistant_tools._rtx._state_record import load_manifest, MANIFEST_VERSION

    legacy_manifest = tmp_path / "install-manifest.json"
    legacy_manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "entries": [
                    {"kind": "symlink", "path": str(tmp_path / "bin" / "dispatcher"), "target": "/opt/famulus/dispatcher"},
                    {"kind": "file", "path": str(tmp_path / "config" / "install-info.toml")},
                ],
            }
        ),
        encoding="utf-8",
    )

    migrated = load_manifest(legacy_manifest)

    assert migrated.manifest_version == MANIFEST_VERSION
    # Every legacy entry must survive migration with no loss: each recorded
    # path from the v1 manifest must have a corresponding v2 record.
    migrated_paths = {str(p) for p in migrated.all_paths()}
    assert str(tmp_path / "bin" / "dispatcher") in migrated_paths
    assert str(tmp_path / "config" / "install-info.toml") in migrated_paths
