"""Lock the exact vendored browser assets and their upstream license texts."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]

EXPECTED_SHA256 = {
    "src/officina/visualization/html_renderer/vendor/elk.bundled.js":
        "48d338d5aeddd9503ccf1d12661c11b5d7d43c6afc5f66c7ddb2ea4170c0f6bf",
    "src/officina/visualization/html_renderer/vendor/elk-worker.min.js":
        "cda1839e26f82a7ac142692ee813974f8f359987348d21d1f16af8f86ff96e80",
    "src/officina/visualization/html_renderer/vendor/mathjax-3.2.2-tex-svg-full.js":
        "a4354ff94fd868aea0cc6eaaa79a57fda0588646fc46ee3700a349ee0a11cbe6",
    "LICENSES/EPL-2.0.txt":
        "89591d4578fb1ebd91501312a3d25f021bd865a2e436641c1cf7b1bc7e3c1617",
    "LICENSES/Apache-2.0.txt":
        "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30",
}


@pytest.mark.parametrize("relative_path, expected", EXPECTED_SHA256.items())
def test_vendored_release_asset_hash(relative_path: str, expected: str) -> None:
    """Require reviewed release assets to retain their recorded bytes."""

    digest = hashlib.sha256((REPO_ROOT / relative_path).read_bytes()).hexdigest()
    assert digest == expected
